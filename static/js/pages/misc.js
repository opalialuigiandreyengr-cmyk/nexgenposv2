/* =============================================================================
   js/pages/misc.js — Miscellaneous Reports Hub (Phase 6, Milestone 6.5)
   =============================================================================
   SPA contract: default export { mount, destroy }.
   ========================================================================== */
'use strict';

import { notify } from '../core/toast.js';

let rootEl = null;
let destroyFns = [];

function toISO(d) {
  if (!d) return '';
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function formatDisplayDate(isoStr) {
  if (!isoStr) return '';
  const parts = isoStr.split('-');
  if (parts.length !== 3) return isoStr;
  const d = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function createModalOverlay(id) {
  let hostEl = document.getElementById('modal-host');
  if (!hostEl) {
    hostEl = document.createElement('div');
    hostEl.id = 'modal-host';
    document.body.appendChild(hostEl);
  }

  const overlay = document.createElement('div');
  overlay.className = 'lib-modal-overlay';
  overlay.id = id;
  return { overlay, hostEl };
}

/* ── Statutory Book Export Modal ────────────────────────────────────────── */
function openExportModal(btnData) {
  const overlayId = 'sb-export-modal';
  if (document.getElementById(overlayId)) return;

  const { overlay, hostEl } = createModalOverlay(overlayId);

  const today = new Date();
  const firstDayOfMonth = new Date(today.getFullYear(), today.getMonth(), 1);

  let fromDate = toISO(firstDayOfMonth);
  let toDate = toISO(today);

  const box = document.createElement('div');
  box.className = 'lib-modal-box sb-modal-box';

  box.innerHTML = `
    <div class="sb-modal-header" style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1.25rem; padding-bottom: 0.85rem; border-bottom: 1px solid var(--border-color);">
      <div class="sb-modal-icon" style="width: 42px; height: 42px; border-radius: var(--radius-md); display: inline-flex; align-items: center; justify-content: center; background: color-mix(in srgb, var(--color-primary) 12%, transparent); color: var(--color-primary); flex-shrink: 0;">
        <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
      </div>
      <div>
        <h3 class="sb-modal-title" style="font-size: 1.15rem; font-weight: 700; color: var(--text-main); margin: 0 0 0.15rem 0;">Export ${btnData.title}</h3>
        <p class="sb-modal-subtitle" style="font-size: 0.8rem; color: var(--text-muted); margin: 0;">Generate BIR-compliant statutory Excel workbook</p>
      </div>
    </div>

    <form id="sbExportForm" class="sb-modal-body" style="display: flex; flex-direction: column; gap: 1rem;">
      <!-- Quick Range Presets -->
      <div>
        <label style="font-size: 0.78rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 0.35rem; display: block;">Quick Presets</label>
        <div class="sb-preset-chips" style="display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap;">
          <button type="button" class="sb-chip-btn" data-preset="today" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; font-weight: 600; border-radius: var(--radius-sm); border: 1px solid var(--border-color); background: var(--bg-elev); color: var(--text-main); cursor: pointer;">Today</button>
          <button type="button" class="sb-chip-btn" data-preset="yesterday" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; font-weight: 600; border-radius: var(--radius-sm); border: 1px solid var(--border-color); background: var(--bg-elev); color: var(--text-main); cursor: pointer;">Yesterday</button>
          <button type="button" class="sb-chip-btn active" data-preset="this_month" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; font-weight: 600; border-radius: var(--radius-sm); border: 1px solid var(--color-primary); background: var(--color-primary); color: #ffffff; cursor: pointer;">This Month</button>
          <button type="button" class="sb-chip-btn" data-preset="last_30" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; font-weight: 600; border-radius: var(--radius-sm); border: 1px solid var(--border-color); background: var(--bg-elev); color: var(--text-main); cursor: pointer;">Last 30 Days</button>
        </div>
      </div>

      <!-- Date Inputs -->
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
        <div>
          <label for="sbFromDate" style="font-size: 0.8rem; font-weight: 600; color: var(--text-main); margin-bottom: 0.3rem; display: block;">From Date</label>
          <input type="date" id="sbFromDate" class="form-control form-control-modern" value="${fromDate}" style="width: 100%; height: 38px; padding: 0 0.75rem; background: var(--bg-input, var(--bg-main)); border: 1px solid var(--border-color); border-radius: var(--radius-md); color: var(--text-main); font-family: var(--font-sans); font-size: 0.85rem;" required>
        </div>
        <div>
          <label for="sbToDate" style="font-size: 0.8rem; font-weight: 600; color: var(--text-main); margin-bottom: 0.3rem; display: block;">To Date</label>
          <input type="date" id="sbToDate" class="form-control form-control-modern" value="${toDate}" style="width: 100%; height: 38px; padding: 0 0.75rem; background: var(--bg-input, var(--bg-main)); border: 1px solid var(--border-color); border-radius: var(--radius-md); color: var(--text-main); font-family: var(--font-sans); font-size: 0.85rem;" required>
        </div>
      </div>

      <!-- Notice Box -->
      <div class="sb-notice-box" style="background: color-mix(in srgb, var(--color-primary) 8%, transparent); border: 1px solid color-mix(in srgb, var(--color-primary) 22%, transparent); border-radius: var(--radius-md); padding: 0.65rem 0.85rem; font-size: 0.78rem; color: var(--text-main); display: flex; align-items: flex-start; gap: 0.5rem; line-height: 1.45;">
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--color-primary)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink: 0; margin-top: 1px;"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
        <div>
          Export includes breakdown of gross sales, 12% VAT exemption, discount amounts, and net sales per qualifying ticket.
        </div>
      </div>

      <!-- Real-Time Progress Bar Container (identical to Activity Logs) -->
      <div id="sbProgressContainer" style="display: none; flex-direction: column; gap: 0.45rem; background: var(--bg-elev); padding: 0.85rem 1rem; border-radius: var(--radius-md); border: 1px solid var(--border-color); margin-top: 0.5rem;">
        <div style="display: flex; align-items: center; justify-content: space-between; font-size: 0.85rem; font-weight: 600; color: var(--text-main);">
          <span id="sbProgressStatusText">Fetching sales records from server...</span>
          <span id="sbProgressPercentText" style="font-family: var(--font-mono); color: #10b981; font-weight: 700;">0%</span>
        </div>

        <div style="width: 100%; height: 10px; background: var(--bg-main); border-radius: 5px; overflow: hidden; border: 1px solid var(--border-color);">
          <div id="sbProgressBar" style="width: 0%; height: 100%; background: linear-gradient(90deg, var(--color-primary, #06b6d4), #10b981); transition: width 0.2s ease-out; border-radius: 5px;"></div>
        </div>

        <div style="font-size: 0.78rem; color: var(--text-muted);" id="sbProgressCountText">Processing qualifying sales transactions &amp; tax details...</div>

        <div style="background: color-mix(in srgb, var(--color-primary, #06b6d4) 8%, transparent); border: 1px solid color-mix(in srgb, var(--color-primary, #06b6d4) 22%, transparent); border-radius: var(--radius-md); padding: 0.65rem 0.85rem; font-size: 0.78rem; color: var(--text-main); display: flex; align-items: flex-start; gap: 0.5rem; margin-top: 0.35rem; line-height: 1.45;">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--color-primary, #06b6d4)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0; margin-top: 1px;"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
          <div>
            <strong>Notice:</strong> The Excel file will automatically download once processing reaches 100%. Please keep this window open.
          </div>
        </div>
      </div>

      <div class="sb-modal-actions" style="display: flex; align-items: center; gap: 0.75rem; margin-top: 1.25rem;">
        <button type="button" class="btn lib-modal-warning-cancel" id="btnCancelSbExport" style="height: 42px; padding: 0 1.25rem; font-weight: 600; font-size: 0.8rem; letter-spacing: 0.04em;">CANCEL</button>
        <button type="submit" class="btn btn-export-primary user-btn-primary" id="btnConfirmSbExport" style="flex: 1; height: 42px; display: inline-flex; align-items: center; justify-content: center; gap: 0.45rem; background: #10b981 !important; color: #ffffff !important; border: 1px solid #10b981 !important; border-radius: 10px !important; font-weight: 600; font-size: 0.8rem; letter-spacing: 0.04em;">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          <span>EXPORT EXCEL</span>
        </button>
      </div>
    </form>
  `;

  overlay.appendChild(box);
  hostEl.appendChild(overlay);

  const close = () => {
    if (window.closeModal) window.closeModal(overlay);
    overlay.remove();
  };

  box.querySelector('#btnCancelSbExport').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

  const inputFrom = box.querySelector('#sbFromDate');
  const inputTo = box.querySelector('#sbToDate');
  const chipBtns = box.querySelectorAll('.sb-chip-btn');

  chipBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      chipBtns.forEach((b) => {
        b.style.background = 'var(--bg-elev)';
        b.style.borderColor = 'var(--border-color)';
        b.style.color = 'var(--text-main)';
      });
      btn.style.background = 'var(--color-primary)';
      btn.style.borderColor = 'var(--color-primary)';
      btn.style.color = '#ffffff';

      const preset = btn.dataset.preset;
      const now = new Date();

      if (preset === 'today') {
        inputFrom.value = toISO(now);
        inputTo.value = toISO(now);
      } else if (preset === 'yesterday') {
        const yest = new Date(now);
        yest.setDate(yest.getDate() - 1);
        inputFrom.value = toISO(yest);
        inputTo.value = toISO(yest);
      } else if (preset === 'this_month') {
        const fdom = new Date(now.getFullYear(), now.getMonth(), 1);
        inputFrom.value = toISO(fdom);
        inputTo.value = toISO(now);
      } else if (preset === 'last_30') {
        const thirtyAgo = new Date(now);
        thirtyAgo.setDate(thirtyAgo.getDate() - 30);
        inputFrom.value = toISO(thirtyAgo);
        inputTo.value = toISO(now);
      }
    });
  });

  const form = box.querySelector('#sbExportForm');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fromVal = inputFrom.value;
    const toVal = inputTo.value;

    if (!fromVal || !toVal) {
      notify.error('Please select both From Date and To Date.');
      return;
    }

    const btnSubmit = box.querySelector('#btnConfirmSbExport');
    btnSubmit.disabled = true;
    btnSubmit.innerHTML = '<span>Exporting...</span>';

    const progressContainer = box.querySelector('#sbProgressContainer');
    if (progressContainer) {
      progressContainer.style.display = 'flex';
    }

    const statusText = box.querySelector('#sbProgressStatusText');
    const percentText = box.querySelector('#sbProgressPercentText');
    const progressBar = box.querySelector('#sbProgressBar');
    const countText = box.querySelector('#sbProgressCountText');

    let totalUnits = 0;
    let countLabel = '';

    try {
      const countRes = await fetch(`/api/reports/count?type=sales_book&from_date=${encodeURIComponent(fromVal)}&to_date=${encodeURIComponent(toVal)}`);
      if (countRes.ok) {
        const countData = await countRes.json();
        totalUnits = countData.total_units || 0;
        countLabel = countData.count_label || '';
      }
    } catch {
      totalUnits = 0;
    }

    let processedCount = 0;
    const maxTarget = totalUnits > 0 ? totalUnits : 100;

    if (countText) {
      countText.textContent = totalUnits > 0
        ? `Processing records (0 / ${totalUnits.toLocaleString()} logs)...`
        : 'Fetching sales records from server...';
    }

    const downloadUrl = `${btnData.endpoint}?from_date=${encodeURIComponent(fromVal)}&to_date=${encodeURIComponent(toVal)}`;

    const timer = setInterval(() => {
      const targetMax = Math.max(1, Math.floor(maxTarget * 0.88));
      if (processedCount < targetMax) {
        const step = Math.max(1, Math.floor(maxTarget / 15));
        processedCount += Math.floor(Math.random() * step) + 1;
        if (processedCount > targetMax) processedCount = targetMax;

        const pct = Math.min(88, Math.round((processedCount / maxTarget) * 100));
        if (percentText) percentText.textContent = `${pct}%`;
        if (progressBar) progressBar.style.width = `${pct}%`;

        if (statusText) {
          statusText.textContent = totalUnits > 0
            ? `Processing records (${processedCount.toLocaleString()} of ${totalUnits.toLocaleString()})...`
            : `Compiling sales records (${pct}%)...`;
        }
        if (countText) {
          countText.textContent = totalUnits > 0
            ? `${processedCount.toLocaleString()} / ${totalUnits.toLocaleString()} records processed (${countLabel})`
            : `Compiling sales transactions & tax details (${pct}%)...`;
        }
      }
    }, 120);

    fetch(downloadUrl)
      .then(async (res) => {
        if (!res.ok) throw new Error(`Server returned HTTP ${res.status}`);
        const blob = await res.blob();

        clearInterval(timer);
        const finalCount = totalUnits > 0 ? totalUnits : maxTarget;
        if (percentText) percentText.textContent = '100%';
        if (progressBar) progressBar.style.width = '100%';
        if (statusText) statusText.textContent = 'Export Complete!';
        if (countText) {
          countText.textContent = totalUnits > 0
            ? `Successfully processed ${finalCount.toLocaleString()} records (${countLabel}). Downloading file...`
            : 'Downloading generated report...';
        }

        const blobUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = btnData.filename || 'export.xlsx';
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);

        notify.success(`Export complete for ${btnData.title} (${formatDisplayDate(fromVal)} - ${formatDisplayDate(toVal)}).`);
        setTimeout(() => close(), 700);
      })
      .catch((err) => {
        clearInterval(timer);
        if (statusText) statusText.textContent = 'Export Failed';
        if (countText) countText.textContent = err.message || 'Error compiling export file';
        if (percentText) percentText.textContent = '!';
        btnSubmit.disabled = false;
        btnSubmit.innerHTML = '<span>EXPORT EXCEL</span>';
        notify.error('Export failed: ' + (err.message || 'Server error'));
      });
  });

  if (window.openModal) window.openModal(overlay.id);
}

/* ── 1. Daily Sales & Readings Modal ────────────────────────────────────── */
function openDailySalesModal() {
  const overlayId = 'misc-daily-sales-modal';
  if (document.getElementById(overlayId)) return;

  const { overlay, hostEl } = createModalOverlay(overlayId);
  const today = new Date();
  const firstDay = new Date(today.getFullYear(), today.getMonth(), 1);

  const fromStr = toISO(firstDay);
  const toStr = toISO(today);

  const box = document.createElement('div');
  box.className = 'lib-modal-box sb-modal-box';

  box.innerHTML = `
    <div class="sb-modal-header" style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1.25rem; padding-bottom: 0.85rem; border-bottom: 1px solid var(--border-color);">
      <div class="sb-modal-icon" style="width: 42px; height: 42px; border-radius: var(--radius-md); display: inline-flex; align-items: center; justify-content: center; background: color-mix(in srgb, #06b6d4 14%, transparent); color: #06b6d4; flex-shrink: 0;">
        <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
      </div>
      <div>
        <h3 class="sb-modal-title" style="font-size: 1.15rem; font-weight: 700; color: var(--text-main); margin: 0 0 0.15rem 0;">Daily Sales &amp; Readings</h3>
        <p class="sb-modal-subtitle" style="font-size: 0.8rem; color: var(--text-muted); margin: 0;">Generate date range Excel summary or trigger Z/X thermal printer readings</p>
      </div>
    </div>

    <div class="sb-modal-body" style="display: flex; flex-direction: column; gap: 1rem;">
      <!-- Quick Range Presets -->
      <div>
        <label style="font-size: 0.78rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 0.35rem; display: block;">Quick Presets</label>
        <div class="sb-preset-chips" style="display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap;">
          <button type="button" class="ds-chip-btn sb-chip-btn" data-preset="today" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; font-weight: 600; border-radius: var(--radius-sm); border: 1px solid var(--border-color); background: var(--bg-elev); color: var(--text-main); cursor: pointer;">Today</button>
          <button type="button" class="ds-chip-btn sb-chip-btn" data-preset="yesterday" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; font-weight: 600; border-radius: var(--radius-sm); border: 1px solid var(--border-color); background: var(--bg-elev); color: var(--text-main); cursor: pointer;">Yesterday</button>
          <button type="button" class="ds-chip-btn sb-chip-btn active" data-preset="this_month" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; font-weight: 600; border-radius: var(--radius-sm); border: 1px solid var(--color-primary); background: var(--color-primary); color: #ffffff; cursor: pointer;">This Month</button>
          <button type="button" class="ds-chip-btn sb-chip-btn" data-preset="last_30" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; font-weight: 600; border-radius: var(--radius-sm); border: 1px solid var(--border-color); background: var(--bg-elev); color: var(--text-main); cursor: pointer;">Last 30 Days</button>
        </div>
      </div>

      <!-- Date Range Inputs -->
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
        <div>
          <label for="dsFromDate" style="font-size: 0.8rem; font-weight: 600; color: var(--text-main); margin-bottom: 0.3rem; display: block;">From Date</label>
          <input type="date" id="dsFromDate" class="form-control form-control-modern" value="${fromStr}" style="width: 100%; height: 38px; padding: 0 0.75rem; background: var(--bg-input, var(--bg-main)); border: 1px solid var(--border-color); border-radius: var(--radius-md); color: var(--text-main); font-family: var(--font-sans); font-size: 0.85rem;" required>
        </div>
        <div>
          <label for="dsToDate" style="font-size: 0.8rem; font-weight: 600; color: var(--text-main); margin-bottom: 0.3rem; display: block;">To Date</label>
          <input type="date" id="dsToDate" class="form-control form-control-modern" value="${toStr}" style="width: 100%; height: 38px; padding: 0 0.75rem; background: var(--bg-input, var(--bg-main)); border: 1px solid var(--border-color); border-radius: var(--radius-md); color: var(--text-main); font-family: var(--font-sans); font-size: 0.85rem;" required>
        </div>
      </div>

      <!-- Real-Time Progress Bar Container (identical to Activity Logs) -->
      <div id="dsProgressContainer" style="display: none; flex-direction: column; gap: 0.45rem; background: var(--bg-elev); padding: 0.85rem 1rem; border-radius: var(--radius-md); border: 1px solid var(--border-color); margin-top: 0.5rem;">
        <div style="display: flex; align-items: center; justify-content: space-between; font-size: 0.85rem; font-weight: 600; color: var(--text-main);">
          <span id="dsProgressStatusText">Fetching daily sales records...</span>
          <span id="dsProgressPercentText" style="font-family: var(--font-mono); color: #10b981; font-weight: 700;">0%</span>
        </div>

        <div style="width: 100%; height: 10px; background: var(--bg-main); border-radius: 5px; overflow: hidden; border: 1px solid var(--border-color);">
          <div id="dsProgressBar" style="width: 0%; height: 100%; background: linear-gradient(90deg, var(--color-primary, #06b6d4), #10b981); transition: width 0.2s ease-out; border-radius: 5px;"></div>
        </div>

        <div style="font-size: 0.78rem; color: var(--text-muted);" id="dsProgressCountText">Extracting gross sales, tender breakdowns &amp; tax readings...</div>
      </div>

      <div style="display: flex; flex-direction: column; gap: 0.6rem; margin-top: 0.5rem;">
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.6rem;">
          <button type="button" class="btn" id="btnDsPrintZ" style="height: 38px; background: var(--bg-elev); border: 1px solid var(--border-color); color: var(--text-main); font-weight: 600; display: inline-flex; align-items: center; justify-content: center; gap: 0.4rem; border-radius: var(--radius-md); cursor: pointer;">
            <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>
            <span>Print Z-Reading</span>
          </button>
          <button type="button" class="btn" id="btnDsPrintX" style="height: 38px; background: var(--bg-elev); border: 1px solid var(--border-color); color: var(--text-main); font-weight: 600; display: inline-flex; align-items: center; justify-content: center; gap: 0.4rem; border-radius: var(--radius-md); cursor: pointer;">
            <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>
            <span>Print X-Reading</span>
          </button>
        </div>
      </div>

      <div class="sb-modal-actions" style="display: flex; align-items: center; gap: 0.75rem; margin-top: 1.25rem;">
        <button type="button" class="btn lib-modal-warning-cancel" id="btnCancelDs" style="height: 42px; padding: 0 1.25rem; font-weight: 600; font-size: 0.8rem; letter-spacing: 0.04em;">CLOSE</button>
        <button type="button" class="btn btn-export-primary user-btn-primary" id="btnDsExportExcel" style="flex: 1; height: 42px; display: inline-flex; align-items: center; justify-content: center; gap: 0.45rem; background: #10b981 !important; color: #ffffff !important; border: 1px solid #10b981 !important; border-radius: 10px !important; font-weight: 600; font-size: 0.8rem; letter-spacing: 0.04em;">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          <span>EXPORT EXCEL</span>
        </button>
      </div>
    </div>
  `;

  overlay.appendChild(box);
  hostEl.appendChild(overlay);

  const close = () => {
    if (window.closeModal) window.closeModal(overlay);
    overlay.remove();
  };

  box.querySelector('#btnCancelDs').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

  const inputFrom = box.querySelector('#dsFromDate');
  const inputTo = box.querySelector('#dsToDate');
  const chipBtns = box.querySelectorAll('.ds-chip-btn');

  chipBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      chipBtns.forEach((b) => {
        b.style.background = 'var(--bg-elev)';
        b.style.borderColor = 'var(--border-color)';
        b.style.color = 'var(--text-main)';
      });
      btn.style.background = 'var(--color-primary)';
      btn.style.borderColor = 'var(--color-primary)';
      btn.style.color = '#ffffff';

      const preset = btn.dataset.preset;
      const now = new Date();

      if (preset === 'today') {
        inputFrom.value = toISO(now);
        inputTo.value = toISO(now);
      } else if (preset === 'yesterday') {
        const yest = new Date(now);
        yest.setDate(yest.getDate() - 1);
        inputFrom.value = toISO(yest);
        inputTo.value = toISO(yest);
      } else if (preset === 'this_month') {
        const fdom = new Date(now.getFullYear(), now.getMonth(), 1);
        inputFrom.value = toISO(fdom);
        inputTo.value = toISO(now);
      } else if (preset === 'last_30') {
        const thirtyAgo = new Date(now);
        thirtyAgo.setDate(thirtyAgo.getDate() - 30);
        inputFrom.value = toISO(thirtyAgo);
        inputTo.value = toISO(now);
      }
    });
  });

  box.querySelector('#btnDsExportExcel').addEventListener('click', () => {
    const fVal = inputFrom.value || fromStr;
    const tVal = inputTo.value || toStr;

    if (!fVal || !tVal) {
      notify.error('Please select both From Date and To Date.');
      return;
    }

    const btnSubmit = box.querySelector('#btnDsExportExcel');
    const progressContainer = box.querySelector('#dsProgressContainer');
    const statusText = box.querySelector('#dsProgressStatusText');
    const percentText = box.querySelector('#dsProgressPercentText');
    const progressBar = box.querySelector('#dsProgressBar');
    const countText = box.querySelector('#dsProgressCountText');

    btnSubmit.disabled = true;
    btnSubmit.innerHTML = '<span>Exporting...</span>';
    if (progressContainer) progressContainer.style.display = 'flex';

    statusText.textContent = 'Initializing background export job...';
    percentText.textContent = '0%';
    progressBar.style.width = '0%';
    countText.textContent = 'Connecting to server event stream...';

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || window.csrfToken || '';
    const headers = { 'Content-Type': 'application/json' };
    if (csrfToken) headers['X-CSRFToken'] = csrfToken;

    fetch('/api/export/start_async', {
      method: 'POST',
      headers,
      body: JSON.stringify({ endpoint: 'export_daily_sales_report', from_date: fVal, to_date: tVal })
    })
      .then((res) => res.json())
      .then((data) => {
        if (!data.success || !data.task_id) {
          throw new Error(data.error || data.message || 'Failed to initialize export task');
        }

        const taskId = data.task_id;
        const evtSource = new EventSource(`/api/export/stream?task_id=${encodeURIComponent(taskId)}`);

        evtSource.onmessage = (event) => {
          try {
            const payload = JSON.parse(event.data);
            const { status, pct, stage, download_url, error } = payload;

            if (status === 'processing') {
              percentText.textContent = `${pct}%`;
              progressBar.style.width = `${pct}%`;
              statusText.textContent = stage || `Generating report (${pct}%)...`;
              countText.textContent = `Processing records: ${pct}% complete...`;
            } else if (status === 'completed') {
              evtSource.close();
              percentText.textContent = '100%';
              progressBar.style.width = '100%';
              statusText.textContent = 'Export Complete!';
              countText.textContent = 'Downloading generated report...';

              const a = document.createElement('a');
              a.href = download_url;
              document.body.appendChild(a);
              a.click();
              a.remove();

              notify.success(`Export complete for Daily Sales (${formatDisplayDate(fVal)} - ${formatDisplayDate(tVal)}).`);
              setTimeout(() => close(), 700);
            } else if (status === 'failed') {
              evtSource.close();
              statusText.textContent = 'Export Failed';
              countText.textContent = error || 'Error compiling export file';
              percentText.textContent = '!';
              btnSubmit.disabled = false;
              btnSubmit.innerHTML = '<span>EXPORT EXCEL</span>';
              notify.error('Export failed: ' + (error || 'Server error'));
            }
          } catch (err) {
            console.error('SSE parse error:', err);
          }
        };

        evtSource.onerror = (err) => {
          console.error('SSE connection error:', err);
          evtSource.close();
          statusText.textContent = 'Connection Lost';
          countText.textContent = 'Unable to maintain stream connection.';
          btnSubmit.disabled = false;
          btnSubmit.innerHTML = '<span>EXPORT EXCEL</span>';
        };
      })
      .catch((err) => {
        statusText.textContent = 'Export Failed';
        countText.textContent = err.message || 'Error initializing task';
        percentText.textContent = '!';
        btnSubmit.disabled = false;
        btnSubmit.innerHTML = '<span>EXPORT EXCEL</span>';
        notify.error('Export failed: ' + (err.message || 'Server error'));
      });
  });

  box.querySelector('#btnDsPrintZ').addEventListener('click', async () => {
    const val = inputTo.value || toStr;
    notify.info('Sending Z-Reading print request to thermal printer...');
    close();
    try {
      const res = await fetch(`/print_zreading?date=${encodeURIComponent(val)}`);
      if (res.ok) {
        notify.success('Z-Reading printed successfully.');
      } else {
        notify.error('Failed to print Z-Reading. Check printer status.');
      }
    } catch {
      notify.error('Network error during Z-Reading print.');
    }
  });

  box.querySelector('#btnDsPrintX').addEventListener('click', async () => {
    notify.info('Sending X-Reading print request to thermal printer...');
    close();
    try {
      const res = await fetch('/print_xreading');
      if (res.ok) {
        notify.success('X-Reading printed successfully.');
      } else {
        notify.error('Failed to print X-Reading. Check printer status.');
      }
    } catch {
      notify.error('Network error during X-Reading print.');
    }
  });

  if (window.openModal) window.openModal(overlay.id);
}

/* ── 2. Item Sales Modal ────────────────────────────────────────────────── */
function openItemSalesModal() {
  const overlayId = 'misc-item-sales-modal';
  if (document.getElementById(overlayId)) return;

  const { overlay, hostEl } = createModalOverlay(overlayId);
  const todayStr = toISO(new Date());

  const box = document.createElement('div');
  box.className = 'lib-modal-box sb-modal-box';

  box.innerHTML = `
    <div class="sb-modal-header" style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1.25rem; padding-bottom: 0.85rem; border-bottom: 1px solid var(--border-color);">
      <div class="sb-modal-icon" style="width: 42px; height: 42px; border-radius: var(--radius-md); display: inline-flex; align-items: center; justify-content: center; background: color-mix(in srgb, #f97316 14%, transparent); color: #f97316; flex-shrink: 0;">
        <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>
      </div>
      <div>
        <h3 class="sb-modal-title" style="font-size: 1.15rem; font-weight: 700; color: var(--text-main); margin: 0 0 0.15rem 0;">Item Sales Summary</h3>
        <p class="sb-modal-subtitle" style="font-size: 0.8rem; color: var(--text-muted); margin: 0;">Analyze product quantity sold and itemized revenue totals</p>
      </div>
    </div>

    <div class="sb-modal-body" style="display: flex; flex-direction: column; gap: 1rem;">
      <!-- Quick Range Presets -->
      <div>
        <label style="font-size: 0.78rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 0.35rem; display: block;">Quick Presets</label>
        <div class="sb-preset-chips" style="display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap;">
          <button type="button" class="is-chip-btn sb-chip-btn active" data-preset="today" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; font-weight: 600; border-radius: var(--radius-sm); border: 1px solid var(--color-primary); background: var(--color-primary); color: #ffffff; cursor: pointer;">Today</button>
          <button type="button" class="is-chip-btn sb-chip-btn" data-preset="yesterday" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; font-weight: 600; border-radius: var(--radius-sm); border: 1px solid var(--border-color); background: var(--bg-elev); color: var(--text-main); cursor: pointer;">Yesterday</button>
          <button type="button" class="is-chip-btn sb-chip-btn" data-preset="this_month" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; font-weight: 600; border-radius: var(--radius-sm); border: 1px solid var(--border-color); background: var(--bg-elev); color: var(--text-main); cursor: pointer;">This Month</button>
          <button type="button" class="is-chip-btn sb-chip-btn" data-preset="last_30" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; font-weight: 600; border-radius: var(--radius-sm); border: 1px solid var(--border-color); background: var(--bg-elev); color: var(--text-main); cursor: pointer;">Last 30 Days</button>
        </div>
      </div>

      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
        <div>
          <label for="isFromDate" style="font-size: 0.8rem; font-weight: 600; color: var(--text-main); margin-bottom: 0.3rem; display: block;">From Date</label>
          <input type="date" id="isFromDate" class="form-control form-control-modern" value="${todayStr}" style="width: 100%; height: 38px; padding: 0 0.75rem; background: var(--bg-input, var(--bg-main)); border: 1px solid var(--border-color); border-radius: var(--radius-md); color: var(--text-main); font-family: var(--font-sans); font-size: 0.85rem;" required>
        </div>
        <div>
          <label for="isToDate" style="font-size: 0.8rem; font-weight: 600; color: var(--text-main); margin-bottom: 0.3rem; display: block;">To Date</label>
          <input type="date" id="isToDate" class="form-control form-control-modern" value="${todayStr}" style="width: 100%; height: 38px; padding: 0 0.75rem; background: var(--bg-input, var(--bg-main)); border: 1px solid var(--border-color); border-radius: var(--radius-md); color: var(--text-main); font-family: var(--font-sans); font-size: 0.85rem;" required>
        </div>
      </div>

      <!-- Real-Time Progress Bar Container (identical to Activity Logs) -->
      <div id="isProgressContainer" style="display: none; flex-direction: column; gap: 0.45rem; background: var(--bg-elev); padding: 0.85rem 1rem; border-radius: var(--radius-md); border: 1px solid var(--border-color); margin-top: 0.5rem;">
        <div style="display: flex; align-items: center; justify-content: space-between; font-size: 0.85rem; font-weight: 600; color: var(--text-main);">
          <span id="isProgressStatusText">Fetching item sales records...</span>
          <span id="isProgressPercentText" style="font-family: var(--font-mono); color: #10b981; font-weight: 700;">0%</span>
        </div>

        <div style="width: 100%; height: 10px; background: var(--bg-main); border-radius: 5px; overflow: hidden; border: 1px solid var(--border-color);">
          <div id="isProgressBar" style="width: 0%; height: 100%; background: linear-gradient(90deg, var(--color-primary, #f97316), #10b981); transition: width 0.2s ease-out; border-radius: 5px;"></div>
        </div>

        <div style="font-size: 0.78rem; color: var(--text-muted);" id="isProgressCountText">Extracting product quantities sold &amp; itemized revenue totals...</div>
      </div>

      <div style="display: flex; flex-direction: column; gap: 0.6rem; margin-top: 0.5rem;">
        <button type="button" class="btn" id="btnIsPrintToday" style="height: 38px; background: var(--bg-elev); border: 1px solid var(--border-color); color: var(--text-main); font-weight: 600; display: inline-flex; align-items: center; justify-content: center; gap: 0.4rem; border-radius: var(--radius-md); cursor: pointer;">
          <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>
          <span>Print Today's Item Sales Slip</span>
        </button>
      </div>

      <div class="sb-modal-actions" style="display: flex; align-items: center; gap: 0.75rem; margin-top: 1.25rem;">
        <button type="button" class="btn lib-modal-warning-cancel" id="btnCancelIs" style="height: 42px; padding: 0 1.25rem; font-weight: 600; font-size: 0.8rem; letter-spacing: 0.04em;">CLOSE</button>
        <button type="button" class="btn btn-export-primary user-btn-primary" id="btnIsExportExcel" style="flex: 1; height: 42px; display: inline-flex; align-items: center; justify-content: center; gap: 0.45rem; background: #10b981 !important; color: #ffffff !important; border: 1px solid #10b981 !important; border-radius: 10px !important; font-weight: 600; font-size: 0.8rem; letter-spacing: 0.04em;">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          <span>EXPORT EXCEL</span>
        </button>
      </div>
    </div>
  `;

  overlay.appendChild(box);
  hostEl.appendChild(overlay);

  const close = () => {
    if (window.closeModal) window.closeModal(overlay);
    overlay.remove();
  };

  box.querySelector('#btnCancelIs').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

  const inputFrom = box.querySelector('#isFromDate');
  const inputTo = box.querySelector('#isToDate');
  const chipBtns = box.querySelectorAll('.is-chip-btn');

  chipBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      chipBtns.forEach((b) => {
        b.style.background = 'var(--bg-elev)';
        b.style.borderColor = 'var(--border-color)';
        b.style.color = 'var(--text-main)';
      });
      btn.style.background = 'var(--color-primary)';
      btn.style.borderColor = 'var(--color-primary)';
      btn.style.color = '#ffffff';

      const preset = btn.dataset.preset;
      const now = new Date();

      if (preset === 'today') {
        inputFrom.value = toISO(now);
        inputTo.value = toISO(now);
      } else if (preset === 'yesterday') {
        const yest = new Date(now);
        yest.setDate(yest.getDate() - 1);
        inputFrom.value = toISO(yest);
        inputTo.value = toISO(yest);
      } else if (preset === 'this_month') {
        const fdom = new Date(now.getFullYear(), now.getMonth(), 1);
        inputFrom.value = toISO(fdom);
        inputTo.value = toISO(now);
      } else if (preset === 'last_30') {
        const thirtyAgo = new Date(now);
        thirtyAgo.setDate(thirtyAgo.getDate() - 30);
        inputFrom.value = toISO(thirtyAgo);
        inputTo.value = toISO(now);
      }
    });
  });

  box.querySelector('#btnIsExportExcel').addEventListener('click', () => {
    const fVal = inputFrom.value || todayStr;
    const tVal = inputTo.value || todayStr;

    if (!fVal || !tVal) {
      notify.error('Please select both From Date and To Date.');
      return;
    }

    const btnSubmit = box.querySelector('#btnIsExportExcel');
    const progressContainer = box.querySelector('#isProgressContainer');
    const statusText = box.querySelector('#isProgressStatusText');
    const percentText = box.querySelector('#isProgressPercentText');
    const progressBar = box.querySelector('#isProgressBar');
    const countText = box.querySelector('#isProgressCountText');

    btnSubmit.disabled = true;
    btnSubmit.innerHTML = '<span>Exporting...</span>';
    if (progressContainer) progressContainer.style.display = 'flex';

    statusText.textContent = 'Initializing background export job...';
    percentText.textContent = '0%';
    progressBar.style.width = '0%';
    countText.textContent = 'Connecting to server event stream...';

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || window.csrfToken || '';
    const headers = { 'Content-Type': 'application/json' };
    if (csrfToken) headers['X-CSRFToken'] = csrfToken;

    fetch('/api/export/start_async', {
      method: 'POST',
      headers,
      body: JSON.stringify({ endpoint: 'export_item_sales_report', from_date: fVal, to_date: tVal })
    })
      .then((res) => res.json())
      .then((data) => {
        if (!data.success || !data.task_id) {
          throw new Error(data.error || data.message || 'Failed to initialize export task');
        }

        const taskId = data.task_id;
        const evtSource = new EventSource(`/api/export/stream?task_id=${encodeURIComponent(taskId)}`);

        evtSource.onmessage = (event) => {
          try {
            const payload = JSON.parse(event.data);
            const { status, pct, stage, download_url, error } = payload;

            if (status === 'processing') {
              percentText.textContent = `${pct}%`;
              progressBar.style.width = `${pct}%`;
              statusText.textContent = stage || `Generating report (${pct}%)...`;
              countText.textContent = `Processing records: ${pct}% complete...`;
            } else if (status === 'completed') {
              evtSource.close();
              percentText.textContent = '100%';
              progressBar.style.width = '100%';
              statusText.textContent = 'Export Complete!';
              countText.textContent = 'Downloading generated report...';

              const a = document.createElement('a');
              a.href = download_url;
              document.body.appendChild(a);
              a.click();
              a.remove();

              notify.success(`Export complete for Item Sales (${formatDisplayDate(fVal)} - ${formatDisplayDate(tVal)}).`);
              setTimeout(() => close(), 700);
            } else if (status === 'failed') {
              evtSource.close();
              statusText.textContent = 'Export Failed';
              countText.textContent = error || 'Error compiling export file';
              percentText.textContent = '!';
              btnSubmit.disabled = false;
              btnSubmit.innerHTML = '<span>EXPORT EXCEL</span>';
              notify.error('Export failed: ' + (error || 'Server error'));
            }
          } catch (err) {
            console.error('SSE parse error:', err);
          }
        };

        evtSource.onerror = (err) => {
          console.error('SSE connection error:', err);
          evtSource.close();
          statusText.textContent = 'Connection Lost';
          countText.textContent = 'Unable to maintain stream connection.';
          btnSubmit.disabled = false;
          btnSubmit.innerHTML = '<span>EXPORT EXCEL</span>';
        };
      })
      .catch((err) => {
        statusText.textContent = 'Export Failed';
        countText.textContent = err.message || 'Error initializing task';
        percentText.textContent = '!';
        btnSubmit.disabled = false;
        btnSubmit.innerHTML = '<span>EXPORT EXCEL</span>';
        notify.error('Export failed: ' + (err.message || 'Server error'));
      });
  });

  box.querySelector('#btnIsPrintToday').addEventListener('click', async () => {
    notify.info('Sending Item Sales Slip print request...');
    close();
    try {
      const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
      const headers = {};
      if (csrfToken) headers['X-CSRFToken'] = csrfToken;

      const res = await fetch('/print_today_item_sales_report', { method: 'POST', headers });
      if (res.ok) {
        notify.success("Today's item sales slip printed successfully.");
      } else {
        notify.error('Failed to print item sales slip. Check printer status.');
      }
    } catch {
      notify.error('Network error during item sales print.');
    }
  });

  if (window.openModal) window.openModal(overlay.id);
}

/* ── 3. BIR Sales Summary Modal ─────────────────────────────────────────── */
function openBirSummaryModal() {
  const overlayId = 'misc-bir-summary-modal';
  if (document.getElementById(overlayId)) return;

  const { overlay, hostEl } = createModalOverlay(overlayId);
  const today = new Date();
  const firstDay = new Date(today.getFullYear(), today.getMonth(), 1);

  const fromStr = toISO(firstDay);
  const toStr = toISO(today);

  const box = document.createElement('div');
  box.className = 'lib-modal-box sb-modal-box';

  box.innerHTML = `
    <div class="sb-modal-header" style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1.25rem; padding-bottom: 0.85rem; border-bottom: 1px solid var(--border-color);">
      <div class="sb-modal-icon" style="width: 42px; height: 42px; border-radius: var(--radius-md); display: inline-flex; align-items: center; justify-content: center; background: color-mix(in srgb, #8b5cf6 14%, transparent); color: #8b5cf6; flex-shrink: 0;">
        <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
      </div>
      <div>
        <h3 class="sb-modal-title" style="font-size: 1.15rem; font-weight: 700; color: var(--text-main); margin: 0 0 0.15rem 0;">BIR Sales Summary</h3>
        <p class="sb-modal-subtitle" style="font-size: 0.8rem; color: var(--text-muted); margin: 0;">Generate BIR-compliant monthly &amp; annual sales summary workbook</p>
      </div>
    </div>

    <div class="sb-modal-body" style="display: flex; flex-direction: column; gap: 1rem;">
      <!-- Quick Range Presets -->
      <div>
        <label style="font-size: 0.78rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 0.35rem; display: block;">Quick Presets</label>
        <div class="sb-preset-chips" style="display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap;">
          <button type="button" class="bir-chip-btn sb-chip-btn" data-preset="today" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; font-weight: 600; border-radius: var(--radius-sm); border: 1px solid var(--border-color); background: var(--bg-elev); color: var(--text-main); cursor: pointer;">Today</button>
          <button type="button" class="bir-chip-btn sb-chip-btn" data-preset="yesterday" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; font-weight: 600; border-radius: var(--radius-sm); border: 1px solid var(--border-color); background: var(--bg-elev); color: var(--text-main); cursor: pointer;">Yesterday</button>
          <button type="button" class="bir-chip-btn sb-chip-btn active" data-preset="this_month" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; font-weight: 600; border-radius: var(--radius-sm); border: 1px solid var(--color-primary); background: var(--color-primary); color: #ffffff; cursor: pointer;">This Month</button>
          <button type="button" class="bir-chip-btn sb-chip-btn" data-preset="last_30" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; font-weight: 600; border-radius: var(--radius-sm); border: 1px solid var(--border-color); background: var(--bg-elev); color: var(--text-main); cursor: pointer;">Last 30 Days</button>
        </div>
      </div>

      <!-- Date Range Inputs -->
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem;">
        <div>
          <label for="birFromDate" style="font-size: 0.8rem; font-weight: 600; color: var(--text-main); margin-bottom: 0.3rem; display: block;">From Date</label>
          <input type="date" id="birFromDate" class="form-control form-control-modern" value="${fromStr}" style="width: 100%; height: 38px; padding: 0 0.75rem; background: var(--bg-input, var(--bg-main)); border: 1px solid var(--border-color); border-radius: var(--radius-md); color: var(--text-main); font-family: var(--font-sans); font-size: 0.85rem;" required>
        </div>
        <div>
          <label for="birToDate" style="font-size: 0.8rem; font-weight: 600; color: var(--text-main); margin-bottom: 0.3rem; display: block;">To Date</label>
          <input type="date" id="birToDate" class="form-control form-control-modern" value="${toStr}" style="width: 100%; height: 38px; padding: 0 0.75rem; background: var(--bg-input, var(--bg-main)); border: 1px solid var(--border-color); border-radius: var(--radius-md); color: var(--text-main); font-family: var(--font-sans); font-size: 0.85rem;" required>
        </div>
      </div>

      <!-- Real-Time Progress Bar Container (identical to Activity Logs) -->
      <div id="birProgressContainer" style="display: none; flex-direction: column; gap: 0.45rem; background: var(--bg-elev); padding: 0.85rem 1rem; border-radius: var(--radius-md); border: 1px solid var(--border-color); margin-top: 0.5rem;">
        <div style="display: flex; align-items: center; justify-content: space-between; font-size: 0.85rem; font-weight: 600; color: var(--text-main);">
          <span id="birProgressStatusText">Fetching BIR sales records from server...</span>
          <span id="birProgressPercentText" style="font-family: var(--font-mono); color: #10b981; font-weight: 700;">0%</span>
        </div>

        <div style="width: 100%; height: 10px; background: var(--bg-main); border-radius: 5px; overflow: hidden; border: 1px solid var(--border-color);">
          <div id="birProgressBar" style="width: 0%; height: 100%; background: linear-gradient(90deg, var(--color-primary, #8b5cf6), #10b981); transition: width 0.2s ease-out; border-radius: 5px;"></div>
        </div>

        <div style="font-size: 0.78rem; color: var(--text-muted);" id="birProgressCountText">Extracting sales tax declarations, VATable sales &amp; Z-reading totals...</div>
      </div>

      <div class="sb-modal-actions" style="display: flex; align-items: center; gap: 0.75rem; margin-top: 1.25rem;">
        <button type="button" class="btn lib-modal-warning-cancel" id="btnCancelBir" style="height: 42px; padding: 0 1.25rem; font-weight: 600; font-size: 0.8rem; letter-spacing: 0.04em;">CLOSE</button>
        <button type="button" class="btn btn-export-primary user-btn-primary" id="btnBirExportExcel" style="flex: 1; height: 42px; display: inline-flex; align-items: center; justify-content: center; gap: 0.45rem; background: #10b981 !important; color: #ffffff !important; border: 1px solid #10b981 !important; border-radius: 10px !important; font-weight: 600; font-size: 0.8rem; letter-spacing: 0.04em;">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          <span>EXPORT EXCEL</span>
        </button>
      </div>
    </div>
  `;

  overlay.appendChild(box);
  hostEl.appendChild(overlay);

  const close = () => {
    if (window.closeModal) window.closeModal(overlay);
    overlay.remove();
  };

  box.querySelector('#btnCancelBir').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

  const inputFrom = box.querySelector('#birFromDate');
  const inputTo = box.querySelector('#birToDate');
  const chipBtns = box.querySelectorAll('.bir-chip-btn');

  chipBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      chipBtns.forEach((b) => {
        b.style.background = 'var(--bg-elev)';
        b.style.borderColor = 'var(--border-color)';
        b.style.color = 'var(--text-main)';
      });
      btn.style.background = 'var(--color-primary)';
      btn.style.borderColor = 'var(--color-primary)';
      btn.style.color = '#ffffff';

      const preset = btn.dataset.preset;
      const now = new Date();

      if (preset === 'today') {
        inputFrom.value = toISO(now);
        inputTo.value = toISO(now);
      } else if (preset === 'yesterday') {
        const yest = new Date(now);
        yest.setDate(yest.getDate() - 1);
        inputFrom.value = toISO(yest);
        inputTo.value = toISO(yest);
      } else if (preset === 'this_month') {
        const fdom = new Date(now.getFullYear(), now.getMonth(), 1);
        inputFrom.value = toISO(fdom);
        inputTo.value = toISO(now);
      } else if (preset === 'last_30') {
        const thirtyAgo = new Date(now);
        thirtyAgo.setDate(thirtyAgo.getDate() - 30);
        inputFrom.value = toISO(thirtyAgo);
        inputTo.value = toISO(now);
      }
    });
  });

  box.querySelector('#btnBirExportExcel').addEventListener('click', () => {
    const fVal = inputFrom.value || fromStr;
    const tVal = inputTo.value || toStr;

    if (!fVal || !tVal) {
      notify.error('Please select both From Date and To Date.');
      return;
    }

    const btnSubmit = box.querySelector('#btnBirExportExcel');
    const progressContainer = box.querySelector('#birProgressContainer');
    const statusText = box.querySelector('#birProgressStatusText');
    const percentText = box.querySelector('#birProgressPercentText');
    const progressBar = box.querySelector('#birProgressBar');
    const countText = box.querySelector('#birProgressCountText');

    btnSubmit.disabled = true;
    btnSubmit.innerHTML = '<span>Exporting...</span>';
    if (progressContainer) progressContainer.style.display = 'flex';

    statusText.textContent = 'Initializing background export job...';
    percentText.textContent = '0%';
    progressBar.style.width = '0%';
    countText.textContent = 'Connecting to server event stream...';

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || window.csrfToken || '';
    const headers = { 'Content-Type': 'application/json' };
    if (csrfToken) headers['X-CSRFToken'] = csrfToken;

    fetch('/api/export/start_async', {
      method: 'POST',
      headers,
      body: JSON.stringify({ endpoint: 'export_bir_sales_summary_excel', from_date: fVal, to_date: tVal })
    })
      .then((res) => res.json())
      .then((data) => {
        if (!data.success || !data.task_id) {
          throw new Error(data.error || data.message || 'Failed to initialize export task');
        }

        const taskId = data.task_id;
        const evtSource = new EventSource(`/api/export/stream?task_id=${encodeURIComponent(taskId)}`);

        evtSource.onmessage = (event) => {
          try {
            const payload = JSON.parse(event.data);
            const { status, pct, stage, download_url, error } = payload;

            if (status === 'processing') {
              percentText.textContent = `${pct}%`;
              progressBar.style.width = `${pct}%`;
              statusText.textContent = stage || `Generating report (${pct}%)...`;
              countText.textContent = `Processing records: ${pct}% complete...`;
            } else if (status === 'completed') {
              evtSource.close();
              percentText.textContent = '100%';
              progressBar.style.width = '100%';
              statusText.textContent = 'Export Complete!';
              countText.textContent = 'Downloading generated report...';

              const a = document.createElement('a');
              a.href = download_url;
              document.body.appendChild(a);
              a.click();
              a.remove();

              notify.success(`Export complete for BIR Sales Summary (${formatDisplayDate(fVal)} - ${formatDisplayDate(tVal)}).`);
              setTimeout(() => close(), 700);
            } else if (status === 'failed') {
              evtSource.close();
              statusText.textContent = 'Export Failed';
              countText.textContent = error || 'Error compiling export file';
              percentText.textContent = '!';
              btnSubmit.disabled = false;
              btnSubmit.innerHTML = '<span>EXPORT EXCEL</span>';
              notify.error('Export failed: ' + (error || 'Server error'));
            }
          } catch (err) {
            console.error('SSE parse error:', err);
          }
        };

        evtSource.onerror = (err) => {
          console.error('SSE connection error:', err);
          evtSource.close();
          statusText.textContent = 'Connection Lost';
          countText.textContent = 'Unable to maintain stream connection.';
          btnSubmit.disabled = false;
          btnSubmit.innerHTML = '<span>EXPORT EXCEL</span>';
        };
      })
      .catch((err) => {
        statusText.textContent = 'Export Failed';
        countText.textContent = err.message || 'Error initializing task';
        percentText.textContent = '!';
        btnSubmit.disabled = false;
        btnSubmit.innerHTML = '<span>EXPORT EXCEL</span>';
        notify.error('Export failed: ' + (err.message || 'Server error'));
      });
  });

  if (window.openModal) window.openModal(overlay.id);
}

export function mount(el) {
  rootEl = el || document.querySelector('#view-root') || document;
  destroyFns = [];

  // Helper to blur element on click to stop hover/focus zoom jitters
  const blurCurrent = () => {
    if (document.activeElement && typeof document.activeElement.blur === 'function') {
      document.activeElement.blur();
    }
  };

  // Bind clicks on statutory export cards
  const exportBtns = rootEl.querySelectorAll('[data-action="open-export"]');
  exportBtns.forEach((btn) => {
    const handler = (e) => {
      e.preventDefault();
      e.stopPropagation();
      blurCurrent();
      const btnData = {
        book: btn.dataset.book || '',
        title: btn.dataset.title || 'Sales Book',
        endpoint: btn.dataset.endpoint || '',
        filename: btn.dataset.filename || 'sales_book.xlsx',
      };
      openExportModal(btnData);
    };
    btn.addEventListener('click', handler);
    destroyFns.push(() => btn.removeEventListener('click', handler));
  });

  // Bind clicks on operational card launchers
  const btnDs = rootEl.querySelector('[data-action="open-daily-sales"]');
  if (btnDs) {
    const handler = (e) => {
      e.preventDefault();
      blurCurrent();
      openDailySalesModal();
    };
    btnDs.addEventListener('click', handler);
    destroyFns.push(() => btnDs.removeEventListener('click', handler));
  }

  const btnIs = rootEl.querySelector('[data-action="open-item-sales"]');
  if (btnIs) {
    const handler = (e) => {
      e.preventDefault();
      blurCurrent();
      openItemSalesModal();
    };
    btnIs.addEventListener('click', handler);
    destroyFns.push(() => btnIs.removeEventListener('click', handler));
  }

  const btnBir = rootEl.querySelector('[data-action="open-bir-summary"]');
  if (btnBir) {
    const handler = (e) => {
      e.preventDefault();
      blurCurrent();
      openBirSummaryModal();
    };
    btnBir.addEventListener('click', handler);
    destroyFns.push(() => btnBir.removeEventListener('click', handler));
  }

  // Check URL params for auto-open (e.g. ?open_modal=daily_sales) and clean URL bar immediately
  const urlParams = new URLSearchParams(window.location.search);
  const openParam = urlParams.get('open_modal');
  if (openParam) {
    // Immediately remove query params so subsequent HTMX navigation never carries ?open_modal=...
    try {
      window.history.replaceState({}, '', window.location.pathname);
    } catch (e) {}

    if (openParam === 'daily_sales') {
      openDailySalesModal();
    } else if (openParam === 'item_sales') {
      openItemSalesModal();
    } else if (openParam === 'bir_summary') {
      openBirSummaryModal();
    }
  }
}

export function destroy() {
  destroyFns.forEach((fn) => {
    try { fn(); } catch (e) {}
  });
  destroyFns = [];
  rootEl = null;
}

export default { mount, destroy };
