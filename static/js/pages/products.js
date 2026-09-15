/* =============================================================================
   js/pages/products.js — Product Catalog Management (Phase 5, Milestone 5.1)
   =============================================================================
   SPA contract: default export { mount, destroy }.
   ========================================================================== */
'use strict';

import { api } from '../core/api.js';
import modal from '../core/modal.js';
import { notify } from '../core/toast.js';

let rootEl = null;
let destroyFns = [];

/* State */
let currentPage = 1;
let perPage = 10;
let searchTerm = '';
let selectedCategory = '';
let selectedPriceRange = '';
let selectedStatus = 'active';
let categoriesList = [];

/* DOM Elements */
let searchInput, categorySelect, priceRangeSelect, statusTabs, resetBtn;
let tableBody, emptyState, paginationInfo, paginationControls, perPageSelect;
let statTotal, statActive, statArchived;

/* Helper: Escape HTML */
function esc(str) {
  return String(str == null ? '' : str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* Helper: Currency */
function formatPeso(val) {
  return '\u20B1' + Number(val || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/* Fetch & Render Products */
async function loadProducts() {
  if (!tableBody) return;
  tableBody.innerHTML = `
    <tr>
      <td colspan="7" style="text-align: center; padding: 2rem; color: var(--text-muted);">
        <span class="lib-spinner" style="display:inline-block; margin-right: 0.5rem;"></span> Loading products...
      </td>
    </tr>
  `;

  try {
    const params = {
      page: currentPage,
      per_page: perPage,
      search: searchTerm,
      category: selectedCategory,
      price_range: selectedPriceRange,
      status: selectedStatus,
    };

    const res = await api.get('/api/products', params);
    if (!res || !res.success) {
      tableBody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--color-danger); padding: 2rem;">Failed to load products.</td></tr>`;
      return;
    }

    /* Update Metrics */
    if (statTotal) statTotal.textContent = (res.active_count + res.archived_count).toLocaleString();
    if (statActive) statActive.textContent = res.active_count.toLocaleString();
    if (statArchived) statArchived.textContent = res.archived_count.toLocaleString();

    /* Render Rows */
    if (!res.products || res.products.length === 0) {
      tableBody.innerHTML = '';
      if (emptyState) emptyState.style.display = 'block';
      if (paginationInfo) paginationInfo.textContent = 'Showing 0 of 0 products';
      if (paginationControls) paginationControls.innerHTML = '';
      return;
    }

    if (emptyState) emptyState.style.display = 'none';

    tableBody.innerHTML = res.products.map((p) => {
      const isArchived = p.archived || p.status === 'archived' || p.status === 'removed';
      const statusClass = isArchived ? 'status-archived' : 'status-active';
      const statusLabel = isArchived ? 'ARCHIVED' : 'ACTIVE';

      const thumbHtml = p.image_url
        ? `<div class="prod-thumb"><img src="${esc(p.image_url)}" alt="${esc(p.name)}" loading="lazy"></div>`
        : `<div class="prod-thumb"><svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg></div>`;

      const actionButtons = isArchived
        ? `<button type="button" class="prod-action-btn prod-btn-restore" title="Restore Product" data-action="restore" data-id="${p.id}" data-name="${esc(p.name)}">
             <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
           </button>`
        : `<button type="button" class="prod-action-btn prod-btn-edit" title="Edit Product" data-action="edit" data-id="${p.id}">
             <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
           </button>
           <button type="button" class="prod-action-btn prod-btn-archive" title="Archive Product" data-action="archive" data-id="${p.id}" data-name="${esc(p.name)}">
             <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/></svg>
           </button>`;

      return `
        <tr class="${isArchived ? 'is-archived' : ''}" data-product-id="${p.id}">
          <td>
            <div class="prod-cell-main">
              ${thumbHtml}
              <div class="prod-info">
                <span class="prod-name">${esc(p.name)}</span>
                <span class="prod-sku">${esc(p.sku)}</span>
              </div>
            </div>
          </td>
          <td><span class="prod-cat-badge">${esc(p.category || 'Unassigned')}</span></td>
          <td><span class="prod-cost">${formatPeso(p.cost)}</span></td>
          <td><span class="prod-price">${formatPeso(p.price)}</span></td>
          <td><span class="prod-pax">${p.no_pax || 1} pax</span></td>
          <td><span class="prod-status-pill ${statusClass}">${statusLabel}</span></td>
          <td><div class="prod-actions">${actionButtons}</div></td>
        </tr>
      `;
    }).join('');

    /* Pagination controls */
    const startItem = (res.page - 1) * res.per_page + 1;
    const endItem = Math.min(res.total, res.page * res.per_page);
    if (paginationInfo) {
      paginationInfo.textContent = `Showing ${startItem} to ${endItem} of ${res.total} products`;
    }
    renderPaginationControls(res.page, res.total_pages);

  } catch (err) {
    console.error('Error loading products:', err);
    tableBody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--color-danger); padding: 2rem;">Error connecting to products server.</td></tr>`;
  }
}

function renderPaginationControls(page, totalPages) {
  if (!paginationControls) return;
  if (totalPages <= 1) {
    paginationControls.innerHTML = '';
    return;
  }

  let html = '';
  html += `<button type="button" class="prod-page-btn ${page <= 1 ? 'disabled' : ''}" data-page="${page - 1}">&#8249;</button>`;

  const start = Math.max(1, page - 2);
  const end = Math.min(totalPages, page + 2);

  if (start > 1) {
    html += `<button type="button" class="prod-page-btn" data-page="1">1</button>`;
    if (start > 2) html += `<span class="prod-page-btn disabled">&#8230;</span>`;
  }

  for (let p = start; p <= end; p++) {
    html += `<button type="button" class="prod-page-btn ${p === page ? 'active' : ''}" data-page="${p}">${p}</button>`;
  }

  if (end < totalPages) {
    if (end < totalPages - 1) html += `<span class="prod-page-btn disabled">&#8230;</span>`;
    html += `<button type="button" class="prod-page-btn" data-page="${totalPages}">${totalPages}</button>`;
  }

  html += `<button type="button" class="prod-page-btn ${page >= totalPages ? 'disabled' : ''}" data-page="${page + 1}">&#8250;</button>`;
  paginationControls.innerHTML = html;
}

/* Quick Add Category Modal */
function openQuickAddCategoryModal(onCreated) {
  const overlayId = 'quick-cat-modal';
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
  box.style.cssText = 'max-width: 440px; width: 100%;';

  box.innerHTML = `
    <h3 class="lib-modal-title lib-modal-warning-title" style="color: var(--text-main);">
      <span class="lib-modal-warning-icon" style="background: color-mix(in srgb, var(--color-primary) 14%, transparent); color: var(--color-primary);">
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>
      </span>
      Add New Category
    </h3>
    <form id="quickCatForm" style="display: flex; flex-direction: column; gap: 0.85rem; margin-top: 0.75rem;">
      <div style="display: flex; flex-direction: column; gap: 0.35rem;">
        <label style="font-size: 0.75rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted);">
          Category Name <span style="color: var(--color-danger, #ef4444)">*</span>
        </label>
        <input type="text" name="name" id="quickCatInputName" placeholder="e.g. Appetizers, Beverages, Desserts" required autocomplete="off"
          style="width: 100%; padding: 0.6rem 0.85rem; background: var(--bg-input, var(--bg-main)); border: 1px solid var(--border-color); border-radius: var(--radius-md); color: var(--text-main); font-size: 0.88rem; box-sizing: border-box;">
      </div>

      <div class="lib-modal-warning-actions" style="margin-top: 0.8rem; display: flex; gap: 0.6rem;">
        <button type="button" class="btn lib-modal-warning-cancel" style="flex: 1; height: 42px; justify-content: center; align-items: center;" id="btnCancelQuickCat">Cancel</button>
        <button type="submit" class="btn prod-btn-primary" style="flex: 1; height: 42px; justify-content: center; align-items: center;" id="btnSubmitQuickCat">Create Category</button>
      </div>
    </form>
  `;

  overlay.appendChild(box);
  hostEl.appendChild(overlay);

  const close = () => {
    if (window.closeModal) window.closeModal(overlay);
    overlay.remove();
  };

  box.querySelector('#btnCancelQuickCat').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

  const form = box.querySelector('#quickCatForm');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = box.querySelector('#quickCatInputName');
    const name = input.value.trim();
    if (!name) return;

    const submitBtn = box.querySelector('#btnSubmitQuickCat');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Creating...';

    try {
      const formData = new FormData();
      formData.append('name', name);

      const res = await fetch('/add_category', {
        method: 'POST',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'X-CSRFToken': (document.querySelector('meta[name="csrf-token"]') || {}).content || '',
        },
        body: formData,
      });

      const data = await res.json().catch(() => ({}));
      if (res.ok && data.success) {
        notify.success(`Category "${name}" created successfully.`);
        close();
        if (typeof onCreated === 'function') {
          onCreated(name);
        }
      } else {
        notify.error(data.message || 'Error creating category.');
        submitBtn.disabled = false;
        submitBtn.textContent = 'Create Category';
      }
    } catch (err) {
      console.error('Error creating category:', err);
      notify.error('Network error creating category.');
      submitBtn.disabled = false;
      submitBtn.textContent = 'Create Category';
    }
  });
}

/* Add / Edit Product Modal */
function openProductModal(product = null) {
  const isEdit = !!product;
  const title = isEdit ? 'Edit Product' : 'Add New Product';
  const overlayId = 'prod-form-modal';

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

  const categoryOptions = categoriesList.map((cat) => {
    const isSelected = product && product.category === cat ? 'selected' : '';
    return `<option value="${esc(cat)}" ${isSelected}>${esc(cat)}</option>`;
  }).join('');

  box.innerHTML = `
    <h3 class="lib-modal-title lib-modal-warning-title" style="color: var(--text-main); margin-bottom: 0.25rem;">
      <span class="lib-modal-warning-icon" style="background: color-mix(in srgb, var(--color-primary) 14%, transparent); color: var(--color-primary);">
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>
      </span>
      ${title}
    </h3>
    <p style="font-size: 0.82rem; color: var(--text-muted); margin: 0 0 0.85rem 0;">Enter product details, category assignment, and pricing parameters.</p>

    <form class="prod-modal-form" id="prodModalForm" enctype="multipart/form-data" style="display: flex; flex-direction: column; gap: 0.75rem;">
      
      <div class="prod-modal-grid" style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; align-items: start;">
        
        <!-- LEFT COLUMN: Product Name, Category (+ Quick Add), Pax Capacity -->
        <div style="display: flex; flex-direction: column; gap: 0.65rem;">
          <div class="prod-form-field">
            <label>Product Name <span style="color: var(--color-danger, #ef4444)">*</span></label>
            <input type="text" name="name" id="prodFormName" value="${esc(product ? product.name : '')}" placeholder="e.g. Classic Cheeseburger" required autocomplete="off">
          </div>

          <div class="prod-form-field">
            <label>Category <span style="color: var(--color-danger, #ef4444)">*</span></label>
            <div style="display: flex; gap: 0.45rem; align-items: center;">
              <select name="category" id="prodFormCategory" required style="flex: 1;">
                <option value="">Select Category</option>
                ${categoryOptions}
              </select>
              <button type="button" id="btnAddCategoryQuick" title="Add New Category" style="height: 38px; width: 38px; padding: 0; display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; background: color-mix(in srgb, var(--color-primary) 12%, transparent); color: var(--color-primary); border: 1px solid color-mix(in srgb, var(--color-primary) 30%, transparent); border-radius: var(--radius-md); cursor: pointer; transition: all 0.15s ease;">
                <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
              </button>
            </div>
          </div>

          <div class="prod-form-field">
            <label>Pax Capacity</label>
            <input type="number" name="no_pax" id="prodFormPax" value="${product ? (product.no_pax || 1) : 1}" min="1" step="1">
          </div>
        </div>

        <!-- RIGHT COLUMN: Big Square Image + Cost & Price below -->
        <div style="display: flex; flex-direction: column; gap: 0.65rem;">
          <div class="prod-form-field">
            <label>Product Image</label>
            <div class="prod-square-picker" id="prodImgPickerBox" style="width: 100%;">
              <div class="prod-big-square-preview" id="prodImgPreviewBox" title="Click to upload product image">
                ${product && product.image_url
                  ? `<img src="${esc(product.image_url)}" alt="">`
                  : `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
                     <span style="font-size: 0.68rem; font-weight: 700; text-transform: uppercase; margin-top: 0.2rem;">Click to Upload</span>`
                }
              </div>
              <input type="file" name="image" id="prodFormImage" accept="image/*" style="display: none;">
            </div>
          </div>

          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.45rem;">
            <div class="prod-form-field">
              <label>Cost (&#8369;)</label>
              <input type="number" name="cost" id="prodFormCost" value="${product ? product.cost : 0}" min="0" step="0.01" placeholder="0.00">
            </div>
            <div class="prod-form-field">
              <label>Selling (&#8369;) <span style="color: var(--color-danger, #ef4444)">*</span></label>
              <input type="number" name="price" id="prodFormPrice" value="${product ? product.price : 0}" min="0" step="0.01" placeholder="0.00" required>
            </div>
          </div>
        </div>

      </div>

      <!-- FULL WIDTH BOTTOM: Description -->
      <div class="prod-form-field">
        <label>Description</label>
        <textarea name="description" id="prodFormDesc" rows="2" placeholder="Optional description..." style="resize: vertical; min-height: 44px; max-height: 70px;">${esc(product ? product.description : '')}</textarea>
      </div>

      <!-- ACTIONS -->
      <div class="lib-modal-warning-actions" style="margin-top: 0.4rem; padding-top: 0.65rem; border-top: 1px solid var(--border-color); display: flex; gap: 0.6rem; align-items: center;">
        <button type="button" class="btn lib-modal-warning-cancel" style="flex: 1; justify-content: center; align-items: center; height: 42px; font-size: 0.85rem; font-weight: 600; text-transform: uppercase;" id="btnCancelProdModal">Cancel</button>
        <button type="submit" class="btn prod-btn-primary" style="flex: 1; justify-content: center; align-items: center; height: 42px; font-size: 0.85rem; font-weight: 600;" id="btnSubmitProdModal">${isEdit ? 'Save Changes' : 'Create Product'}</button>
      </div>
    </form>
  `;

  overlay.appendChild(box);
  hostEl.appendChild(overlay);

  const imgInput = box.querySelector('#prodFormImage');
  const pickerBox = box.querySelector('#prodImgPickerBox');
  const previewBox = box.querySelector('#prodImgPreviewBox');
  const btnQuickCat = box.querySelector('#btnAddCategoryQuick');
  const catSelect = box.querySelector('#prodFormCategory');

  if (btnQuickCat && catSelect) {
    btnQuickCat.addEventListener('click', () => {
      openQuickAddCategoryModal((newCatName) => {
        if (!categoriesList.includes(newCatName)) {
          categoriesList.push(newCatName);
          categoriesList.sort();
        }
        const opt = document.createElement('option');
        opt.value = newCatName;
        opt.textContent = newCatName;
        opt.selected = true;
        catSelect.appendChild(opt);

        const mainCatSelect = rootEl ? rootEl.querySelector('#prodCategorySelect') : document.querySelector('#prodCategorySelect');
        if (mainCatSelect) {
          const mainOpt = document.createElement('option');
          mainOpt.value = newCatName;
          mainOpt.textContent = newCatName;
          mainCatSelect.appendChild(mainOpt);
        }
      });
    });
  }

  if (pickerBox && imgInput) {
    pickerBox.addEventListener('click', () => {
      imgInput.click();
    });
  }

  if (imgInput && previewBox) {
    imgInput.addEventListener('change', (e) => {
      const file = e.target.files && e.target.files[0];
      if (file) {
        const reader = new FileReader();
        reader.onload = (evt) => {
          previewBox.innerHTML = `<img src="${evt.target.result}" alt="Preview">`;
        };
        reader.readAsDataURL(file);
      }
    });
  }

  const close = () => {
    if (window.closeModal) window.closeModal(overlay);
    overlay.remove();
  };

  box.querySelector('#btnCancelProdModal').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

  const form = box.querySelector('#prodModalForm');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const submitBtn = box.querySelector('#btnSubmitProdModal');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Saving...';

    try {
      const formData = new FormData(form);
      const url = isEdit ? `/edit_product/${product.id}` : '/add_product';
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'X-CSRFToken': (document.querySelector('meta[name="csrf-token"]') || {}).content || '' },
        body: formData,
      });

      if (res.ok) {
        notify.success(isEdit ? 'Product updated successfully.' : 'Product created successfully.');
        close();
        loadProducts();
      } else {
        const data = await res.json().catch(() => ({}));
        notify.error(data.message || 'Error saving product.');
        submitBtn.disabled = false;
        submitBtn.textContent = isEdit ? 'Save Changes' : 'Create Product';
      }
    } catch (err) {
      console.error(err);
      notify.error('Network error saving product.');
      submitBtn.disabled = false;
      submitBtn.textContent = isEdit ? 'Save Changes' : 'Create Product';
    }
  });

  if (window.openModal) window.openModal(overlay.id);
  setTimeout(() => {
    const firstInput = box.querySelector('#prodFormName');
    if (firstInput) firstInput.focus();
  }, 80);
}

/* Excel Bulk Import Modal */
function openExcelImportModal() {
  const overlayId = 'prod-import-modal';
  let hostEl = document.getElementById('modal-host');
  if (!hostEl) {
    hostEl = document.createElement('div');
    hostEl.id = 'modal-host';
    document.body.appendChild(hostEl);
  }

  const overlay = document.createElement('div');
  overlay.className = 'lib-modal-overlay';
  overlay.id = overlayId;

  const box = document.createElement('div');
  box.className = 'lib-modal-box is-warning lib-modal-warning';
  box.style.cssText = 'max-width: 480px; width: 100%;';

  box.innerHTML = `
    <h3 class="lib-modal-title lib-modal-warning-title" style="color: var(--text-main);">
      <span class="lib-modal-warning-icon" style="background: rgba(34, 197, 94, 0.14); color: #16a34a;">
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
      </span>
      Import Products (Excel)
    </h3>
    <p class="lib-modal-text lib-modal-warning-text" style="margin-bottom: 1rem;">
      Upload an Excel (<code>.xlsx</code>, <code>.xls</code>) or CSV file with columns: <strong>name, category, cost, price, no_pax, description</strong>.
    </p>

    <div class="prod-dropzone" id="excelDropzone">
      <div class="prod-dropzone-icon">
        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
      </div>
      <div>
        <p style="margin: 0; font-weight: 700; font-size: 0.92rem;">Click to browse or drag & drop</p>
        <p style="margin: 0.2rem 0 0 0; font-size: 0.78rem; color: var(--text-muted);" id="selectedFileName">Supports .xlsx, .xls, .csv</p>
      </div>
      <input type="file" id="excelFileInput" accept=".xlsx,.xls,.csv" style="display: none;">
    </div>

    <div class="lib-modal-warning-actions" style="margin-top: 1.25rem;">
      <button type="button" class="btn lib-modal-warning-cancel" id="btnCancelImport">Cancel</button>
      <button type="button" class="btn prod-btn-primary" style="flex: 1; justify-content: center; height: auto;" id="btnSubmitImport" disabled>Upload &amp; Import</button>
    </div>
  `;

  overlay.appendChild(box);
  hostEl.appendChild(overlay);

  const dropzone = box.querySelector('#excelDropzone');
  const fileInput = box.querySelector('#excelFileInput');
  const fileNameText = box.querySelector('#selectedFileName');
  const submitBtn = box.querySelector('#btnSubmitImport');
  let selectedFile = null;

  dropzone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', (e) => {
    if (e.target.files && e.target.files[0]) {
      selectedFile = e.target.files[0];
      fileNameText.textContent = `Selected: ${selectedFile.name} (${(selectedFile.size / 1024).toFixed(1)} KB)`;
      submitBtn.disabled = false;
    }
  });

  dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      selectedFile = e.dataTransfer.files[0];
      fileNameText.textContent = `Selected: ${selectedFile.name} (${(selectedFile.size / 1024).toFixed(1)} KB)`;
      submitBtn.disabled = false;
    }
  });

  const close = () => {
    if (window.closeModal) window.closeModal(overlay);
    overlay.remove();
  };

  box.querySelector('#btnCancelImport').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

  submitBtn.addEventListener('click', async () => {
    if (!selectedFile) return;
    submitBtn.disabled = true;
    submitBtn.textContent = 'Importing...';

    try {
      const formData = new FormData();
      formData.append('file', selectedFile);

      const res = await fetch('/import_products', {
        method: 'POST',
        headers: { 'X-CSRFToken': (document.querySelector('meta[name="csrf-token"]') || {}).content || '' },
        body: formData,
      });

      const data = await res.json().catch(() => ({}));
      if (res.ok && data.success) {
        notify.success(`Imported ${data.imported_count || 0} product(s) successfully.`);
        close();
        loadProducts();
      } else {
        notify.error(data.message || 'Import failed. Check file formatting.');
        submitBtn.disabled = false;
        submitBtn.textContent = 'Upload & Import';
      }
    } catch (err) {
      console.error(err);
      notify.error('Network error during import.');
      submitBtn.disabled = false;
      submitBtn.textContent = 'Upload & Import';
    }
  });

  if (window.openModal) window.openModal(overlay.id);
}

/* Event Wireup & Lifecycle */
export function mount(el) {
  rootEl = el || document.querySelector('#view-root') || document;
  destroyFns = [];

  /* Parse server category data */
  const catDataScript = rootEl.querySelector('#prodCategoriesData');
  if (catDataScript) {
    try {
      categoriesList = JSON.parse(catDataScript.textContent || '[]');
    } catch (e) {
      categoriesList = [];
    }
  }

  /* DOM references */
  searchInput = rootEl.querySelector('#prodSearchInput');
  categorySelect = rootEl.querySelector('#prodCategorySelect');
  priceRangeSelect = rootEl.querySelector('#prodPriceRangeSelect');
  statusTabs = rootEl.querySelectorAll('.prod-status-tab');
  resetBtn = rootEl.querySelector('#btnResetFilters');
  tableBody = rootEl.querySelector('#prodTableBody');
  emptyState = rootEl.querySelector('#prodEmpty');
  paginationInfo = rootEl.querySelector('#prodPaginationInfo');
  paginationControls = rootEl.querySelector('#prodPaginationControls');
  perPageSelect = rootEl.querySelector('#prodPerPageSelect');
  statTotal = rootEl.querySelector('#statTotalCount');
  statActive = rootEl.querySelector('#statActiveCount');
  statArchived = rootEl.querySelector('#statArchivedCount');

  /* Search with debounce */
  let searchTimer = null;
  const onSearch = () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      searchTerm = searchInput.value.trim();
      currentPage = 1;
      loadProducts();
    }, 280);
  };
  if (searchInput) searchInput.addEventListener('input', onSearch);

  /* Category Filter */
  const onCategoryChange = () => {
    selectedCategory = categorySelect.value;
    currentPage = 1;
    loadProducts();
  };
  if (categorySelect) categorySelect.addEventListener('change', onCategoryChange);

  /* Price Filter */
  const onPriceChange = () => {
    selectedPriceRange = priceRangeSelect.value;
    currentPage = 1;
    loadProducts();
  };
  if (priceRangeSelect) priceRangeSelect.addEventListener('change', onPriceChange);

  /* Status Tabs */
  statusTabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      statusTabs.forEach((t) => t.classList.remove('active'));
      tab.classList.add('active');
      selectedStatus = tab.dataset.status;
      currentPage = 1;
      loadProducts();
    });
  });

  /* Reset Button */
  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      if (searchInput) searchInput.value = '';
      if (categorySelect) categorySelect.value = '';
      if (priceRangeSelect) priceRangeSelect.value = '';
      statusTabs.forEach((t) => t.classList.toggle('active', t.dataset.status === 'active'));
      searchTerm = '';
      selectedCategory = '';
      selectedPriceRange = '';
      selectedStatus = 'active';
      currentPage = 1;
      loadProducts();
    });
  }

  /* Per Page Select */
  if (perPageSelect) {
    perPageSelect.addEventListener('change', () => {
      perPage = Number(perPageSelect.value) || 10;
      currentPage = 1;
      loadProducts();
    });
  }

  /* Pagination Click */
  if (paginationControls) {
    paginationControls.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-page]');
      if (!btn || btn.classList.contains('disabled') || btn.classList.contains('active')) return;
      currentPage = Number(btn.dataset.page);
      loadProducts();
    });
  }

  /* Table Actions (Edit, Archive, Restore) */
  if (tableBody) {
    tableBody.addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-action]');
      if (!btn) return;

      const action = btn.dataset.action;
      const id = btn.dataset.id;
      const name = btn.dataset.name || 'this product';

      if (action === 'edit') {
        try {
          const row = btn.closest('tr');
          const p = {
            id,
            name: row.querySelector('.prod-name').textContent,
            category: row.querySelector('.prod-cat-badge').textContent,
            cost: parseFloat(row.querySelector('.prod-cost').textContent.replace(/[^\d.]/g, '')) || 0,
            price: parseFloat(row.querySelector('.prod-price').textContent.replace(/[^\d.]/g, '')) || 0,
            no_pax: parseInt(row.querySelector('.prod-pax').textContent) || 1,
            description: '',
            image_url: (row.querySelector('.prod-thumb img') || {}).src || '',
          };
          openProductModal(p);
        } catch (err) {
          console.error(err);
        }
      } else if (action === 'archive') {
        const confirmed = await modal.confirm(
          'Archive Product',
          `Are you sure you want to archive "${name}"? It will no longer appear on active POS cashier registers.`,
          { danger: true, confirmLabel: 'Archive Product' }
        );
        if (!confirmed) return;

        try {
          const res = await api.delete(`/delete_product/${id}`);
          if (res && res.success) {
            notify.success(`Product "${name}" has been archived.`);
            loadProducts();
          } else {
            await modal.error('Archive Failed', (res && res.message) || 'Unable to archive product.');
          }
        } catch (err) {
          const msg = (err && (err.message || (err.detail && err.detail.message))) || 'Error archiving product.';
          await modal.error('Archive Error', msg);
        }
      } else if (action === 'restore') {
        try {
          const res = await api.post(`/set_product_active/${id}`);
          if (res && res.success) {
            notify.success(`Product "${name}" is now active.`);
            loadProducts();
          } else {
            await modal.error('Activation Failed', (res && res.message) || 'Unable to activate product.');
          }
        } catch (err) {
          const msg = (err && (err.message || (err.detail && err.detail.message))) || 'Error activating product.';
          await modal.error('Activation Error', msg);
        }
      }
    });
  }

  /* Header Buttons */
  const btnExport = rootEl.querySelector('#btnExportExcel') || rootEl.querySelector('#btnExportCSV');
  if (btnExport) {
    btnExport.addEventListener('click', () => {
      const params = new URLSearchParams();
      if (searchTerm) params.set('search', searchTerm);
      if (selectedCategory) params.set('category', selectedCategory);
      if (selectedStatus) params.set('status', selectedStatus);

      const exportUrl = `/export_products_excel?${params.toString()}`;
      notify.info('Downloading Excel products catalog (.xlsx)...');
      window.location.href = exportUrl;
    });
  }

  const btnAdd = rootEl.querySelector('#btnAddProduct');
  if (btnAdd) btnAdd.addEventListener('click', () => openProductModal(null));

  const btnImport = rootEl.querySelector('#btnImportExcel');
  if (btnImport) btnImport.addEventListener('click', openExcelImportModal);

  /* Initial Load */
  loadProducts();
}

export function destroy() {
  destroyFns.forEach((fn) => {
    try { fn(); } catch (e) {}
  });
  destroyFns = [];
  rootEl = null;
}

export default { mount, destroy };
