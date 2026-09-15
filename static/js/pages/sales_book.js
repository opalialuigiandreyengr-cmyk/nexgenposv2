/* =============================================================================
   js/pages/sales_book.js — Statutory Sales Book Reports (Phase 6, Milestone 6.1)
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

function openExportModal(btnData) {
  const overlayId = 'sb-export-modal';
  if (document.getElementById(overlayId)) return;

  let hostEl = document.getElementById('modal-host');
  if (!hostEl) {
    hostEl = document.createElement('div');
    hostEl.id = 'modal-host';
    document.body.appendChild(hostEl);
  }

  const overlay = document.createElement('div');
  overlay.className = 'lib-modal-overlay';
  overlay.id = overlayId;

  const today = new Date();
  const firstDayOfMonth = new Date(today.getFullYear(), today.getMonth(), 1);

  let fromDate = toISO(firstDayOfMonth);
  let toDate = toISO(today);

  const box = document.createElement('div');
  box.className = 'lib-modal-box sb-modal-box';

  box.innerHTML = `
    <div class="sb-modal-header">
      <div class="sb-modal-icon">
        <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
      </div>
      <div>
        <h3 class="sb-modal-title">Export ${btnData.title}</h3>
        <p class="sb-modal-subtitle">Generate BIR-compliant statutory Excel workbook</p>
      </div>
    </div>

    <form id="sbExportForm" class="sb-modal-body">
      <!-- Quick Range Presets -->
      <div>
        <label style="font-size: 0.78rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 0.35rem; display: block;">Quick Presets</label>
        <div class="sb-preset-chips">
          <button type="button" class="sb-chip-btn" data-preset="today">Today</button>
          <button type="button" class="sb-chip-btn" data-preset="yesterday">Yesterday</button>
          <button type="button" class="sb-chip-btn active" data-preset="this_month">This Month</button>
          <button type="button" class="sb-chip-btn" data-preset="last_30">Last 30 Days</button>
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
      chipBtns.forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');

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
  form.addEventListener('submit', (e) => {
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

    statusText.textContent = 'Initializing background export job...';
    percentText.textContent = '0%';
    progressBar.style.width = '0%';
    countText.textContent = 'Connecting to server event stream...';

    // Map endpoint string to Python handler name
    let endpointName = 'export_senior_citizen_sales_book_excel_route';
    if (btnData.endpoint.includes('senior')) endpointName = 'export_senior_citizen_sales_book_excel_route';
    else if (btnData.endpoint.includes('pwd')) endpointName = 'export_pwd_sales_book_excel_route';
    else if (btnData.endpoint.includes('mov')) endpointName = 'export_mov_sales_book_excel_route';
    else if (btnData.endpoint.includes('athlete')) endpointName = 'export_athlete_coaches_sales_book_excel_route';
    else if (btnData.endpoint.includes('solo')) endpointName = 'export_solo_parent_sales_book_excel_route';
    else if (btnData.endpoint.includes('regular')) endpointName = 'export_regular_discount_sales_book_excel_route';

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || window.csrfToken || '';
    const headers = { 'Content-Type': 'application/json' };
    if (csrfToken) headers['X-CSRFToken'] = csrfToken;

    fetch('/api/export/start_async', {
      method: 'POST',
      headers,
      body: JSON.stringify({ endpoint: endpointName, from_date: fromVal, to_date: toVal })
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

              notify.success(`Export complete for ${btnData.title} (${formatDisplayDate(fromVal)} - ${formatDisplayDate(toVal)}).`);
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

  const downloadBtns = rootEl.querySelectorAll('[data-action="open-export"]');

  downloadBtns.forEach((btn) => {
    const handler = (e) => {
      e.preventDefault();
      e.stopPropagation();
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
}

export function destroy() {
  destroyFns.forEach((fn) => {
    try { fn(); } catch (e) {}
  });
  destroyFns = [];
  rootEl = null;
}

export default { mount, destroy };
