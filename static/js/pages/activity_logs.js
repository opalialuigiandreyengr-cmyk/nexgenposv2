/* =============================================================================
   js/pages/activity_logs.js — Activity Logs (Phase 5, Milestone 5.5)
   =============================================================================
   SPA contract: default export { mount, destroy }.
   ========================================================================== */
'use strict';

import { api } from '../core/api.js';
import { notify } from '../core/toast.js';

let rootEl = null;
let destroyFns = [];

let currentPage = 1;
let perPage = 20;
let searchTerm = '';
let selectedEventType = '';
let selectedUserId = '';
let startDate = '';
let endDate = '';

/* Date range picker state */
let pickerState = { start: null, end: null, view: null };

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];
const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

const ICO = {
  chevronLeft: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>',
  chevronRight: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>',
};

/* Helpers */
function parseISO(s) {
  if (!s) return null;
  const parts = String(s).split('-');
  if (parts.length !== 3) return null;
  const y = Number(parts[0]), m = Number(parts[1]) - 1, d = Number(parts[2]);
  if (!y || isNaN(m) || !d) return null;
  return new Date(y, m, d);
}

function toISO(d) {
  if (!d) return '';
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function sameDay(a, b) {
  return a && b && a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function formatLabelDate(d) {
  if (!d) return '';
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function esc(str) {
  return String(str == null ? '' : str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function csvEscape(val) {
  if (val == null) return '""';
  const str = String(val).replace(/"/g, '""');
  return `"${str}"`;
}

function syncLabel() {
  const lbl = rootEl ? rootEl.querySelector('#actRangeLabel') : document.querySelector('#actRangeLabel');
  if (!lbl) return;

  if (pickerState.start && pickerState.end) {
    lbl.textContent = `${formatLabelDate(pickerState.start)} - ${formatLabelDate(pickerState.end)}`;
  } else if (pickerState.start) {
    lbl.textContent = `${formatLabelDate(pickerState.start)} - Select end`;
  } else {
    lbl.textContent = 'All Dates';
  }
}

function renderCalendar() {
  const pop = rootEl ? rootEl.querySelector('#actRangePop') : document.querySelector('#actRangePop');
  if (!pop) return;

  const viewDate = pickerState.view || new Date();
  const year = viewDate.getFullYear();
  const month = viewDate.getMonth();

  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();

  const prevMonthDays = new Date(year, month, 0).getDate();

  let html = `
    <div class="act-cal-hdr">
      <button type="button" class="act-cal-nav" id="actCalPrev">${ICO.chevronLeft}</button>
      <span class="act-cal-title">${MONTHS[month]} ${year}</span>
      <button type="button" class="act-cal-nav" id="actCalNext">${ICO.chevronRight}</button>
    </div>

    <div class="act-cal-grid">
      ${DOW.map(d => `<div class="act-cal-dow">${d}</div>`).join('')}
  `;

  for (let i = firstDay - 1; i >= 0; i--) {
    const pDay = prevMonthDays - i;
    html += `<div class="act-cal-day out">${pDay}</div>`;
  }

  const today = new Date();
  for (let d = 1; d <= daysInMonth; d++) {
    const cur = new Date(year, month, d);
    let cls = 'act-cal-day';
    if (sameDay(cur, today)) cls += ' today';

    const isStart = sameDay(cur, pickerState.start);
    const isEnd = sameDay(cur, pickerState.end);

    if (isStart) cls += ' sel-start range-start active';
    if (isEnd) cls += ' sel-end range-end active';

    if (pickerState.start && pickerState.end && cur > pickerState.start && cur < pickerState.end) {
      cls += ' in-range';
    }

    const isoStr = toISO(cur);
    html += `<div class="${cls}" data-date="${isoStr}">${d}</div>`;
  }

  const totalCells = firstDay + daysInMonth;
  const trailing = (7 - (totalCells % 7)) % 7;
  for (let t = 1; t <= trailing; t++) {
    html += `<div class="act-cal-day out">${t}</div>`;
  }

  html += `</div>`;

  pop.innerHTML = html;

  const btnPrev = pop.querySelector('#actCalPrev');
  const btnNext = pop.querySelector('#actCalNext');

  if (btnPrev) {
    btnPrev.addEventListener('click', (e) => {
      e.stopPropagation();
      pickerState.view = new Date(year, month - 1, 1);
      renderCalendar();
    });
  }

  if (btnNext) {
    btnNext.addEventListener('click', (e) => {
      e.stopPropagation();
      pickerState.view = new Date(year, month + 1, 1);
      renderCalendar();
    });
  }

  const grid = pop.querySelector('.act-cal-grid');
  if (grid) {
    grid.addEventListener('click', (e) => {
      const dayEl = e.target.closest('[data-date]');
      if (!dayEl) return;
      e.stopPropagation();

      const clickedDate = parseISO(dayEl.dataset.date);
      if (!clickedDate) return;

      if (!pickerState.start || (pickerState.start && pickerState.end)) {
        /* First click: select Start Date only, keep popover open */
        pickerState.start = clickedDate;
        pickerState.end = null;
        syncLabel();
        renderCalendar();
      } else if (pickerState.start && !pickerState.end) {
        if (clickedDate < pickerState.start) {
          /* Clicked an earlier date: update Start Date, keep popover open */
          pickerState.start = clickedDate;
          pickerState.end = null;
          syncLabel();
          renderCalendar();
        } else {
          /* Second click: select End Date, close popover & fetch logs */
          pickerState.end = clickedDate;
          startDate = toISO(pickerState.start);
          endDate = toISO(pickerState.end);

          syncLabel();
          renderCalendar();

          pop.hidden = true;
          currentPage = 1;
          loadLogs();
        }
      }
    });
  }
}

function initializeRangePicker() {
  const btn = rootEl ? rootEl.querySelector('#actRangeBtn') : document.querySelector('#actRangeBtn');
  const pop = rootEl ? rootEl.querySelector('#actRangePop') : document.querySelector('#actRangePop');
  if (!btn || !pop) return;

  pickerState.start = parseISO(startDate);
  pickerState.end = parseISO(endDate);
  pickerState.view = pickerState.start || new Date();

  syncLabel();

  const togglePop = (e) => {
    e.stopPropagation();
    const isHidden = pop.hidden;
    pop.hidden = !isHidden;
    if (pop.hidden === false) {
      renderCalendar();
    }
  };

  btn.addEventListener('click', togglePop);

  const onDocClick = (e) => {
    const container = rootEl ? rootEl.querySelector('#actRangePicker') : document.querySelector('#actRangePicker');
    if (container && !container.contains(e.target)) {
      pop.hidden = true;
    }
  };

  document.addEventListener('click', onDocClick);
  destroyFns.push(() => document.removeEventListener('click', onDocClick));
}

/* Real-Time Export Process with Progress Overlay */
async function startRealtimeExport() {
  const overlayId = 'act-export-modal';
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

  const box = document.createElement('div');
  box.className = 'lib-modal-box usr-modal-box';
  box.style.maxWidth = '520px';
  box.style.padding = '2rem';
  box.style.textAlign = 'center';

  box.innerHTML = `
    <div id="actExportIconWrap" style="width: 56px; height: 56px; border-radius: 50%; background: color-mix(in srgb, var(--color-primary) 15%, transparent); color: var(--color-primary); display: inline-flex; align-items: center; justify-content: center; margin-bottom: 1.25rem; margin-left: auto; margin-right: auto;">
      <svg class="act-spinner" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="animation: actSpin 1s linear infinite;">
        <line x1="12" y1="2" x2="12" y2="6"></line>
        <line x1="12" y1="18" x2="12" y2="22"></line>
        <line x1="4.93" y1="4.93" x2="7.76" y2="7.76"></line>
        <line x1="16.24" y1="16.24" x2="19.07" y2="19.07"></line>
        <line x1="2" y1="12" x2="6" y2="12"></line>
        <line x1="18" y1="12" x2="22" y2="12"></line>
        <line x1="4.93" y1="19.07" x2="7.76" y2="16.24"></line>
        <line x1="16.24" y1="7.76" x2="19.07" y2="4.93"></line>
      </svg>
    </div>

    <h3 id="actExportTitle" style="font-size: 1.25rem; font-weight: 700; color: var(--text-main); margin-bottom: 0.4rem;">Exporting Activity Logs</h3>
    <p id="actExportStatusText" style="font-size: 0.88rem; color: var(--text-muted); margin-bottom: 1.5rem;">
      Fetching activity records from server...
    </p>

    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; font-size: 0.85rem; font-weight: 600;">
      <span id="actExportCountText" style="color: var(--color-primary);">0 / 0 logs</span>
      <span id="actExportPercentText" style="color: var(--text-muted);">0%</span>
    </div>

    <div style="width: 100%; height: 10px; background: var(--bg-elev); border-radius: 5px; overflow: hidden; margin-bottom: 1.25rem; border: 1px solid var(--border-color);">
      <div id="actExportProgressBar" style="width: 0%; height: 100%; background: linear-gradient(90deg, var(--color-primary), #10b981); transition: width 0.2s ease-out; border-radius: 5px;"></div>
    </div>

    <div id="actExportInfoBox" style="background: color-mix(in srgb, var(--color-primary) 8%, transparent); border: 1px solid color-mix(in srgb, var(--color-primary) 25%, transparent); border-radius: var(--radius-md); padding: 0.75rem 1rem; font-size: 0.8rem; color: var(--text-main); text-align: left; display: flex; align-items: flex-start; gap: 0.6rem;">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--color-primary)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0; margin-top: 1px;"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
      <div>
        <strong>Notice:</strong> The Excel file will automatically download once processing reaches 100%. Please keep this window open.
      </div>
    </div>

    <div id="actExportActions" style="margin-top: 1.5rem; display: none;">
      <button type="button" class="btn user-btn-primary" id="btnCloseExportModal" style="width: 100%; height: 42px;">Done</button>
    </div>
  `;

  overlay.appendChild(box);
  hostEl.appendChild(overlay);

  if (window.openModal) window.openModal(overlay.id);

  const close = () => {
    if (window.closeModal) window.closeModal(overlay);
    overlay.remove();
  };

  box.querySelector('#btnCloseExportModal').addEventListener('click', close);

  const countText = box.querySelector('#actExportCountText');
  const percentText = box.querySelector('#actExportPercentText');
  const progressBar = box.querySelector('#actExportProgressBar');
  const statusText = box.querySelector('#actExportStatusText');
  const titleText = box.querySelector('#actExportTitle');
  const iconWrap = box.querySelector('#actExportIconWrap');
  const actionsDiv = box.querySelector('#actExportActions');

  try {
    const params = new URLSearchParams();
    params.set('export', '1');
    params.set('per_page', '5000');
    if (startDate) params.set('start_date', startDate);
    if (endDate) params.set('end_date', endDate);
    if (selectedEventType) params.set('event_type', selectedEventType);
    if (selectedUserId) params.set('user_id', selectedUserId);
    if (searchTerm) params.set('search', searchTerm);

    params.set('page', '1');
    const firstRes = await api.get(`/api/activity_logs?${params.toString()}`);

    const totalLogs = firstRes.total || 0;
    const totalPages = firstRes.total_pages || 1;

    if (totalLogs === 0) {
      statusText.textContent = 'No activity logs found for the selected filters.';
      actionsDiv.style.display = 'block';
      return;
    }

    const csvHeader = '"Date and Timestamp","Transaction No","User","Module","Activity Performed","Order No","Old Value","New Value"\n';
    const csvChunks = ['\ufeff' + csvHeader];

    let logsFetched = 0;

    (firstRes.logs || []).forEach((log) => {
      csvChunks.push([
        csvEscape(log.timestamp),
        csvEscape(log.trxn_no),
        csvEscape(log.user),
        csvEscape(log.event_type),
        csvEscape(log.description),
        csvEscape(log.order_no),
        csvEscape(log.old_value),
        csvEscape(log.new_value)
      ].join(',') + '\n');
    });

    logsFetched += (firstRes.logs || []).length;
    let pct = Math.round((logsFetched / totalLogs) * 100);
    countText.textContent = `${logsFetched.toLocaleString()} / ${totalLogs.toLocaleString()} logs`;
    percentText.textContent = `${pct}%`;
    progressBar.style.width = `${pct}%`;
    statusText.textContent = `Processing records (${logsFetched.toLocaleString()} of ${totalLogs.toLocaleString()})...`;

    for (let p = 2; p <= totalPages; p++) {
      params.set('page', String(p));
      const res = await api.get(`/api/activity_logs?${params.toString()}`);
      const batchLogs = res.logs || [];

      batchLogs.forEach((log) => {
        csvChunks.push([
          csvEscape(log.timestamp),
          csvEscape(log.trxn_no),
          csvEscape(log.user),
          csvEscape(log.event_type),
          csvEscape(log.description),
          csvEscape(log.order_no),
          csvEscape(log.old_value),
          csvEscape(log.new_value)
        ].join(',') + '\n');
      });

      logsFetched += batchLogs.length;
      pct = Math.round((logsFetched / totalLogs) * 100);
      countText.textContent = `${logsFetched.toLocaleString()} / ${totalLogs.toLocaleString()} logs`;
      percentText.textContent = `${pct}%`;
      progressBar.style.width = `${pct}%`;
      statusText.textContent = `Processing records (${logsFetched.toLocaleString()} of ${totalLogs.toLocaleString()})...`;
    }

    statusText.textContent = `Generating file download...`;
    const csvBlob = new Blob(csvChunks, { type: 'text/csv;charset=utf-8;' });
    const blobUrl = URL.createObjectURL(csvBlob);

    const dateStr = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    const timeStr = new Date().toTimeString().slice(0, 8).replace(/:/g, '');
    const filename = `activity_logs_${dateStr}_${timeStr}.csv`;

    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);

    titleText.textContent = 'Export Complete!';
    statusText.textContent = `Successfully extracted ${totalLogs.toLocaleString()} logs. File downloaded automatically.`;
    iconWrap.style.background = 'color-mix(in srgb, #10b981 15%, transparent)';
    iconWrap.style.color = '#10b981';
    iconWrap.innerHTML = `
      <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="20 6 9 17 4 12"></polyline>
      </svg>
    `;
    actionsDiv.style.display = 'block';
    notify.success(`Export complete! Downloaded ${totalLogs.toLocaleString()} logs.`);
  } catch (err) {
    console.error('Export failed:', err);
    statusText.textContent = 'An error occurred while generating the export file.';
    statusText.style.color = 'var(--color-danger, #ef4444)';
    actionsDiv.style.display = 'block';
    notify.error('Export failed. Please try again.');
  }
}

async function loadLogs() {
  const tbody = rootEl ? rootEl.querySelector('#actTableBody') : document.querySelector('#actTableBody');
  const paginationInfo = rootEl ? rootEl.querySelector('#actPaginationInfo') : document.querySelector('#actPaginationInfo');
  const paginationControls = rootEl ? rootEl.querySelector('#actPaginationControls') : document.querySelector('#actPaginationControls');

  if (!tbody) return;

  tbody.innerHTML = `
    <tr>
      <td colspan="4" style="text-align: center; color: var(--text-muted); padding: 2.5rem 1rem;">
        Loading activity logs...
      </td>
    </tr>
  `;

  try {
    const params = new URLSearchParams();
    params.set('page', String(currentPage));
    params.set('per_page', String(perPage));
    if (startDate) params.set('start_date', startDate);
    if (endDate) params.set('end_date', endDate);
    if (selectedEventType) params.set('event_type', selectedEventType);
    if (selectedUserId) params.set('user_id', selectedUserId);
    if (searchTerm) params.set('search', searchTerm);

    const query = params.toString();
    const url = `/api/activity_logs${query ? '?' + query : ''}`;
    const res = await api.get(url);

    const logs = res.logs || [];
    if (!logs.length) {
      tbody.innerHTML = `
        <tr>
          <td colspan="4" style="text-align: center; color: var(--text-muted); padding: 2.5rem 1rem;">
            No activity logs found.
          </td>
        </tr>
      `;
      if (paginationInfo) paginationInfo.textContent = 'Showing 0 logs';
      if (paginationControls) paginationControls.innerHTML = '';
      return;
    }

    tbody.innerHTML = logs.map((log) => {
      const timeStr = log.timestamp || 'N/A';
      const eventType = log.event_type || 'SYSTEM';
      const userStr = log.user || 'System';
      const desc = log.description || '';

      return `
        <tr>
          <td><span class="act-time">${esc(timeStr)}</span></td>
          <td><span class="act-badge-event">${esc(eventType)}</span></td>
          <td><span class="act-badge-user">${esc(userStr)}</span></td>
          <td><span>${esc(desc)}</span></td>
        </tr>
      `;
    }).join('');

    const totalCount = res.total != null ? res.total : logs.length;
    const startItem = (currentPage - 1) * perPage + 1;
    const endItem = Math.min(totalCount, currentPage * perPage);
    if (paginationInfo) {
      paginationInfo.textContent = `Showing ${startItem} to ${endItem} of ${totalCount} logs`;
    }

    /* Update Export to Excel link with active filters */
    const btnExport = rootEl ? rootEl.querySelector('#btnExportLogs') : document.querySelector('#btnExportLogs');
    if (btnExport) {
      btnExport.setAttribute('hx-boost', 'false');
      const qParams = new URLSearchParams();
      if (startDate) qParams.set('start_date', startDate);
      if (endDate) qParams.set('end_date', endDate);
      if (selectedEventType) qParams.set('event_type', selectedEventType);
      if (selectedUserId) qParams.set('user_id', selectedUserId);
      if (searchTerm) qParams.set('search', searchTerm);
      const str = qParams.toString();
      btnExport.href = `/export_activity_logs_excel${str ? '?' + str : ''}`;
    }

    /* Render sticky pagination page buttons */
    if (paginationControls) {
      const totalPages = Math.ceil(totalCount / perPage) || 1;
      if (totalPages <= 1) {
        paginationControls.innerHTML = '';
      } else {
        let html = '';
        html += `<button type="button" class="prod-page-btn ${currentPage <= 1 ? 'disabled' : ''}" data-page="${currentPage - 1}">&#8249;</button>`;

        const startPage = Math.max(1, currentPage - 2);
        const endPage = Math.min(totalPages, currentPage + 2);

        if (startPage > 1) {
          html += `<button type="button" class="prod-page-btn" data-page="1">1</button>`;
          if (startPage > 2) {
            html += `<span class="prod-page-btn disabled" style="border:none; background:transparent;">&hellip;</span>`;
          }
        }

        for (let p = startPage; p <= endPage; p++) {
          html += `<button type="button" class="prod-page-btn ${p === currentPage ? 'active' : ''}" data-page="${p}">${p}</button>`;
        }

        if (endPage < totalPages) {
          if (endPage < totalPages - 1) {
            html += `<span class="prod-page-btn disabled" style="border:none; background:transparent;">&hellip;</span>`;
          }
          html += `<button type="button" class="prod-page-btn" data-page="${totalPages}">${totalPages}</button>`;
        }

        html += `<button type="button" class="prod-page-btn ${currentPage >= totalPages ? 'disabled' : ''}" data-page="${currentPage + 1}">&#8250;</button>`;
        paginationControls.innerHTML = html;
      }
    }
  } catch (err) {
    console.error('Error loading activity logs:', err);
    tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--color-danger); padding: 2rem;">Error connecting to activity logs server.</td></tr>`;
  }
}

export function mount(el) {
  rootEl = el || document.querySelector('#view-root') || document;
  destroyFns = [];

  const searchInput = rootEl.querySelector('#actSearchInput');
  const eventSelect = rootEl.querySelector('#actEventSelect');
  const userSelect = rootEl.querySelector('#actUserSelect');
  const perPageSelect = rootEl.querySelector('#actPerPageSelect');
  const paginationControls = rootEl.querySelector('#actPaginationControls');
  const btnReset = rootEl.querySelector('#actBtnReset');
  const btnExport = rootEl.querySelector('#btnExportLogs');

  if (btnExport) {
    btnExport.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      startRealtimeExport();
    });
  }

  initializeRangePicker();

  if (btnReset) {
    btnReset.addEventListener('click', () => {
      searchTerm = '';
      selectedEventType = '';
      selectedUserId = '';
      startDate = '';
      endDate = '';
      pickerState.start = null;
      pickerState.end = null;
      pickerState.view = new Date();

      if (searchInput) searchInput.value = '';
      if (eventSelect) eventSelect.value = '';
      if (userSelect) userSelect.value = '';

      syncLabel();
      renderCalendar();

      currentPage = 1;
      loadLogs();
    });
  }

  let searchTimer = null;
  if (searchInput) {
    searchInput.addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        searchTerm = searchInput.value.trim();
        currentPage = 1;
        loadLogs();
      }, 300);
    });
  }

  if (eventSelect) {
    eventSelect.addEventListener('change', () => {
      selectedEventType = eventSelect.value;
      currentPage = 1;
      loadLogs();
    });
  }

  if (userSelect) {
    userSelect.addEventListener('change', () => {
      selectedUserId = userSelect.value;
      currentPage = 1;
      loadLogs();
    });
  }

  if (perPageSelect) {
    perPageSelect.addEventListener('change', () => {
      perPage = Number(perPageSelect.value) || 20;
      currentPage = 1;
      loadLogs();
    });
  }

  if (paginationControls) {
    paginationControls.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-page]');
      if (!btn || btn.classList.contains('disabled') || btn.classList.contains('active')) return;
      currentPage = Number(btn.dataset.page);
      loadLogs();
    });
  }

  loadLogs();
}

export function destroy() {
  destroyFns.forEach((fn) => {
    try { fn(); } catch (e) {}
  });
  destroyFns = [];
  rootEl = null;
}

export default { mount, destroy };
