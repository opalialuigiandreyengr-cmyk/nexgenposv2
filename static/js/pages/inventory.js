/* =============================================================================
   js/pages/inventory.js — Daily Inventory & Consumption Tracker
   SPA module: recipe BOM management + daily consumption report
   ============================================================================= */
'use strict';

/* ── State ─────────────────────────────────────────────────────────── */
let allProducts = [];           // Available POS products
let allRecipes  = [];           // Loaded recipe mappings
let reportData  = null;         // Latest generated report data
let activeTab   = 'report';
let rootEl      = null;

/* ── DOM Helper scoped to rootEl ───────────────────────────────────── */
const $ = (sel) => (rootEl || document).querySelector(sel);
const $$ = (sel) => [...(rootEl || document).querySelectorAll(sel)];

/* ── Lifecycle: Mount ──────────────────────────────────────────────── */
export async function mount(container) {
  rootEl = container || document;

  // Load products list embedded in template script tag
  try {
    const dataTag = document.getElementById('invProductsData');
    allProducts = JSON.parse(dataTag?.textContent || '[]');
  } catch { allProducts = []; }

  // Set today's date as default
  const dateInput = $('#reportDate');
  if (dateInput) dateInput.value = todayLocal();

  bindEvents();
  await loadRecipes();
}

/* ── Lifecycle: Destroy ────────────────────────────────────────────── */
export function destroy() {
  allRecipes = [];
  reportData = null;
  rootEl = null;
}

/* ── Events ────────────────────────────────────────────────────────── */
function bindEvents() {
  // Tabs
  $$('.inv-tab').forEach(t => t.addEventListener('click', () => switchTab(t.dataset.tab)));

  // Report
  $('#btnGenerateReport')?.addEventListener('click', generateReport);
  $('#btnExportReport')?.addEventListener('click', exportReport);

  // Recipes
  $('#btnAddRecipe')?.addEventListener('click', () => showAddRecipeModal(null));
  $('#btnImportRecipes')?.addEventListener('click', showImportModal);
  $('#recipeSearch')?.addEventListener('input', debounce(filterRecipes, 250));
  $('#recipeCategoryFilter')?.addEventListener('change', filterRecipes);
}

/* ── Tab Switch ────────────────────────────────────────────────────── */
function switchTab(tab) {
  activeTab = tab;
  $$('.inv-tab').forEach(t => {
    const isActive = t.dataset.tab === tab;
    t.classList.toggle('active', isActive);
    t.setAttribute('aria-selected', isActive ? 'true' : 'false');
  });
  $$('.inv-pane').forEach(p => p.classList.toggle('active', p.id === `pane-${tab}`));
}

/* ======================================================================
   RECIPES (BOM) MANAGEMENT
   ====================================================================== */
async function loadRecipes() {
  try {
    const resp = await fetch('/api/inventory/recipes');
    if (!resp.ok) throw new Error('Load failed');
    allRecipes = await resp.json();
    renderRecipeTable(allRecipes);
    populateCategoryFilter();
  } catch (err) {
    console.error('Load recipes:', err);
    showToast('Failed to load recipe mappings', 'error');
  }
}

function renderRecipeTable(recipes) {
  const tbody = $('#recipeTableBody');
  const empty = $('#recipeEmpty');
  const wrap = $('.inv-table-wrap', $('#pane-recipes'));
  if (!tbody) return;

  if (!recipes.length) {
    tbody.innerHTML = '';
    if (wrap) wrap.style.display = 'none';
    if (empty) empty.style.display = 'flex';
    const cnt = $('#recipeCount');
    if (cnt) cnt.textContent = '0 mappings';
    return;
  }
  if (wrap) wrap.style.display = '';
  if (empty) empty.style.display = 'none';
  const cnt = $('#recipeCount');
  if (cnt) cnt.textContent = `${recipes.length} mapping${recipes.length !== 1 ? 's' : ''}`;

  tbody.innerHTML = recipes.map(r => `
    <tr data-id="${r.id}">
      <td><strong>${esc(r.product_name)}</strong></td>
      <td>${esc(r.raw_material_name)}</td>
      <td>${r.category ? `<span class="inv-cat-badge">${esc(r.category)}</span>` : '-'}</td>
      <td class="inv-qty-cell">${r.consumed_quantity ?? '-'}</td>
      <td>${r.uom ? `<span class="inv-uom-badge">${esc(r.uom)}</span>` : '-'}</td>
      <td style="text-align:right;">
        <button class="inv-action-btn" title="Edit" onclick="window.__inv.editRecipe(${r.id})">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
        </button>
        <button class="inv-action-btn danger" title="Delete" onclick="window.__inv.deleteRecipe(${r.id})">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
        </button>
      </td>
    </tr>
  `).join('');
}

function populateCategoryFilter() {
  const sel = $('#recipeCategoryFilter');
  if (!sel) return;
  const cats = [...new Set(allRecipes.map(r => r.category).filter(Boolean))].sort();
  sel.innerHTML = '<option value="">All Categories</option>' +
    cats.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
}

function filterRecipes() {
  const q = ($('#recipeSearch')?.value || '').trim().toLowerCase();
  const cat = $('#recipeCategoryFilter')?.value || '';
  let filtered = allRecipes;
  if (q) {
    filtered = filtered.filter(r =>
      r.product_name.toLowerCase().includes(q) ||
      r.raw_material_name.toLowerCase().includes(q)
    );
  }
  if (cat) filtered = filtered.filter(r => r.category === cat);
  renderRecipeTable(filtered);
}

/* ── Add/Edit Recipe Modal (using standard custom modal system) ───── */
function showAddRecipeModal(existingRecipe) {
  const isEdit = existingRecipe && typeof existingRecipe === 'object' && existingRecipe.id;
  const title = isEdit ? 'Edit Recipe Mapping' : 'Add Recipe Mapping';
  const overlayId = 'inv-recipe-modal';

  // Cleanup existing modal if present
  const oldModal = document.getElementById(overlayId);
  if (oldModal) oldModal.remove();

  let hostEl = document.getElementById('modal-host');
  if (!hostEl) {
    hostEl = document.createElement('div');
    hostEl.id = 'modal-host';
    document.body.appendChild(hostEl);
  } else if (hostEl.parentNode !== document.body) {
    document.body.appendChild(hostEl);
  }

  const overlay = document.createElement('div');
  overlay.className = 'lib-modal-overlay';
  overlay.id = overlayId;

  const box = document.createElement('div');
  box.className = 'lib-modal-box is-warning lib-modal-warning';
  box.style.cssText = 'max-width: 520px; width: 100%; max-height: calc(100vh - 40px); overflow-y: auto;';

  const productOptions = allProducts.map(p =>
    `<option value="${p.id}" ${isEdit && p.name === existingRecipe.product_name ? 'selected' : ''}>${esc(p.name)} (${esc(p.category || '')})</option>`
  ).join('');

  box.innerHTML = `
    <h3 class="lib-modal-title lib-modal-warning-title" style="color: var(--text-main); margin-bottom: 0.25rem;">
      <span class="lib-modal-warning-icon" style="background: color-mix(in srgb, var(--color-primary) 14%, transparent); color: var(--color-primary);">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
      </span>
      ${title}
    </h3>
    <p style="font-size: 0.82rem; color: var(--text-muted); margin: 0 0 1rem 0;">Map a POS sales product to its raw material consumption quantity.</p>

    <form id="recipeModalForm" style="display: flex; flex-direction: column; gap: 0.85rem;">
      <div>
        <label class="usr-modal-label" style="font-size: 0.78rem; font-weight: 600; color: var(--text-muted); margin-bottom: 4px; display: block;">Product (Sales Category) <span style="color: var(--color-danger, #ef4444)">*</span></label>
        <select class="inv-select" id="rmProductId" style="width:100%; height:38px;">
          <option value="">-- Select Product --</option>
          ${productOptions}
        </select>
      </div>
      <div>
        <label class="usr-modal-label" style="font-size: 0.78rem; font-weight: 600; color: var(--text-muted); margin-bottom: 4px; display: block;">Raw Material Name <span style="color: var(--color-danger, #ef4444)">*</span></label>
        <input type="text" class="inv-input" id="rmRawMatName" style="width:100%; height:38px;" placeholder="e.g. Pata prep 1ord=1pc" value="${isEdit ? esc(existingRecipe.raw_material_name) : ''}">
      </div>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
        <div>
          <label class="usr-modal-label" style="font-size: 0.78rem; font-weight: 600; color: var(--text-muted); margin-bottom: 4px; display: block;">Qty / Order</label>
          <input type="number" step="0.01" class="inv-input" id="rmQty" style="width:100%; height:38px;" placeholder="1.00" value="${isEdit ? (existingRecipe.consumed_quantity || '') : ''}">
        </div>
        <div>
          <label class="usr-modal-label" style="font-size: 0.78rem; font-weight: 600; color: var(--text-muted); margin-bottom: 4px; display: block;">UOM</label>
          <input type="text" class="inv-input" id="rmUom" style="width:100%; height:38px;" placeholder="e.g. order, kg" value="${isEdit ? esc(existingRecipe.uom || '') : ''}">
        </div>
      </div>
      <div>
        <label class="usr-modal-label" style="font-size: 0.78rem; font-weight: 600; color: var(--text-muted); margin-bottom: 4px; display: block;">Category</label>
        <input type="text" class="inv-input" id="rmCategory" style="width:100%; height:38px;" placeholder="e.g. Pork, Beef, Chicken" value="${isEdit ? esc(existingRecipe.category || '') : ''}">
      </div>

      <div style="display: flex; gap: 0.75rem; justify-content: flex-end; margin-top: 0.5rem; padding-top: 0.75rem; border-top: 1px solid var(--border-color, #2a2a3e);">
        <button type="button" class="btn lib-modal-warning-cancel" id="btnCancelRecipeModal" style="flex: 1; justify-content: center; align-items: center; height: 38px;">Cancel</button>
        <button type="submit" class="btn inv-btn-primary" id="btnSaveRecipe" style="flex: 1; justify-content: center; align-items: center; height: 38px;">${isEdit ? 'Update Mapping' : 'Add Mapping'}</button>
      </div>
    </form>
  `;

  overlay.appendChild(box);
  hostEl.appendChild(overlay);

  const close = () => {
    if (window.closeModal) window.closeModal(overlay);
    setTimeout(() => overlay.remove(), 250);
  };

  box.querySelector('#btnCancelRecipeModal')?.addEventListener('click', close);

  box.querySelector('#recipeModalForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const productId = box.querySelector('#rmProductId')?.value;
    const productName = productId ? allProducts.find(p => String(p.id) === String(productId))?.name || '' : '';
    const data = {
      product_id: parseInt(productId) || 0,
      product_name: productName,
      raw_material_name: box.querySelector('#rmRawMatName')?.value.trim(),
      raw_material_code: '',
      consumed_quantity: parseFloat(box.querySelector('#rmQty')?.value) || 0,
      uom: box.querySelector('#rmUom')?.value.trim(),
      category: box.querySelector('#rmCategory')?.value.trim(),
    };
    if (!data.product_id || !data.raw_material_name) {
      showToast('Product and Raw Material Name are required', 'error');
      return;
    }
    try {
      const url = isEdit ? `/api/inventory/recipes/${existingRecipe.id}` : '/api/inventory/recipes';
      const method = isEdit ? 'PUT' : 'POST';
      const resp = await fetch(url, {
        method,
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken()
        },
        body: JSON.stringify(data)
      });
      if (!resp.ok) { const errObj = await resp.json(); throw new Error(errObj.error || 'Save failed'); }
      showToast(isEdit ? 'Recipe updated' : 'Recipe added', 'success');
      close();
      loadRecipes();
    } catch (err) {
      showToast(err.message, 'error');
    }
  });

  if (window.openModal) window.openModal(overlay.id);
}

/* ── Edit Recipe (global hook) ─────────────────────────────────────── */
function editRecipe(id) {
  const recipe = allRecipes.find(r => r.id === id);
  if (recipe) showAddRecipeModal(recipe);
}

/* ── Delete Recipe (global hook) ───────────────────────────────────── */
async function deleteRecipe(id) {
  if (!confirm('Delete this recipe mapping?')) return;
  try {
    const resp = await fetch(`/api/inventory/recipes/${id}`, {
      method: 'DELETE',
      headers: {
        'X-CSRFToken': getCsrfToken()
      }
    });
    if (!resp.ok) throw new Error('Delete failed');
    showToast('Recipe mapping deleted', 'success');
    loadRecipes();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

/* ── Import Excel Modal (using standard custom modal system) ──────── */
function showImportModal() {
  const overlayId = 'inv-import-modal';

  const oldModal = document.getElementById(overlayId);
  if (oldModal) oldModal.remove();

  let hostEl = document.getElementById('modal-host');
  if (!hostEl) {
    hostEl = document.createElement('div');
    hostEl.id = 'modal-host';
    document.body.appendChild(hostEl);
  } else if (hostEl.parentNode !== document.body) {
    document.body.appendChild(hostEl);
  }

  const overlay = document.createElement('div');
  overlay.className = 'lib-modal-overlay';
  overlay.id = overlayId;

  const box = document.createElement('div');
  box.className = 'lib-modal-box is-warning lib-modal-warning';
  box.style.cssText = 'max-width: 580px; width: 100%; max-height: calc(100vh - 40px); overflow-y: auto;';

  box.innerHTML = `
    <h3 class="lib-modal-title lib-modal-warning-title" style="color: var(--text-main); margin-bottom: 0.25rem;">
      <span class="lib-modal-warning-icon" style="background: color-mix(in srgb, var(--color-primary) 14%, transparent); color: var(--color-primary);">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
      </span>
      Import Recipe Mappings
    </h3>
    <p style="font-size: 0.82rem; color: var(--text-muted); margin: 0 0 0.85rem 0;">Upload your raw materials Excel file (.xlsx, .xls, .csv).</p>

    <div style="font-size: 0.78rem; margin-bottom: 12px;">
      <p style="color: var(--text-muted); margin: 0 0 6px 0;">Expected Excel structure:</p>
      <div class="inv-table-wrap" style="border: 1px solid var(--border-color); border-radius: 6px; overflow: hidden;">
        <table class="inv-table" style="font-size: 0.75rem;">
          <thead>
            <tr><th>Category</th><th>Item/s</th><th>Sales Category</th><th>Measurement/Order</th><th>UOM2</th></tr>
          </thead>
          <tbody>
            <tr><td>Pork</td><td>Pata prep 1ord=1pc</td><td>Crispy Pata</td><td>1.00</td><td>order</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div style="border: 2px dashed var(--border-color); border-radius: 8px; padding: 24px; text-align: center; cursor: pointer; transition: border-color 0.2s;" id="importDropZone">
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" stroke-width="1.5" stroke-linecap="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
      <p style="font-size: 0.82rem; color: var(--text-main); margin: 8px 0 2px 0; font-weight: 600;">Click or drag & drop Excel file</p>
      <p style="font-size: 0.72rem; color: var(--text-muted); margin: 0;">Supports .xlsx, .xls, .csv</p>
      <input type="file" id="importFileInput" accept=".xlsx,.xls,.csv" style="display:none;">
    </div>

    <div id="importPreview" style="display:none; margin-top: 12px; padding: 10px 14px; background: var(--bg-elev); border-radius: 6px; border: 1px solid var(--border-color);">
      <p style="font-size: 0.82rem; font-weight: 600; color: var(--text-main); margin: 0 0 4px 0;">
        File: <span id="importFileName"></span> (<span id="importRowCount">0</span> rows)
      </p>
      <div id="importMatchInfo" style="font-size: 0.78rem;"></div>
    </div>

    <div style="display: flex; gap: 0.75rem; justify-content: flex-end; margin-top: 1rem; padding-top: 0.75rem; border-top: 1px solid var(--border-color, #2a2a3e);">
      <button type="button" class="btn lib-modal-warning-cancel" id="btnCancelImport" style="flex: 1; justify-content: center; align-items: center; height: 38px;">Cancel</button>
      <button type="button" class="btn inv-btn-primary" id="btnConfirmImport" disabled style="flex: 1; justify-content: center; align-items: center; height: 38px;">Import Recipes</button>
    </div>
  `;

  overlay.appendChild(box);
  hostEl.appendChild(overlay);

  const close = () => {
    if (window.closeModal) window.closeModal(overlay);
    setTimeout(() => overlay.remove(), 250);
  };

  box.querySelector('#btnCancelImport')?.addEventListener('click', close);

  const dropZone = box.querySelector('#importDropZone');
  const fileInput = box.querySelector('#importFileInput');
  dropZone?.addEventListener('click', () => fileInput?.click());
  dropZone?.addEventListener('dragover', e => { e.preventDefault(); dropZone.style.borderColor = 'var(--color-primary)'; });
  dropZone?.addEventListener('dragleave', () => { dropZone.style.borderColor = 'var(--border-color)'; });
  dropZone?.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.style.borderColor = 'var(--border-color)';
    if (e.dataTransfer.files.length) handleImportFile(e.dataTransfer.files[0], box, close);
  });
  fileInput?.addEventListener('change', () => {
    if (fileInput.files.length) handleImportFile(fileInput.files[0], box, close);
  });

  if (window.openModal) window.openModal(overlay.id);
}

async function handleImportFile(file, modalBox, closeModalFn) {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('preview', '1');

  try {
    const resp = await fetch('/api/inventory/import_recipes', {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCsrfToken()
      },
      body: fd
    });
    if (!resp.ok) { const e = await resp.json(); throw new Error(e.error || 'Upload failed'); }
    const data = await resp.json();

    const nameEl = modalBox.querySelector('#importFileName'); if (nameEl) nameEl.textContent = file.name;
    const rowEl = modalBox.querySelector('#importRowCount'); if (rowEl) rowEl.textContent = data.total_rows;
    const matchedUnique = data.matched_unique || (data.matched_products || 0);
    const unmatchedUnique = data.unmatched_unique || (data.unmatched_names ? data.unmatched_names.length : (data.unmatched_products || 0));

    const matchEl = modalBox.querySelector('#importMatchInfo');
    if (matchEl) {
      let html = `<span style="color:#22c55e; font-weight:600;">✓ ${matchedUnique} products matched in POS</span>`;
      if (unmatchedUnique > 0) {
        html += ` · <span style="color:var(--color-danger); font-weight:600;">✗ ${unmatchedUnique} product${unmatchedUnique === 1 ? '' : 's'} not found</span>`;
        if (data.unmatched_names && data.unmatched_names.length > 0) {
          html += `<div style="margin-top:10px; padding:10px 14px; background:rgba(239, 68, 68, 0.08); border:1px solid rgba(239, 68, 68, 0.25); border-radius:8px; font-size:12px; color:var(--color-danger, #ef4444); text-align:left;">` +
                  `<strong>Products not found in POS (${data.unmatched_names.length}):</strong>` +
                  `<ul style="margin:6px 0 0 16px; padding:0; line-height:1.5;">` +
                  data.unmatched_names.map(n => `<li><strong>${n}</strong></li>`).join('') +
                  `</ul></div>`;
        }
      }
      matchEl.innerHTML = html;
    }
    const prevEl = modalBox.querySelector('#importPreview'); if (prevEl) prevEl.style.display = 'block';

    const btn = modalBox.querySelector('#btnConfirmImport');
    if (btn) {
      btn.disabled = false;
      const newBtn = btn.cloneNode(true);
      btn.parentNode.replaceChild(newBtn, btn);
      newBtn.addEventListener('click', () => confirmImport(file, newBtn, closeModalFn));
    }
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function confirmImport(file, btnEl, closeModalFn) {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('preview', '0');

  if (btnEl) { btnEl.disabled = true; btnEl.innerHTML = 'Importing...'; }

  try {
    const resp = await fetch('/api/inventory/import_recipes', {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCsrfToken()
      },
      body: fd
    });
    if (!resp.ok) { const e = await resp.json(); throw new Error(e.error || 'Import failed'); }
    const data = await resp.json();
    showToast(`Imported ${data.imported} recipe mappings`, 'success');
    if (typeof closeModalFn === 'function') closeModalFn();
    await loadRecipes();
    switchTab('recipes');
  } catch (err) {
    showToast(err.message, 'error');
    if (btnEl) { btnEl.disabled = false; btnEl.innerHTML = 'Import Recipes'; }
  }
}

/* ======================================================================
   DAILY CONSUMPTION REPORT
   ====================================================================== */
async function generateReport() {
  const date = $('#reportDate')?.value;
  if (!date) { showToast('Please select a date', 'error'); return; }

  const btn = $('#btnGenerateReport');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-sm"></span> Generating...'; }

  try {
    const resp = await fetch(`/api/inventory/daily_report?date=${date}`);
    if (!resp.ok) { const e = await resp.json(); throw new Error(e.error || 'Failed'); }
    reportData = await resp.json();
    renderReport(reportData);
    const expBtn = $('#btnExportReport');
    if (expBtn) expBtn.disabled = false;
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg><span>Generate</span>`;
    }
  }
}

function renderReport(data) {
  // Metrics
  const metricsEl = $('#reportMetrics');
  if (metricsEl) {
    metricsEl.style.display = '';
    const mOrd = $('#metricOrders'); if (mOrd) mOrd.textContent = (data.total_orders || 0).toLocaleString();
    const mProd = $('#metricProducts'); if (mProd) mProd.textContent = (data.total_products_sold || 0).toLocaleString();
    const mRaw = $('#metricRawMats'); if (mRaw) mRaw.textContent = (data.total_raw_materials || 0).toLocaleString();
    const mUnm = $('#metricUnmapped'); if (mUnm) mUnm.textContent = (data.unmapped_count || 0).toLocaleString();
  }

  const container = $('#reportContent');
  if (!container) return;
  container.innerHTML = '';

  if (!data.groups || !data.groups.length) {
    if (!data.unmapped || !data.unmapped.length) {
      container.innerHTML = `
        <div class="inv-empty-state">
          <div class="inv-empty-icon">
            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          </div>
          <h3>No Data for This Date</h3>
          <p>No completed orders found for the selected date, or no recipe mappings are configured yet.</p>
        </div>`;
      return;
    }
  }

  // Consumption groups
  data.groups.forEach(group => {
    const groupHtml = `
      <div class="inv-report-group">
        <div class="inv-report-group-header">
          <span>${esc(group.category || 'Uncategorized')}</span>
          <span class="inv-group-badge">${group.items.length} items</span>
        </div>
        <div class="inv-table-wrap">
          <table class="inv-table">
            <thead>
              <tr>
                <th>Product Sold</th>
                <th style="text-align:right;">Qty Sold</th>
                <th>Raw Material</th>
                <th style="text-align:right;">Consumed Qty</th>
                <th>UOM</th>
              </tr>
            </thead>
            <tbody>
              ${group.items.map(it => `
                <tr>
                  <td><strong>${esc(it.product_name)}</strong></td>
                  <td class="inv-qty-cell" style="text-align:right;">${it.qty_sold}</td>
                  <td>${esc(it.raw_material_name)}</td>
                  <td class="inv-qty-cell" style="text-align:right;">${it.total_consumed.toFixed(2)}</td>
                  <td><span class="inv-uom-badge">${esc(it.uom || '-')}</span></td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      </div>
    `;
    container.insertAdjacentHTML('beforeend', groupHtml);
  });

  // Unmapped products
  if (data.unmapped && data.unmapped.length) {
    const unmappedHtml = `
      <div class="inv-unmapped-section">
        <div class="inv-unmapped-header">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
          Products Sold Without Recipe Mapping (${data.unmapped.length})
        </div>
        <div class="inv-table-wrap">
          <table class="inv-table">
            <thead><tr><th>Product Name</th><th style="text-align:right;">Qty Sold</th></tr></thead>
            <tbody>
              ${data.unmapped.map(u => `<tr><td>${esc(u.product_name)}</td><td class="inv-qty-cell" style="text-align:right;">${u.qty_sold}</td></tr>`).join('')}
            </tbody>
          </table>
        </div>
      </div>
    `;
    container.insertAdjacentHTML('beforeend', unmappedHtml);
  }
}

async function exportReport() {
  const date = $('#reportDate')?.value;
  if (!date) return;
  window.location.href = `/api/inventory/export_daily_report?date=${date}`;
}

/* ── Utilities ─────────────────────────────────────────────────────── */
function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.content : '';
}

function todayLocal() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

function debounce(fn, ms) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn.apply(null, a), ms); };
}

function showToast(msg, type) {
  if (window.showToast) { window.showToast(msg, type); return; }
  const toast = document.createElement('div');
  toast.style.cssText = `position:fixed;bottom:24px;right:24px;padding:12px 20px;border-radius:8px;color:#fff;font-size:0.85rem;font-weight:600;z-index:9999;transition:opacity .3s ease;${type === 'error' ? 'background:#ef4444;' : 'background:#22c55e;'}`;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 3000);
}

/* ── Expose global hooks for onclick events in HTML ──────────────── */
window.__inv = { editRecipe, deleteRecipe };

export default { mount, destroy };
