/* =============================================================================
   pages/all_orders.js — order history (templates/all_orders.html)
   -----------------------------------------------------------------------------
   spa.js contract: default export { mount, destroy }. Ports the legacy
   all_orders.html inline controller 1:1 (see .ulpi/design/pos-cashier-core.md,
   "All orders"): filter toolbar (date range picker, status, search, order
   type), data table loaded page-by-page from /api/all_orders, pagination
   with page-size selector, and row actions (view, reprint invoice, refund).
   The admin-auth modal supports both card swipe (USB HID magstripe) and
   username/password credentials (V1 parity). All modals use the njX
   inline-overlay idiom — no Bootstrap classes.
   ========================================================================== */
'use strict';

import { api, getCsrfToken } from '../core/api.js';
import modal from '../core/modal.js';
import { notify } from '../core/toast.js';
import { guardPrinter } from '../core/guards.js';

/* ---- Module state -------------------------------------------------------- */
let rootEl = null;
let destroyFns = [];

/* DOM references */
let tableWrap, tableBody, emptyState, pagination, pageInfo, pageControls;
let pageSizeSelect, filterForm, statusSelect, searchInput, orderTypeSelect;
let startInput, endInput;

/* Pagination state */
let currentPage = 1;
let perPage = 24;

/* Request sequencing (supersede stale responses) */
let requestSeq = 0;

/* Date range picker state */
let pickerState = { start: null, end: null, view: null, userPicked: false };

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];
const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

/* ========================================================================= */
/* Helpers                                                                    */
/* ========================================================================= */

function esc(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

function titleCase(value) {
  const s = String(value || '');
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '';
}

function typeLabel(value) {
  return { dinein: 'Dine-in', takeout: 'Takeout', pickup: 'Pickup', delivery: 'Delivery' }[value]
    || titleCase(value) || 'N/A';
}

function formatPeso(n) {
  return '\u20B1' + Number(n || 0).toFixed(2);
}

/* Inline SVG icon snippets for JS-rendered rows (mirror _icons.html paths) */
const ICO = {
  utensils: '<svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 2v7c0 1.1.9 2 2 2h4a2 2 0 0 0 2-2V2"/><path d="M7 2v20"/><path d="M21 15V2v0a5 5 0 0 0-5 5v6c0 1.1.9 2 2 2h3zm0 0v7"/></svg>',
  calendar: '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
  clock: '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>',
  eye: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>',
  printer: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9V2h12v7"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>',
  undo: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6.74 2.74L3 13"/></svg>',
  chevronLeft: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>',
  chevronRight: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>',
};

/* ========================================================================= */
/* Data loader                                                                */
/* ========================================================================= */

function loadingHtml() {
  return '<div class="ao-loading"><svg class="icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4"/><path d="M12 18v4"/><path d="M4.93 4.93l2.83 2.83"/><path d="M16.24 16.24l2.83 2.83"/><path d="M2 12h4"/><path d="M18 12h4"/><path d="M4.93 19.07l2.83-2.83"/><path d="M16.24 7.76l2.83-2.83"/></svg> Loading orders\u2026</div>';
}

function errorHtml() {
  return '<div class="ao-loading">Failed to load orders. Please try again.</div>';
}

function rowHtml(o) {
  const headline = esc(o.invoice_no || o.order_no);
  const customer = esc(o.customer_name || 'N/A');
  const printBtn = o.invoice_no
    ? `<button type="button" class="ao-action-btn ao-btn-print" title="Reprint Sales Invoice" data-action="reprint" data-order-id="${o.id}">${ICO.printer}</button>`
    : '';
  const refundBtn = o.status === 'completed'
    ? `<button type="button" class="ao-action-btn ao-btn-refund" title="Refund Order" data-action="refund" data-order-id="${o.id}">${ICO.undo}</button>`
    : '';
  return `<article class="ao-row status-${esc(o.status)}">` +
    `<div class="ao-col">` +
    `<h4 class="ao-invoice" title="${headline}">${headline}</h4>` +
    `<p class="ao-order-no" title="Order: ${esc(o.order_no)}">Order: ${esc(o.order_no)}</p>` +
    `<div class="ao-mini-pills">` +
    `<span class="ao-mini-pill">${ICO.utensils}${esc(typeLabel(o.order_type))}</span>` +
    `</div></div>` +
    `<div class="ao-col">` +
    `<p class="ao-date-line">${ICO.calendar}${esc(o.date_str)}</p>` +
    `<p class="ao-date-line">${ICO.clock}${esc(o.time_str)}</p>` +
    `</div>` +
    `<div class="ao-col">` +
    `<p class="ao-customer">${customer}</p>` +
    `<p class="ao-muted">Walk-in Customer</p>` +
    `</div>` +
    `<div class="ao-col">` +
    `<p class="ao-total">${formatPeso(o.total)}</p>` +
    `<p class="ao-muted">${o.items_count} Items</p>` +
    `</div>` +
    `<div class="ao-col">` +
    `<span class="ao-status status-${esc(o.status)}">${esc(String(o.status).toUpperCase())}</span>` +
    `</div>` +
    `<div class="ao-col ao-actions">` +
    `<a href="/order/${o.id}?ref=all_orders" class="ao-action-btn ao-btn-view" title="View Order">${ICO.eye}</a>` +
    printBtn +
    refundBtn +
    `</div></article>`;
}

function pageBtn(p, inner) {
  return `<button type="button" class="ao-page-btn" data-page="${p}">${inner}</button>`;
}

function paginationHtml(page, totalPages) {
  if (totalPages <= 1) return '';
  const left = ICO.chevronLeft;
  const right = ICO.chevronRight;
  let html = page > 1 ? pageBtn(page - 1, left) : `<span class="ao-page-btn disabled">${left}</span>`;
  const startPage = Math.max(1, page - 2);
  const endPage = Math.min(totalPages, page + 2);
  if (startPage > 1) {
    html += pageBtn(1, '1');
    if (startPage > 2) html += '<span class="ao-page-btn disabled">\u2026</span>';
  }
  for (let p = startPage; p <= endPage; p++) {
    html += p === page
      ? `<span class="ao-page-btn active">${p}</span>`
      : pageBtn(p, String(p));
  }
  if (endPage < totalPages) {
    if (endPage < totalPages - 1) html += '<span class="ao-page-btn disabled">\u2026</span>';
    html += pageBtn(totalPages, String(totalPages));
  }
  html += page < totalPages ? pageBtn(page + 1, right) : `<span class="ao-page-btn disabled">${right}</span>`;
  return html;
}

function renderPagination(data) {
  const from = data.total_orders === 0 ? 0 : (data.page - 1) * data.per_page + 1;
  const to = Math.min(data.page * data.per_page, data.total_orders);
  pageInfo.textContent = `Showing ${from} to ${to} of ${data.total_orders} result${data.total_orders !== 1 ? 's' : ''}`;
  pageControls.innerHTML = paginationHtml(data.page, data.total_pages);
}

function syncUrl() {
  const u = new URL(window.location.origin + '/all_orders');
  if (startInput.value) u.searchParams.set('start_date', startInput.value);
  if (endInput.value) u.searchParams.set('end_date', endInput.value);
  if (statusSelect.value) u.searchParams.set('status', statusSelect.value);
  if (orderTypeSelect.value) u.searchParams.set('order_type', orderTypeSelect.value);
  if (searchInput.value.trim()) u.searchParams.set('search', searchInput.value.trim());
  if (currentPage > 1) u.searchParams.set('page', currentPage);
  if (perPage !== 24) u.searchParams.set('per_page', perPage);
  window.history.replaceState({}, document.title, u.toString());
}

async function loadOrders() {
  const seq = ++requestSeq;
  tableBody.innerHTML = loadingHtml();
  const params = new URLSearchParams();
  params.set('page', currentPage);
  params.set('per_page', perPage);
  if (startInput.value) params.set('start_date', startInput.value);
  if (endInput.value) params.set('end_date', endInput.value);
  if (statusSelect.value) params.set('status', statusSelect.value);
  if (orderTypeSelect.value) params.set('order_type', orderTypeSelect.value);
  if (searchInput.value.trim()) params.set('search', searchInput.value.trim());
  try {
    const data = await api.get('/api/all_orders', Object.fromEntries(params));
    if (seq !== requestSeq) return;
    currentPage = data.page;
    if (data.total_orders === 0) {
      tableWrap.hidden = true;
      pagination.hidden = true;
      emptyState.hidden = false;
    } else {
      emptyState.hidden = true;
      tableWrap.hidden = false;
      pagination.hidden = false;
    }
    tableBody.innerHTML = data.orders.map(rowHtml).join('');
    renderPagination(data);
    syncUrl();
  } catch (err) {
    if (seq === requestSeq) tableBody.innerHTML = errorHtml();
  }
}

/* ========================================================================= */
/* Date range picker                                                          */
/* ========================================================================= */

function parseISO(value) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
}

function toISO(d) {
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return d.getFullYear() + '-' + mm + '-' + dd;
}

function fmtDate(d) {
  return d.toLocaleDateString('en-US', { month: 'short', day: '2-digit', year: 'numeric' });
}

function sameDay(a, b) {
  return a && b && a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function syncLabel() {
  const label = document.getElementById('aoRangeLabel');
  if (!label) return;
  if (pickerState.start && pickerState.end) {
    label.textContent = fmtDate(pickerState.start) + '  \u2192  ' + fmtDate(pickerState.end);
  } else if (pickerState.start) {
    label.textContent = fmtDate(pickerState.start) + '  \u2192  pick end date';
  } else {
    label.textContent = 'Select date range';
  }
  startInput.value = pickerState.start ? toISO(pickerState.start) : '';
  endInput.value = pickerState.end ? toISO(pickerState.end) : '';
  if (pickerState.userPicked && pickerState.start && pickerState.end) {
    startInput.dispatchEvent(new Event('change', { bubbles: true }));
  }
}

function renderCalendar() {
  const pop = document.getElementById('aoRangePop');
  if (!pop) return;
  const v = pickerState.view;
  const y = v.getFullYear();
  const m = v.getMonth();
  const firstDow = new Date(y, m, 1).getDay();
  const daysInMonth = new Date(y, m + 1, 0).getDate();
  const today = new Date();
  let html = `<div class="ao-cal-head">` +
    `<button type="button" class="ao-cal-nav" data-nav="-1" title="Previous month">${ICO.chevronLeft}</button>` +
    `<span class="ao-cal-title">${MONTHS[m]} ${y}</span>` +
    `<button type="button" class="ao-cal-nav" data-nav="1" title="Next month">${ICO.chevronRight}</button>` +
    `</div><div class="ao-cal-grid">`;
  DOW.forEach(function (d) { html += `<span class="ao-cal-dow">${d}</span>`; });
  for (let i = 0; i < firstDow; i++) html += '<span class="ao-cal-day blank"></span>';
  for (let d = 1; d <= daysInMonth; d++) {
    const date = new Date(y, m, d);
    const cls = ['ao-cal-day'];
    if (sameDay(date, today)) cls.push('today');
    if (sameDay(date, pickerState.start)) cls.push('range-start');
    if (sameDay(date, pickerState.end)) cls.push('range-end');
    if (pickerState.start && pickerState.end && date > pickerState.start && date < pickerState.end) cls.push('in-range');
    html += `<button type="button" class="${cls.join(' ')}" data-date="${toISO(date)}">${d}</button>`;
  }
  html += '</div>';
  pop.innerHTML = html;
}

function openCalendar() { renderCalendar(); const pop = document.getElementById('aoRangePop'); if (pop) pop.hidden = false; const btn = document.getElementById('aoRangeBtn'); if (btn) btn.setAttribute('aria-expanded', 'true'); }
function closeCalendar() { const pop = document.getElementById('aoRangePop'); if (pop) pop.hidden = true; const btn = document.getElementById('aoRangeBtn'); if (btn) btn.setAttribute('aria-expanded', 'false'); }

function initializeRangePicker() {
  const btn = document.getElementById('aoRangeBtn');
  const pop = document.getElementById('aoRangePop');
  if (!btn || !pop) return;

  const anchor = parseISO(endInput.value) || parseISO(startInput.value) || new Date();
  pickerState.start = parseISO(startInput.value);
  pickerState.end = parseISO(endInput.value);
  pickerState.view = new Date(anchor.getFullYear(), anchor.getMonth(), 1);

  const onBtnClick = () => { pop.hidden ? openCalendar() : closeCalendar(); };

  const onPopClick = (e) => {
    e.stopPropagation();
    const nav = e.target.closest('.ao-cal-nav');
    if (nav) {
      pickerState.view = new Date(pickerState.view.getFullYear(), pickerState.view.getMonth() + Number(nav.dataset.nav), 1);
      renderCalendar();
      return;
    }
    const day = e.target.closest('.ao-cal-day[data-date]');
    if (!day) return;
    const date = parseISO(day.dataset.date);
    if (!date) return;
    pickerState.userPicked = true;
    if (pickerState.start && !pickerState.end) {
      if (date < pickerState.start) { pickerState.start = date; } else { pickerState.end = date; }
    } else {
      pickerState.start = date;
      pickerState.end = null;
    }
    syncLabel();
    if (pickerState.start && pickerState.end) { renderCalendar(); closeCalendar(); } else { renderCalendar(); }
  };

  const onDocClick = (e) => {
    const picker = document.getElementById('aoRangePicker');
    if (picker && !picker.contains(e.target)) closeCalendar();
  };

  const onKeydown = (e) => { if (e.key === 'Escape') closeCalendar(); };

  btn.addEventListener('click', onBtnClick);
  pop.addEventListener('click', onPopClick);
  document.addEventListener('click', onDocClick);
  document.addEventListener('keydown', onKeydown);

  syncLabel();

  destroyFns.push(() => {
    btn.removeEventListener('click', onBtnClick);
    pop.removeEventListener('click', onPopClick);
    document.removeEventListener('click', onDocClick);
    document.removeEventListener('keydown', onKeydown);
  });
}

/* ========================================================================= */
/* Admin auth modal (card swipe + credentials)                                */
/* ========================================================================= */

function showAdminAuthModal(options = {}) {
  const {
    title = 'Authentication Required',
    text = 'Swipe an admin or manager card, or enter credentials to continue.',
    confirmButtonText = 'Authenticate',
  } = options;

  return new Promise((resolve) => {
    let resolved = false;
    let mode = 'swipe';
    let keyBuffer = '';
    let lastKeyTime = 0;
    let swipeTimer = null;
    const SWIPE_TIMEOUT_MS = 350;
    const SWIPE_MIN_LENGTH = 8;
    const overlayId = 'ao-auth-modal';

    let el = document.getElementById('modal-host');
    if (!el) {
      el = document.createElement('div');
      el.id = 'modal-host';
      document.body.appendChild(el);
    }

    const overlay = document.createElement('div');
    overlay.className = 'lib-modal-overlay';
    overlay.id = overlayId;

    const box = document.createElement('div');
    box.className = 'lib-modal-box is-warning lib-modal-warning';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', overlayId + '-title');

    box.innerHTML = `
      <h3 class="lib-modal-title lib-modal-warning-title" id="${overlayId}-title">
        <span class="lib-modal-warning-icon"><svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></span>
        ${title}
      </h3>
      <p class="lib-modal-text lib-modal-warning-text">${text}</p>
      <div class="auth-tabs" role="tablist">
        <button type="button" class="auth-tab active" data-auth-mode="swipe" aria-pressed="true">Card Swipe</button>
        <button type="button" class="auth-tab" data-auth-mode="credentials" aria-pressed="false">Use Credentials</button>
      </div>
      <div class="auth-panel" data-auth-panel="swipe">
        <div class="auth-swipe-waiting">
          <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="4" width="22" height="16" rx="2" ry="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>
          <span>Waiting for card swipe...</span>
        </div>
        <span class="auth-swipe-hint">Use an active admin or manager card.</span>
      </div>
      <form class="auth-form" data-auth-panel="credentials" style="display:none;">
        <div class="auth-field">
          <label for="${overlayId}-user">Admin/Manager Username</label>
          <input type="text" id="${overlayId}-user" name="username" placeholder="Enter username" autocomplete="username">
        </div>
        <div class="auth-field">
          <label for="${overlayId}-pass">Admin/Manager Password</label>
          <input type="password" id="${overlayId}-pass" name="password" placeholder="Enter password" autocomplete="current-password">
        </div>
      </form>
      <div class="lib-modal-warning-actions" style="margin-top:0.75rem;">
        <button type="button" class="btn lib-modal-warning-cancel" data-auth-action="cancel">Cancel</button>
        <button type="button" class="btn btn-warning" data-auth-action="confirm">${confirmButtonText}</button>
      </div>`;

    overlay.appendChild(box);
    el.appendChild(overlay);

    function doResolve(value) {
      if (resolved) return;
      resolved = true;
      if (window.closeModal) window.closeModal(overlay);
      overlay.remove();
      document.removeEventListener('keydown', onKeyDown);
      if (swipeTimer) clearTimeout(swipeTimer);
      resolve(value);
    }

    function extractCardNumber(buffer) {
      const digitsOnly = String(buffer).replace(/\D/g, '');
      return digitsOnly.length >= SWIPE_MIN_LENGTH ? digitsOnly : null;
    }

    function onSwipeDetected(raw) {
      const cardNum = extractCardNumber(raw);
      if (!cardNum) return;
      doResolve({ card_number: cardNum });
    }

    function onKeyDown(e) {
      if (resolved) return;
      const target = e.target;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'SELECT' || target.tagName === 'TEXTAREA')) return;
      const now = Date.now();
      if (now - lastKeyTime > SWIPE_TIMEOUT_MS) keyBuffer = '';
      lastKeyTime = now;
      if (e.key.length === 1) keyBuffer += e.key;
      if (swipeTimer) { clearTimeout(swipeTimer); swipeTimer = null; }
      if (e.key === 'Enter' && keyBuffer.length >= SWIPE_MIN_LENGTH) {
        onSwipeDetected(keyBuffer);
        keyBuffer = '';
        e.preventDefault();
        return;
      }
      if (keyBuffer.length > 120) {
        onSwipeDetected(keyBuffer);
        keyBuffer = '';
        e.preventDefault();
        return;
      }
      swipeTimer = setTimeout(() => {
        if (keyBuffer.length >= SWIPE_MIN_LENGTH) onSwipeDetected(keyBuffer);
        keyBuffer = '';
        swipeTimer = null;
      }, SWIPE_TIMEOUT_MS + 80);
      if (keyBuffer.length > 500) keyBuffer = keyBuffer.slice(-200);
    }

    box.addEventListener('click', (e) => {
      const tab = e.target.closest('[data-auth-mode]');
      if (tab) {
        mode = tab.dataset.authMode;
        box.querySelectorAll('[data-auth-mode]').forEach((t) => {
          t.classList.toggle('active', t === tab);
          t.setAttribute('aria-pressed', String(t === tab));
        });
        box.querySelector('[data-auth-panel="swipe"]').style.display = mode === 'swipe' ? '' : 'none';
        box.querySelector('[data-auth-panel="credentials"]').style.display = mode === 'credentials' ? '' : 'none';
        if (mode === 'credentials') {
          const first = box.querySelector(`#${overlayId}-user`);
          if (first) first.focus();
        }
        return;
      }
      if (e.target.closest('[data-auth-action="cancel"]')) {
        doResolve(null);
        return;
      }
      if (e.target.closest('[data-auth-action="confirm"]')) {
        if (mode === 'credentials') {
          const username = box.querySelector(`#${overlayId}-user`).value.trim();
          const password = box.querySelector(`#${overlayId}-pass`).value;
          if (!username || !password) {
            showToast('Please enter both username and password.', 'error');
            return;
          }
          doResolve({ username, password });
        } else {
          doResolve(null);
        }
      }
    });

    const userInput = box.querySelector(`#${overlayId}-user`);
    const passInput = box.querySelector(`#${overlayId}-pass`);
    if (userInput && passInput) {
      userInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          passInput.focus();
        }
      });
      passInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          const confirmBtn = box.querySelector('[data-auth-action="confirm"]');
          if (confirmBtn) confirmBtn.click();
        }
      });
    }

    box.querySelector('.auth-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const username = box.querySelector(`#${overlayId}-user`).value.trim();
      const password = box.querySelector(`#${overlayId}-pass`).value;
      if (!username || !password) {
        showToast('Please enter both username and password.', 'error');
        return;
      }
      doResolve({ username, password });
    });

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) doResolve(null);
    });
    document.addEventListener('keydown', onKeyDown);
    if (window.openModal) window.openModal(overlay.id);
  });
}

/* ========================================================================= */
/* Reason input modal                                                         */
/* ========================================================================= */

function showReasonModal(options = {}) {
  const {
    title = 'Confirm Refund Order',
    question = 'Are you sure you want to refund this order?',
    label = 'Reason for Refunding',
    placeholder = 'Enter reason for refunding this order',
    confirmButtonText = 'Confirm Refund',
    cancelButtonText = 'Cancel',
  } = options;

  return new Promise((resolve) => {
    let resolved = false;
    const overlayId = 'ao-reason-modal';

    let el = document.getElementById('modal-host');
    if (!el) {
      el = document.createElement('div');
      el.id = 'modal-host';
      document.body.appendChild(el);
    }

    const overlay = document.createElement('div');
    overlay.className = 'lib-modal-overlay';
    overlay.id = overlayId;

    const box = document.createElement('div');
    box.className = 'lib-modal-box is-warning lib-modal-warning';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', overlayId + '-title');

    box.innerHTML = `
      <h3 class="lib-modal-title lib-modal-warning-title" id="${overlayId}-title">
        <span class="lib-modal-warning-icon">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        </span>
        ${esc(title)}
      </h3>
      <p class="lib-modal-text lib-modal-warning-text">${esc(question)}</p>
      <form class="auth-form" id="aoReasonForm" style="margin-top: 0.5rem;">
        <div class="auth-field">
          <label for="aoReasonInput">${esc(label)}</label>
          <input type="text" id="aoReasonInput" name="reason" placeholder="${esc(placeholder)}" autocomplete="off">
        </div>
      </form>
      <div class="lib-modal-warning-actions" style="margin-top:0.75rem;">
        <button type="button" class="btn lib-modal-warning-cancel" data-reason-action="cancel">${esc(cancelButtonText)}</button>
        <button type="button" class="btn btn-warning" data-reason-action="confirm">${esc(confirmButtonText)}</button>
      </div>`;

    overlay.appendChild(box);
    el.appendChild(overlay);

    function doResolve(value) {
      if (resolved) return;
      resolved = true;
      if (window.closeModal) window.closeModal(overlay);
      overlay.remove();
      resolve(value);
    }

    const input = box.querySelector('#aoReasonInput');
    const form = box.querySelector('#aoReasonForm');

    box.addEventListener('click', (e) => {
      if (e.target.closest('[data-reason-action="cancel"]')) {
        doResolve(null);
        return;
      }
      if (e.target.closest('[data-reason-action="confirm"]')) {
        const val = input ? input.value.trim() : '';
        if (!val) {
          notify.error('Please enter a reason for refunding.');
          if (input) input.focus();
          return;
        }
        doResolve(val);
      }
    });

    if (form) {
      form.addEventListener('submit', (e) => {
        e.preventDefault();
        const val = input ? input.value.trim() : '';
        if (!val) {
          notify.error('Please enter a reason for refunding.');
          if (input) input.focus();
          return;
        }
        doResolve(val);
      });
    }

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) doResolve(null);
    });

    if (window.openModal) window.openModal(overlay.id);
    setTimeout(() => { if (input) input.focus(); }, 80);
  });
}

/* ========================================================================= */
/* Row actions: reprint + refund                                              */
/* ========================================================================= */

async function handleReprint(orderId) {
  const printerReady = await guardPrinter();
  if (!printerReady) return;

  const confirmed = await modal.confirm(
    'Reprint Sales Invoice',
    'Send a REPRINT copy of this sales invoice to the cashier printer?',
    { confirmLabel: 'Print' }
  );
  if (!confirmed) return;

  try {
    const data = await api.post('/reprint_receipt/' + orderId, {});
    if (data && data.deduped) return;
    if (data && data.success) {
      notify.success(data.message || 'Invoice reprinted successfully!');
    } else {
      await modal.error('Printer Error', (data && data.message) || 'Failed to reprint invoice');
    }
  } catch (err) {
    const errorMsg = (err && (err.message || (err.detail && (err.detail.message || err.detail.error))))
      || 'Printer is disconnected or unavailable. Please check USB connection.';
    await modal.error('Printer Error', errorMsg, { confirmLabel: 'I understand' });
  }
}

async function handleRefund(orderId) {
  const printerReady = await guardPrinter();
  if (!printerReady) return;

  /* Step 1: Admin authentication */
  const auth = await showAdminAuthModal({
    title: 'AUTHENTICATION REQUIRED',
    text: 'Swipe an admin or manager card to refund this order.',
    confirmButtonText: 'Authenticate',
  });
  if (!auth) return;

  try {
    const authPayload = auth.card_number
      ? { card_number: auth.card_number }
      : { username: auth.username, password: auth.password };
    const authData = await api.post('/refund_order/' + orderId, authPayload);
    if (!authData || !authData.success) {
      await modal.error('Authentication Failed', authData ? authData.message : 'Invalid admin credentials');
      return;
    }
  } catch (err) {
    const authErrMsg = (err && (err.message || (err.detail && err.detail.message))) || 'An error occurred during authentication';
    await modal.error('Authentication Failed', authErrMsg);
    return;
  }

  /* Step 2: Reason input */
  const reason = await showReasonModal({
    title: 'Confirm Refund Order',
    question: 'Are you sure you want to refund this order?',
    label: 'Reason for Refunding',
    placeholder: 'Enter reason for refunding this order',
    confirmButtonText: 'Confirm Refund',
  });
  if (!reason) return;

  /* Step 3: Confirm refund */
  try {
    const data = await api.post('/confirm_refund_order/' + orderId, { reason });
    if (data && data.success) {
      notify.success('The order has been successfully refunded.');
      loadOrders();
    } else {
      await modal.error('Refund Failed', (data && data.message) || 'Failed to refund the order');
    }
  } catch (err) {
    const errorMsg = (err && (err.message || (err.detail && (err.detail.message || err.detail.error))))
      || 'Printer is disconnected or unavailable. Please check USB connection.';
    await modal.error('Refund Failed', errorMsg, { confirmLabel: 'I understand' });
  }
}

/* ========================================================================= */
/* Filter event wiring                                                        */
/* ========================================================================= */

function initializeFilters() {
  let searchDebounce = null;

  const onFormSubmit = (e) => {
    e.preventDefault();
    currentPage = 1;
    loadOrders();
  };

  const onPageClick = (e) => {
    const btn = e.target.closest('[data-page]');
    if (!btn || btn.classList.contains('disabled')) return;
    const target = Number(btn.dataset.page);
    if (!target || target === currentPage) return;
    currentPage = target;
    loadOrders();
  };

  const onPageSizeChange = () => {
    perPage = Number(pageSizeSelect.value) || 24;
    currentPage = 1;
    loadOrders();
  };

  const onSearchInput = () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => {
      currentPage = 1;
      loadOrders();
    }, 300);
  };

  const onStatusChange = () => {
    currentPage = 1;
    loadOrders();
  };

  const onOrderTypeChange = () => {
    currentPage = 1;
    loadOrders();
  };

  const onDateChange = () => {
    currentPage = 1;
    loadOrders();
  };

  const onActionClick = (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    const orderId = btn.dataset.orderId;
    if (action === 'reprint') handleReprint(orderId);
    else if (action === 'refund') handleRefund(orderId);
  };

  filterForm.addEventListener('submit', onFormSubmit);
  pageControls.addEventListener('click', onPageClick);
  if (pageSizeSelect) pageSizeSelect.addEventListener('change', onPageSizeChange);
  searchInput.addEventListener('input', onSearchInput);
  statusSelect.addEventListener('change', onStatusChange);
  if (orderTypeSelect) orderTypeSelect.addEventListener('change', onOrderTypeChange);
  [startInput, endInput].forEach((inp) => {
    if (inp) inp.addEventListener('change', onDateChange);
  });
  tableBody.addEventListener('click', onActionClick);

  destroyFns.push(() => {
    filterForm.removeEventListener('submit', onFormSubmit);
    pageControls.removeEventListener('click', onPageClick);
    if (pageSizeSelect) pageSizeSelect.removeEventListener('change', onPageSizeChange);
    searchInput.removeEventListener('input', onSearchInput);
    statusSelect.removeEventListener('change', onStatusChange);
    if (orderTypeSelect) orderTypeSelect.removeEventListener('change', onOrderTypeChange);
    [startInput, endInput].forEach((inp) => {
      if (inp) inp.removeEventListener('change', onDateChange);
    });
    tableBody.removeEventListener('click', onActionClick);
  });
}

/* ========================================================================= */
/* mount / destroy                                                            */
/* ========================================================================= */

export function mount() {
  rootEl = document.getElementById('view-root') || document.body;

  /* Cache DOM references */
  tableWrap = document.getElementById('aoTableWrap');
  tableBody = document.getElementById('aoTableBody');
  emptyState = document.getElementById('aoEmpty');
  pagination = document.getElementById('aoPagination');
  pageInfo = document.getElementById('aoPaginationInfo');
  pageControls = document.getElementById('aoPaginationControls');
  pageSizeSelect = document.getElementById('aoPageSize');
  filterForm = document.getElementById('aoFilterForm');
  statusSelect = filterForm ? filterForm.querySelector('select[name="status"]') : null;
  searchInput = filterForm ? filterForm.querySelector('input[name="search"]') : null;
  orderTypeSelect = document.getElementById('aoOrderType');
  startInput = document.getElementById('aoStartDate');
  endInput = document.getElementById('aoEndDate');

  if (!tableBody || !pageControls) return;

  /* Read initial state from the JSON bridge */
  const stateEl = document.getElementById('aoInitialState');
  if (stateEl) {
    try {
      const initial = JSON.parse(stateEl.textContent);
      currentPage = Math.max(1, initial.current_page || 1);
      perPage = initial.per_page || 24;
    } catch (_) { /* use defaults */ }
  }

  /* Wire up events */
  initializeFilters();
  initializeRangePicker();

  /* Initial load */
  loadOrders();
}

export function destroy() {
  destroyFns.forEach((fn) => fn());
  destroyFns = [];
  rootEl = null;
}

export default { mount, destroy };
