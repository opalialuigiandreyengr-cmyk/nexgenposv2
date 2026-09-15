/* =============================================================================
   pages/order_details.js — order detail view (templates/order_details.html)
   -----------------------------------------------------------------------------
   spa.js contract: default export { mount }. Ports the legacy inline
   controller from templates/order_details.html 1:1 (see
   .ulpi/design/pos-cashier-core.md, "Order detail"): the 5s presence lock
   (/order_presence + sendBeacon release), status-gated reprints and bill
   out with the printer guard, the Modify redirect into the V2 register
   (add-items flow), the admin-auth cancel flow
   (/cancel_order_auth -> reason -> /confirm_cancel_order), the item
   management modal (/submit_modify_changes with the V1 changes payload),
   and the split-bill drag zones (/modify_order auth -> /split_bill or
   /cancel_split_items). Spec addition: the OrderAuditLog timeline is
   fetched from the read-only /api/order/<id>/audit feed and rendered as a
   mono timeline. All POSTs go through core/api.js (X-CSRFToken parity).
   ========================================================================== */
'use strict';

import { api, ApiError, getCsrfToken } from '../core/api.js';
import modal from '../core/modal.js';
import { notify } from '../core/toast.js';
import { guardPrinter } from '../core/guards.js';

const PRESENCE_POLL_MS = 5000;
const RELEASE_PATH = (id) => `/release_order_lock/${id}`;
const REASON_CHOICES = [
  'Customer asked to cancel',
  'Wrong input',
  'Out of stock',
  'Other',
];

/* ---- Inline SVG set mirroring partials/_icons.html for JS-rendered rows. ---- */
function iconSvg(name, size = 14) {
  const SPECS = {
    'x': '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    'plus': '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    'minus': '<line x1="5" y1="12" x2="19" y2="12"/>',
    'trash': '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
    'search': '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    'user': '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    'lock': '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    'id-card': '<rect x="2" y="5" width="20" height="14" rx="2"/><circle cx="8" cy="11" r="2"/><path d="M5.5 16a3.5 3.5 0 0 1 5 0"/><line x1="14" y1="10" x2="19" y2="10"/><line x1="14" y1="14" x2="18" y2="14"/>',
    'key': '<path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/>',
    'grip-vertical': '<circle cx="9" cy="5" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="9" cy="19" r="1"/><circle cx="15" cy="5" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="15" cy="19" r="1"/>',
    'ban': '<circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>',
    'divide': '<circle cx="12" cy="6" r="2"/><line x1="5" y1="12" x2="19" y2="12"/><circle cx="12" cy="18" r="2"/>',
    'scissors': '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="20" y1="4" x2="8.12" y2="15.88"/><line x1="14.47" y1="14.48" x2="20" y2="20"/><line x1="8.12" y1="8.12" x2="12" y2="12"/>',
    'check': '<path d="M20 6L9 17l-5-5"/>',
    'alert-triangle': '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    'receipt': '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1-2-1z"/><line x1="8" y1="9" x2="16" y2="9"/><line x1="8" y1="13" x2="14" y2="13"/>',
    'printer': '<path d="M6 9V2h12v7"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/>',
    'maximize-2': '<polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>',
  };
  return `<svg class="icon icon-${name}" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SPECS[name] || ''}</svg>`;
}

/* ---- Module state (seeded per mount) --------------------------------------- */
let ORDER = null;           // odOrderBridge
let SEED_ITEMS = [];        // odItemsBridge
let PRODUCTS = [];          // odProductsBridge
let presenceInterval = null;
let lockConflictHandled = false;
let splitBillAuth = null;   // {card_number} | {username, password} from auth
let openOverlays = [];
let overlaySeq = 0;
let rootEl = null;
let destroyFns = [];

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function money(n) {
  return `\u20B1${Number(n || 0).toFixed(2)}`;
}

function moneyPhp(n) {
  return '\u20b1' + Number(n).toLocaleString('en-PH', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/* ---- Inline overlay builder (njX idiom, mirrors pages/orders.js) ----------- */
function modalHost() {
  let el = document.getElementById('modal-host');
  if (!el) {
    el = document.createElement('div');
    el.id = 'modal-host';
    document.body.appendChild(el);
  }
  return el;
}

function openOverlay({ title, bodyHtml, className = '', dismissOnOverlay = true, dismissOnEscape = true }) {
  const overlay = document.createElement('div');
  overlay.className = 'lib-modal-overlay ' + className;
  overlay.id = 'od-modal-' + (++overlaySeq);
  const box = document.createElement('div');
  box.className = 'lib-modal-box od-modal ' + className;
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  box.setAttribute('aria-labelledby', overlay.id + '-title');
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'lib-modal-close';
  closeBtn.setAttribute('aria-label', 'Close');
  closeBtn.textContent = '\u2715';
  if (title) {
    const h = document.createElement('h3');
    h.id = overlay.id + '-title';
    h.className = 'lib-modal-title';
    h.textContent = title;
    box.appendChild(h);
  }
  const body = document.createElement('div');
  body.className = 'lib-modal-text od-modal-body';
  body.innerHTML = bodyHtml;
  box.appendChild(body);
  box.appendChild(closeBtn);
  overlay.appendChild(box);
  modalHost().appendChild(overlay);
  openOverlays.push(overlay);

  const close = () => {
    const i = openOverlays.indexOf(overlay);
    if (i >= 0) openOverlays.splice(i, 1);
    window.closeModal(overlay);
    overlay.remove();
  };
  overlay.addEventListener('click', (e) => {
    if (dismissOnOverlay && e.target === overlay) close();
  });
  closeBtn.addEventListener('click', close);
  if (dismissOnEscape) {
    const onKey = (e) => {
      if (e.key === 'Escape' && overlay.classList.contains('open')) close();
    };
    document.addEventListener('keydown', onKey);
    destroyFns.push(() => document.removeEventListener('keydown', onKey));
  }
  window.openModal(overlay.id);
  return { overlay, box, close };
}

function closeAllOverlays() {
  openOverlays.slice().forEach((o) => {
    window.closeModal(o);
    o.remove();
  });
  openOverlays = [];
}

/* ---- Presence lock -----------------------------------------------------------
   POST /order_presence/<id> every 5s; a 409 (lock_conflict) freezes the page
   behind the V1 "Order In Use" notice. The lock is released with a sendBeacon
   on pagehide/beforeunload/visibilitychange (V1 parity). -------------------- */
function showLockModal(message) {
  if (lockConflictHandled) return;
  lockConflictHandled = true;
  if (presenceInterval) {
    clearInterval(presenceInterval);
    presenceInterval = null;
  }
  if (rootEl) rootEl.classList.add('od-page-locked');
  modal.open({
    title: 'Order In Use',
    text: message || 'Order is currently being processed by another user. Please wait until they finish.',
    buttons: [{ label: 'OK', value: true, variant: 'btn-error', autofocus: true }],
  });
}

async function sendPresence() {
  try {
    await api.post(`/order_presence/${ORDER.id}`, {});
    return true;
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      showLockModal(err.message);
    }
    return false;
  }
}

function releasePresence() {
  if (presenceInterval) {
    clearInterval(presenceInterval);
    presenceInterval = null;
  }
  const url = RELEASE_PATH(ORDER.id);
  const payload = JSON.stringify({ csrf_token: getCsrfToken() });
  if (navigator.sendBeacon) {
    navigator.sendBeacon(url, new Blob([payload], { type: 'application/json' }));
  } else {
    fetch(url, {
      method: 'POST',
      keepalive: true,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: payload,
    });
  }
}

function onVisibilityChange() {
  if (document.hidden) releasePresence();
}

/* ---- Admin auth modal (V1 showAdminAuthModal port) ---------------------------
   Card-swipe panel with the V1 key-buffer detection (350ms window, >= 8
   digits, Enter or buffer overflow resolves { card_number }); "Use
   credentials" switches to username/password fields. Resolves the V1 auth
   payload shape, or null when dismissed. ------------------------------------- */
function showAdminAuthModal({ title, text, confirmLabel, accent = 'danger' } = {}) {
  return new Promise((resolve) => {
    let resolved = false;
    let mode = 'swipe';
    let keyBuffer = '';
    let lastKeyTime = 0;
    let swipeTimer = null;
    const SWIPE_TIMEOUT_MS = 350;
    const SWIPE_MIN_LENGTH = 8;

    let el = document.getElementById('modal-host');
    if (!el) {
      el = document.createElement('div');
      el.id = 'modal-host';
      document.body.appendChild(el);
    }

    const overlay = document.createElement('div');
    overlay.className = 'lib-modal-overlay';
    overlay.id = 'od-admin-auth-' + (++overlaySeq);

    const box = document.createElement('div');
    box.className = 'lib-modal-box is-warning lib-modal-warning';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', overlay.id + '-title');

    box.innerHTML = `
      <h3 class="lib-modal-title lib-modal-warning-title" id="${overlay.id}-title">
        <span class="lib-modal-warning-icon"><svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></span>
        ${esc(title || 'Authentication Required')}
      </h3>
      <p class="lib-modal-text lib-modal-warning-text">${esc(text || 'Swipe an admin or manager card, or enter credentials to continue.')}</p>
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
          <label for="${overlay.id}-user">Admin/Manager Username</label>
          <input type="text" id="${overlay.id}-user" name="username" placeholder="Enter username" autocomplete="username">
        </div>
        <div class="auth-field">
          <label for="${overlay.id}-pass">Admin/Manager Password</label>
          <input type="password" id="${overlay.id}-pass" name="password" placeholder="Enter password" autocomplete="current-password">
        </div>
      </form>
      <div class="lib-modal-warning-actions" style="margin-top:0.75rem;">
        <button type="button" class="btn lib-modal-warning-cancel" data-auth-action="cancel">Cancel</button>
        <button type="button" class="btn ${accent === 'success' ? 'btn-warning' : 'btn-error'}" data-auth-action="confirm">${esc(confirmLabel || 'Authenticate')}</button>
      </div>`;

    overlay.appendChild(box);
    el.appendChild(overlay);

    const finish = (value) => {
      if (resolved) return;
      resolved = true;
      document.removeEventListener('keydown', onKeyDown);
      if (swipeTimer) clearTimeout(swipeTimer);
      if (window.closeModal) window.closeModal(overlay);
      overlay.remove();
      resolve(value);
    };

    function extractCardNumber(buffer) {
      const digitsOnly = String(buffer).replace(/\D/g, '');
      return digitsOnly.length >= SWIPE_MIN_LENGTH ? digitsOnly : null;
    }

    function onSwipeDetected(raw) {
      const cardNum = extractCardNumber(raw);
      if (!cardNum) return;
      finish({ card_number: cardNum });
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
          const first = box.querySelector(`#${overlay.id}-user`);
          if (first) first.focus();
        }
        return;
      }
      if (e.target.closest('[data-auth-action="cancel"]')) {
        finish(null);
        return;
      }
      if (e.target.closest('[data-auth-action="confirm"]')) {
        if (mode === 'credentials') {
          const username = box.querySelector(`#${overlay.id}-user`).value.trim();
          const password = box.querySelector(`#${overlay.id}-pass`).value;
          if (!username || !password) {
            notify.error('Please enter both username and password.');
            return;
          }
          finish({ username, password });
        } else {
          finish(null);
        }
      }
    });

    const userInput = box.querySelector(`#${overlay.id}-user`);
    const passInput = box.querySelector(`#${overlay.id}-pass`);
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
      const username = box.querySelector(`#${overlay.id}-user`).value.trim();
      const password = box.querySelector(`#${overlay.id}-pass`).value;
      if (!username || !password) {
        notify.error('Please enter both username and password.');
        return;
      }
      finish({ username, password });
    });

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) finish(null);
    });
    document.addEventListener('keydown', onKeyDown);
    if (window.openModal) window.openModal(overlay.id);
  });
}

/* ---- Reason select modal ----------------------------------------------------- */
function askReason(title, promptText) {
  return new Promise((resolve) => {
    let el = document.getElementById('modal-host');
    if (!el) {
      el = document.createElement('div');
      el.id = 'modal-host';
      document.body.appendChild(el);
    }

    const overlay = document.createElement('div');
    overlay.className = 'lib-modal-overlay';
    overlay.id = 'od-reason-modal-' + (++overlaySeq);

    const box = document.createElement('div');
    box.className = 'lib-modal-box is-warning lib-modal-warning';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', overlay.id + '-title');

    const options = REASON_CHOICES.map((r) => `<option value="${esc(r)}">${esc(r)}</option>`).join('');

    box.innerHTML = `
      <h3 class="lib-modal-title lib-modal-warning-title" id="${overlay.id}-title">
        <span class="lib-modal-warning-icon">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
            <line x1="12" y1="9" x2="12" y2="13"/>
            <line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
        </span>
        ${esc(title || 'Confirm Action')}
      </h3>
      <p class="lib-modal-text lib-modal-warning-text">${esc(promptText || 'Are you sure you want to proceed?')}</p>

      <div class="auth-form" style="margin-top:0.75rem;">
        <div class="auth-field">
          <label for="${overlay.id}-select">Reason for Cancellation</label>
          <select id="${overlay.id}-select" style="width:100%; padding:0.55rem 0.75rem; background:var(--bg-card, rgba(255,255,255,0.04)); border:1px solid var(--border-color); border-radius:var(--radius-md); color:var(--text-main); font-family:var(--font-mono); font-size:0.85rem; outline:none;">
            ${options}
          </select>
        </div>
        <div class="auth-field" id="${overlay.id}-other-wrap" style="display:none; margin-top:0.4rem;">
          <label for="${overlay.id}-other">Specify Other Reason</label>
          <input type="text" id="${overlay.id}-other" placeholder="Type reason here..." style="width:100%; padding:0.55rem 0.75rem; background:var(--bg-card, rgba(255,255,255,0.04)); border:1px solid var(--border-color); border-radius:var(--radius-md); color:var(--text-main); font-family:var(--font-mono); font-size:0.85rem; outline:none;">
        </div>
      </div>

      <div class="lib-modal-warning-actions" style="margin-top:1rem;">
        <button type="button" class="btn lib-modal-warning-cancel" data-reason-action="cancel">Cancel</button>
        <button type="button" class="btn btn-warning" data-reason-action="confirm">Confirm</button>
      </div>`;

    overlay.appendChild(box);
    el.appendChild(overlay);

    let settled = false;
    function finish(value) {
      if (settled) return;
      settled = true;
      if (window.closeModal) window.closeModal(overlay);
      overlay.remove();
      resolve(value);
    }

    const selectEl = box.querySelector(`#${overlay.id}-select`);
    const otherWrap = box.querySelector(`#${overlay.id}-other-wrap`);
    const otherInput = box.querySelector(`#${overlay.id}-other`);

    selectEl.addEventListener('change', () => {
      const isOther = selectEl.value === 'Other';
      otherWrap.style.display = isOther ? 'flex' : 'none';
      if (isOther && otherInput) {
        otherInput.focus();
      }
    });

    box.addEventListener('click', (e) => {
      if (e.target.closest('[data-reason-action="cancel"]')) {
        finish(null);
        return;
      }
      if (e.target.closest('[data-reason-action="confirm"]')) {
        const val = selectEl.value;
        if (val === 'Other') {
          const customReason = (otherInput ? otherInput.value : '').trim();
          if (!customReason) {
            notify.error('Please specify a reason for "Other".');
            if (otherInput) otherInput.focus();
            return;
          }
          finish(customReason);
        } else {
          finish(val);
        }
      }
    });

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) finish(null);
    });

    if (window.openModal) window.openModal(overlay.id);
  });
}

/* ---- Reprints / bill out ------------------------------------------------------ */
async function reprintInvoice(btn) {
  if (!btn || btn.dataset.inFlight === '1') return;
  btn.dataset.inFlight = '1';
  const original = btn.innerHTML;
  const printerReady = await guardPrinter();
  if (!printerReady) {
    delete btn.dataset.inFlight;
    return;
  }
  btn.disabled = true;
  btn.innerHTML = `<span>Printing…</span>`;
  try {
    const data = await api.post(`/reprint_receipt/${ORDER.id}`, {});
    if (data && data.deduped) return;
    if (data && data.success) {
      notify.success(data.message || 'Invoice reprinted successfully!');
    } else {
      notify.error(data.message || 'Failed to reprint invoice');
    }
  } catch (err) {
    console.error('Error reprinting invoice:', err);
    notify.error(err.message || 'Error reprinting invoice');
  } finally {
    btn.disabled = false;
    btn.innerHTML = original;
    delete btn.dataset.inFlight;
    loadTimeline();
  }
}

async function reprintKitchenSlip(btn) {
  if (!btn || btn.dataset.inFlight === '1') return;
  btn.dataset.inFlight = '1';
  const original = btn.innerHTML;
  const printerReady = await guardPrinter();
  if (!printerReady) {
    delete btn.dataset.inFlight;
    return;
  }
  btn.disabled = true;
  btn.innerHTML = `<span>Printing…</span>`;
  try {
    const data = await api.post(`/reprint_kitchen_slip/${ORDER.id}`, {});
    if (data && data.deduped) return;
    if (data && data.success) {
      notify.success(data.message || 'Kitchen order slip reprinted successfully');
    } else {
      const msg = (data && data.message) || 'Failed to reprint kitchen order slip';
      modal.error('Printer Error', msg);
      notify.error(msg);
    }
  } catch (err) {
    console.error('Error reprinting kitchen order slip:', err);
    const msg = err.message || 'Failed to reprint kitchen order slip';
    modal.error('Printer Error', msg);
    notify.error(msg);
  } finally {
    btn.disabled = false;
    btn.innerHTML = original;
    delete btn.dataset.inFlight;
    loadTimeline();
  }
}

async function billOut(btn) {
  if (!btn || btn.dataset.inFlight === '1') return;
  btn.dataset.inFlight = '1';
  const original = btn.innerHTML;
  const printerReady = await guardPrinter();
  if (!printerReady) {
    delete btn.dataset.inFlight;
    return;
  }
  btn.disabled = true;
  btn.innerHTML = `<span>Bill Out…</span>`;
  try {
    const data = await api.post(`/bill_out/${ORDER.id}`, {});
    if (data && data.success) {
      notify.success(data.message || 'Bill printed successfully');
    } else {
      const msg = (data && data.message) || 'Failed to print bill — Printer may be busy or disconnected.';
      modal.error('Printer Error', msg);
      notify.error(msg);
    }
  } catch (err) {
    console.error('Error generating bill:', err);
    const msg = err.message || 'Failed to print bill — Printer may be busy or disconnected.';
    modal.error('Printer Error', msg);
    notify.error(msg);
  } finally {
    btn.disabled = false;
    btn.innerHTML = original;
    delete btn.dataset.inFlight;
    loadTimeline();
  }
}

/* ---- Cancel order flow --------------------------------------------------------
   V1 startCancelOrderFlow: admin auth (/cancel_order_auth), reason select,
   printer guard, /confirm_cancel_order { reason }, redirect /orders. ------- */
async function startCancelOrderFlow(triggerBtn) {
  const auth = await showAdminAuthModal({
    title: 'Authentication Required',
    text: 'Swipe an admin or manager card to cancel this order.',
    confirmLabel: 'Authenticate',
    accent: 'danger',
  });
  if (!auth) return;

  try {
    const authRes = await api.post(`/cancel_order_auth/${ORDER.id}`, auth);
    if (!authRes || !authRes.success) {
      notify.error((authRes && authRes.message) || 'Invalid admin/manager credentials.');
      return;
    }
  } catch (err) {
    if (err instanceof ApiError && err.status === 409 && err.detail && err.detail.lock_conflict) {
      showLockModal(err.message);
      return;
    }
    notify.error((err instanceof ApiError && err.message) || 'Authentication failed. Please try again.');
    return;
  }

  const reason = await askReason('Confirm Cancel Order', 'Are you sure you want to cancel this order? All items will be removed and this action cannot be undone.');
  if (!reason) return;

  const printerConfirmed = await guardPrinter();
  if (!printerConfirmed) return;

  if (triggerBtn) {
    triggerBtn.disabled = true;
    triggerBtn.innerHTML = `<span>Processing…</span>`;
  }
  try {
    const data = await api.post(`/confirm_cancel_order/${ORDER.id}`, { reason });
    if (data && data.success) {
      notify.success(data.message || 'Order cancelled successfully.');
      setTimeout(() => { window.location.href = '/orders'; }, 900);
    } else {
      notify.error((data && data.message) || 'Failed to cancel order.');
    }
  } catch (err) {
    if (err instanceof ApiError && err.status === 409 && err.detail && err.detail.lock_conflict) {
      showLockModal(err.message);
      return;
    }
    notify.error('Error cancelling order. Please try again.');
  } finally {
    if (triggerBtn) {
      triggerBtn.disabled = false;
      triggerBtn.innerHTML = triggerBtn.dataset.originalLabel || triggerBtn.innerHTML;
    }
  }
}

/* Item management removed — Modify now redirects to /add-items?order_id=<id>
   which renders the POS register in add-items mode (same as legacy V1). */

/* ---- Split bill zones ---------------------------------------------------------
   V1 split IIFE port: drag cards between two zones (pointer-drag ghost,
   qtyToMove = 1 partial-split merge), Confirm posts /split_bill with
   { split_items: [{order_item_id, quantity}] }, Cancel Item(s) posts
   /cancel_split_items with { cancel_items, reason, ...splitBillAuth }.
   Both flows require admin/manager auth via /modify_order first. ---------- */
let leftItems = [];
let rightItems = [];
let splitDragSrc = null;
let splitPointerDrag = null;
let splitOverlayCtl = null;

function splitCalcTotals(items) {
  const total = items.reduce((s, i) => s + (Number(i.price) || 0) * (Number(i.quantity) || 0), 0);
  const vat = total * 0.12 / 1.12;
  return { total, vat, subtotal: total - vat };
}

function updateSplitTotals() {
  const l = splitCalcTotals(leftItems);
  const r = splitCalcTotals(rightItems);
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = moneyPhp(v); };
  set('odSplitLeftSubtotal', l.subtotal);
  set('odSplitLeftVat', l.vat);
  set('odSplitLeftTotal', l.total);
  set('odSplitRightSubtotal', r.subtotal);
  set('odSplitRightVat', r.vat);
  set('odSplitRightTotal', r.total);
  const confirmBtn = document.getElementById('odSplitConfirmBtn');
  const cancelBtn = document.getElementById('odSplitCancelItemsBtn');
  if (confirmBtn) confirmBtn.disabled = rightItems.length === 0;
  if (cancelBtn) cancelBtn.disabled = rightItems.length === 0;
}

function makeSplitCard(item, side) {
  const card = document.createElement('div');
  card.className = 'od-split-card';
  card.dataset.id = String(item.id);
  card.dataset.side = side;
  card.style.touchAction = 'none';
  card.innerHTML = `
    <span class="od-split-qty">${esc(item.quantity)}x</span>
    <div class="od-split-info">
      <div class="od-split-name">${esc(item.product_name)}${item.modifier ? ` <em>(${esc(item.modifier)})</em>` : ''}</div>
      <div class="od-split-sub">${moneyPhp(item.price)} each — ${moneyPhp(item.price * item.quantity)}</div>
    </div>
    <button type="button" class="od-split-move-btn" title="${side === 'left' ? 'Move to New Order' : 'Return to Original'}" aria-label="Move item">
      ${iconSvg(side === 'left' ? 'scissors' : 'plus', 14)}
    </button>
    <span class="od-split-grip">${iconSvg('grip-vertical', 14)}</span>`;

  const moveBtn = card.querySelector('.od-split-move-btn');
  if (moveBtn) {
    moveBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      e.preventDefault();
      moveSplitItem({ side, id: String(item.id) }, side === 'left' ? 'right' : 'left');
    });
  }

  card.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.od-split-move-btn')) return;
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    startSplitPointerDrag(e, card, item, side);
  });

  return card;
}

function renderSplitZone(side) {
  const zone = document.getElementById(side === 'left' ? 'odSplitLeftZone' : 'odSplitRightZone');
  if (!zone) return;
  const items = side === 'left' ? leftItems : rightItems;
  zone.querySelectorAll('.od-split-card').forEach((c) => c.remove());
  items.forEach((item) => zone.appendChild(makeSplitCard(item, side)));
  const placeholder = document.getElementById('odSplitRightPlaceholder');
  if (placeholder) placeholder.style.display = rightItems.length === 0 ? 'flex' : 'none';
  updateSplitTotals();
}

function findSplitItem(arr, id) { return arr.find((i) => String(i.id) === String(id)); }
function removeSplitItem(arr, id) { const idx = arr.findIndex((i) => String(i.id) === String(id)); if (idx > -1) arr.splice(idx, 1); }

function moveSplitItem(source, targetSide) {
  if (!source || source.side === targetSide) return false;
  const srcItems = source.side === 'left' ? leftItems : rightItems;
  const dstItems = targetSide === 'left' ? leftItems : rightItems;
  const item = findSplitItem(srcItems, source.id);
  if (!item) return false;
  const qtyToMove = 1;
  if (qtyToMove < item.quantity) {
    const existing = findSplitItem(dstItems, item.id);
    if (existing) existing.quantity += qtyToMove;
    else dstItems.push(Object.assign({}, item, { quantity: qtyToMove }));
    item.quantity -= qtyToMove;
  } else {
    removeSplitItem(srcItems, item.id);
    const existing = findSplitItem(dstItems, item.id);
    if (existing) existing.quantity += qtyToMove;
    else dstItems.push(Object.assign({}, item, { quantity: qtyToMove }));
  }
  renderSplitZone('left');
  renderSplitZone('right');
  return true;
}

function getSplitSideFromPoint(clientX, clientY) {
  const el = document.elementFromPoint(clientX, clientY);
  const zone = el ? el.closest('#odSplitLeftZone, #odSplitRightZone') : null;
  if (!zone) return null;
  return zone.id === 'odSplitLeftZone' ? 'left' : 'right';
}

function setSplitDropHighlight(side) {
  const leftZone = document.getElementById('odSplitLeftZone');
  const rightZone = document.getElementById('odSplitRightZone');
  if (leftZone) leftZone.classList.toggle('is-drop-target', side === 'left');
  if (rightZone) rightZone.classList.toggle('is-drop-target', side === 'right');
}

function startSplitPointerDrag(e, card, item, side) {
  if (splitPointerDrag) finishSplitPointerDrag();
  if (e.cancelable) e.preventDefault();

  splitDragSrc = { side, id: String(item.id) };
  const rect = card.getBoundingClientRect();

  const getPos = (ev) => {
    if (ev.touches && ev.touches.length > 0) return { x: ev.touches[0].clientX, y: ev.touches[0].clientY };
    if (ev.changedTouches && ev.changedTouches.length > 0) return { x: ev.changedTouches[0].clientX, y: ev.changedTouches[0].clientY };
    return { x: ev.clientX, y: ev.clientY };
  };

  const startPos = getPos(e);
  const offsetX = startPos.x - rect.left;
  const offsetY = startPos.y - rect.top;

  const ghost = card.cloneNode(true);
  ghost.classList.add('od-split-ghost');
  ghost.style.position = 'fixed';
  ghost.style.top = '0px';
  ghost.style.left = '0px';
  ghost.style.width = rect.width + 'px';
  ghost.style.margin = '0';
  ghost.style.zIndex = '999999';
  ghost.style.pointerEvents = 'none';
  ghost.style.willChange = 'transform';

  const initialX = startPos.x - offsetX;
  const initialY = startPos.y - offsetY;
  ghost.style.transform = `translate3d(${initialX}px, ${initialY}px, 0)`;

  document.body.appendChild(ghost);
  card.classList.add('is-dragging');

  let rafId = null;
  let latestX = startPos.x;
  let latestY = startPos.y;

  const updateGhostPosition = () => {
    if (!splitPointerDrag) return;
    const x = latestX - offsetX;
    const y = latestY - offsetY;
    ghost.style.transform = `translate3d(${x}px, ${y}px, 0)`;
    setSplitDropHighlight(getSplitSideFromPoint(latestX, latestY));
    rafId = null;
  };

  const onMove = (moveEv) => {
    if (!splitPointerDrag) return;
    if (moveEv.cancelable) moveEv.preventDefault();
    const pos = getPos(moveEv);
    latestX = pos.x;
    latestY = pos.y;
    if (!rafId) {
      rafId = requestAnimationFrame(updateGhostPosition);
    }
  };

  const onEnd = (upEv) => {
    if (!splitPointerDrag) return;
    if (upEv.cancelable) upEv.preventDefault();
    const pos = getPos(upEv);
    const targetSide = getSplitSideFromPoint(pos.x, pos.y);
    if (targetSide) moveSplitItem(splitPointerDrag.source, targetSide);
    finishSplitPointerDrag();
  };

  splitPointerDrag = {
    source: splitDragSrc,
    card,
    ghost,
    cleanup: () => {
      if (rafId) cancelAnimationFrame(rafId);
      window.removeEventListener('pointermove', onMove, true);
      window.removeEventListener('pointerup', onEnd, true);
      window.removeEventListener('pointercancel', onEnd, true);
      window.removeEventListener('mousemove', onMove, true);
      window.removeEventListener('mouseup', onEnd, true);
      window.removeEventListener('touchmove', onMove, true);
      window.removeEventListener('touchend', onEnd, true);
      window.removeEventListener('touchcancel', onEnd, true);
    }
  };

  window.addEventListener('pointermove', onMove, { capture: true, passive: false });
  window.addEventListener('pointerup', onEnd, { capture: true, passive: false });
  window.addEventListener('pointercancel', onEnd, { capture: true, passive: false });
  window.addEventListener('mousemove', onMove, { capture: true, passive: false });
  window.addEventListener('mouseup', onEnd, { capture: true, passive: false });
  window.addEventListener('touchmove', onMove, { capture: true, passive: false });
  window.addEventListener('touchend', onEnd, { capture: true, passive: false });
  window.addEventListener('touchcancel', onEnd, { capture: true, passive: false });
}

function finishSplitPointerDrag() {
  setSplitDropHighlight(null);
  if (splitPointerDrag) {
    if (splitPointerDrag.cleanup) splitPointerDrag.cleanup();
    splitPointerDrag.card.classList.remove('is-dragging');
    if (splitPointerDrag.ghost) splitPointerDrag.ghost.remove();
  }
  splitPointerDrag = null;
  splitDragSrc = null;
}

function onSplitPointerEnd(e) {
  if (!splitPointerDrag) return;
  e.preventDefault();
  const targetSide = getSplitSideFromPoint(e.clientX, e.clientY);
  if (targetSide) moveSplitItem(splitPointerDrag.source, targetSide);
  finishSplitPointerDrag();
}

function onSplitPointerCancel(e) {
  if (e) e.preventDefault();
  finishSplitPointerDrag();
}

async function confirmSplit(btn) {
  if (rightItems.length === 0) return;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span>Splitting…</span>';
  }
  const splitPayload = rightItems.map((i) => ({ order_item_id: i.id, quantity: i.quantity }));
  try {
    const data = await api.post(`/split_bill/${ORDER.id}`, { split_items: splitPayload });
    if (data && data.success) {
      closeAllOverlays();
      const go = await modal.confirm(
        'Bill Split!',
        `Original: ${data.original_order_no}\nNew: ${data.new_order_no}\n\nBoth orders are now pending on the same table.`,
        { confirmLabel: 'View New Order', cancelLabel: 'Stay Here' }
      );
      if (go) window.location.href = `/order/${data.new_order_id}`;
      else window.location.reload();
    } else {
      notify.error((data && data.message) || 'Split failed');
    }
  } catch (err) {
    notify.error('Error: ' + ((err instanceof ApiError && err.message) || 'split failed'));
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `${iconSvg('scissors', 14)}<span>Confirm Split</span>`;
    }
  }
}

async function cancelSplitItems(btn) {
  if (rightItems.length === 0) return;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span>Cancelling…</span>';
  }
  const cancelPayload = rightItems.map((i) => ({ order_item_id: i.id, quantity: i.quantity }));
  const reason = await askReason('Cancel Items', `Cancel ${rightItems.length} item(s) from this order?`);
  if (!reason) {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `${iconSvg('ban', 14)}<span>Cancel Item(s)</span>`;
    }
    return;
  }
  const printerConfirmed = await guardPrinter();
  if (!printerConfirmed) {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `${iconSvg('ban', 14)}<span>Cancel Item(s)</span>`;
    }
    return;
  }
  try {
    const cancelData = await api.post(`/cancel_split_items/${ORDER.id}`, Object.assign(
      { cancel_items: cancelPayload, reason },
      splitBillAuth || {}
    ));
    if (cancelData && cancelData.success) {
      closeAllOverlays();
      await modal.success(
        'Items Cancelled',
        `${cancelData.cancelled_count} item(s) removed from order\nCancel slip printed.\n\nRemaining total: ${moneyPhp(cancelData.new_order_total || 0)}`
      );
      window.location.reload();
    } else {
      notify.error((cancelData && cancelData.message) || 'Cancellation failed');
    }
  } catch (err) {
    notify.error('Error: ' + ((err instanceof ApiError && err.message) || 'cancellation failed'));
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `${iconSvg('ban', 14)}<span>Cancel Item(s)</span>`;
    }
  }
}

async function openSplitBill(triggerBtn) {
  const auth = await showAdminAuthModal({
    title: 'Authentication Required',
    text: 'Swipe an admin or manager card to split this order.',
    confirmLabel: 'Authenticate',
    accent: 'success',
  });
  if (!auth) return;
  try {
    const data = await api.post(`/modify_order/${ORDER.id}`, auth.card_number
      ? { card_number: auth.card_number }
      : { username: auth.username, password: auth.password });
    if (!data || !data.success) {
      notify.error((data && data.message) || 'Invalid admin/manager credentials.');
      return;
    }
  } catch (err) {
    if (err instanceof ApiError && err.status === 409 && err.detail && err.detail.lock_conflict) {
      showLockModal(err.message);
      return;
    }
    notify.error('Authentication failed. Please try again.');
    return;
  }
  notify.success('Authentication successful.');
  splitBillAuth = auth.card_number
    ? { card_number: auth.card_number }
    : { username: auth.username, password: auth.password };

  leftItems = SEED_ITEMS.map((i) => ({
    id: i.id,
    product_name: i.product_name,
    price: Number(i.price),
    quantity: Number(i.quantity),
    modifier: i.modifier || null
  }));
  rightItems = [];

  let el = document.getElementById('modal-host');
  if (!el) {
    el = document.createElement('div');
    el.id = 'modal-host';
    document.body.appendChild(el);
  }

  const overlay = document.createElement('div');
  overlay.className = 'lib-modal-overlay';
  overlay.id = 'od-split-overlay-' + (++overlaySeq);

  const box = document.createElement('div');
  box.className = 'lib-modal-box is-warning lib-modal-warning od-split-modal-box';
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  box.style.cssText = 'width:92vw; max-width:840px; max-height:88vh; padding:0; background:var(--bg-card, #151515); border:1px solid var(--border-color, #2a2a3e); border-radius:var(--radius-lg, 16px); display:flex; flex-direction:column; overflow:hidden;';

  box.innerHTML = `
    <div style="padding:1rem 1.25rem; border-bottom:1px solid var(--border-color, #2a2a3e); display:flex; align-items:center; gap:0.65rem;">
      <span class="lib-modal-warning-icon" style="width:2.2rem; height:2.2rem; border-radius:50%; background:rgba(255, 221, 0, 0.12); color:var(--color-warning, #ffdd00); display:inline-flex; align-items:center; justify-content:center; flex-shrink:0;">
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="6" r="2"/><line x1="5" y1="12" x2="19" y2="12"/><circle cx="12" cy="18" r="2"/></svg>
      </span>
      <div>
        <h3 class="lib-modal-title lib-modal-warning-title" style="margin:0; font-size:1.05rem; font-family:var(--font-mono); font-weight:700;">Split Bill — Order #${esc(ORDER.order_no)}</h3>
        <p class="lib-modal-text lib-modal-warning-text" style="margin:0.2rem 0 0; font-size:0.78rem; font-family:var(--font-mono);">Drag items between zones to split into a new order or cancel items.</p>
      </div>
    </div>

    <div class="od-split-zones" style="flex:1; min-height:0; overflow-y:auto; padding:1rem 1.25rem; display:grid; grid-template-columns:1fr 1fr; gap:1.25rem;">
      <div>
        <div class="od-split-zone-head">
          <span class="od-split-title">${iconSvg('receipt', 14)} Original — #${esc(ORDER.order_no)}</span>
        </div>
        <div class="od-split-zone" id="odSplitLeftZone"></div>
        <div class="od-split-zone-foot">
          <div><span>Subtotal</span><span id="odSplitLeftSubtotal">₱0.00</span></div>
          <div><span>VAT</span><span id="odSplitLeftVat">₱0.00</span></div>
          <div class="od-split-total"><span>Total</span><span id="odSplitLeftTotal">₱0.00</span></div>
        </div>
      </div>
      <div>
        <div class="od-split-zone-head">
          <span class="od-split-title">${iconSvg('divide', 14)} New Order</span>
          <span class="od-split-hint">Drag items here</span>
        </div>
        <div class="od-split-zone" id="odSplitRightZone">
          <div class="od-split-placeholder" id="odSplitRightPlaceholder">
            ${iconSvg('grip-vertical', 22)} Drag items to split into a new order
          </div>
        </div>
        <div class="od-split-zone-foot">
          <div><span>Subtotal</span><span id="odSplitRightSubtotal">₱0.00</span></div>
          <div><span>VAT</span><span id="odSplitRightVat">₱0.00</span></div>
          <div class="od-split-total"><span>Total</span><span id="odSplitRightTotal">₱0.00</span></div>
        </div>
      </div>
    </div>

    <div class="lib-modal-warning-actions" style="padding:0.85rem 1.25rem; border-top:1px solid var(--border-color, #2a2a3e); background:var(--bg-secondary, #12121f); display:flex; gap:0.6rem;">
      <button type="button" class="btn lib-modal-warning-cancel" data-od-split-close style="flex:1;">Close</button>
      <button type="button" class="btn btn-warning" id="odSplitCancelItemsBtn" disabled style="flex:1; background:var(--color-error, #ef4444); color:#fff; border:none;">${iconSvg('ban', 14)}<span>Cancel Item(s)</span></button>
      <button type="button" class="btn btn-primary" id="odSplitConfirmBtn" disabled style="flex:1; background:var(--color-success, #22c55e); color:#ffffff; border:none; font-weight:700;">${iconSvg('scissors', 14)}<span>Confirm Split</span></button>
    </div>`;

  overlay.appendChild(box);
  el.appendChild(overlay);
  openOverlays.push(overlay);

  splitOverlayCtl = {
    overlay,
    box,
    close: () => {
      const i = openOverlays.indexOf(overlay);
      if (i >= 0) openOverlays.splice(i, 1);
      if (window.closeModal) window.closeModal(overlay);
      overlay.remove();
    }
  };

  renderSplitZone('left');
  renderSplitZone('right');

  box.addEventListener('click', (e) => {
    const confirmBtn = e.target.closest('#odSplitConfirmBtn');
    if (confirmBtn) { confirmSplit(confirmBtn); return; }
    const cancelBtn = e.target.closest('#odSplitCancelItemsBtn');
    if (cancelBtn) { cancelSplitItems(cancelBtn); return; }
    if (e.target.closest('[data-od-split-close]')) splitOverlayCtl.close();
  });

  if (window.openModal) window.openModal(overlay.id);
}

/* ---- OrderAuditLog timeline ---------------------------------------------------
   Spec addition (see .ulpi/design/pos-cashier-core.md): fetched from the
   read-only /api/order/<id>/audit feed, rendered oldest-first as a mono
   timeline. Event types follow the V1 audit vocabulary. --------------------- */
const TIMELINE_TYPES = {
  'Order Created': { label: 'Order Created', cls: 'od-tl-event-create' },
  'Order Modification': { label: 'Order Modified', cls: 'od-tl-event-mod' },
  'Bill Out': { label: 'Bill Out Printed', cls: 'od-tl-event-bill' },
  'Order Slip Reprinted': { label: 'Order Slip Reprinted', cls: 'od-tl-event-bill' },
  'Receipt Reprint': { label: 'Receipt Reprinted', cls: 'od-tl-event-bill' },
  'Settle Order': { label: 'Order Settled', cls: 'od-tl-event-settle' },
  'Payment': { label: 'Payment Received', cls: 'od-tl-event-settle' },
  'Cancel': { label: 'Order Cancelled', cls: 'od-tl-event-cancel' },
  'Void': { label: 'Order Voided', cls: 'od-tl-event-void' },
  'Refund': { label: 'Order Refunded', cls: 'od-tl-event-refund' },
};

function formatTimestamp(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

let CURRENT_TIMELINE_LOGS = [];

function renderTimelineItems(logs) {
  return logs.map((log) => {
    const type = TIMELINE_TYPES[log.event_type] || { label: log.event_type || 'Event', cls: '' };
    const ref = log.reference_no ? `<span class="od-tl-ref">${esc(log.reference_no)}</span>` : '';
    const detail = [];
    if (log.product_name) detail.push(log.product_name);
    if (log.reason) detail.push(log.reason);
    if (log.event_type === 'Order Modification' && log.original_quantity != null && log.modified_qty != null) {
      detail.push(`qty ${log.original_quantity} \u2192 ${log.modified_qty}`);
    }
    return `
      <li class="od-timeline-item ${type.cls}">
        <div class="od-tl-head">
          <span class="od-tl-type">${esc(type.label)}</span>
          <span class="od-tl-time">${esc(formatTimestamp(log.timestamp))}</span>
          ${ref}
        </div>
        ${detail.length ? `<div class="od-tl-desc">${esc(detail.join(' \u2014 '))}</div>` : ''}
        ${log.username ? `<div class="od-tl-user">by ${esc(log.username)}</div>` : ''}
      </li>`;
  }).join('');
}

async function loadTimeline() {
  const state = document.getElementById('odTimelineState');
  const list = document.getElementById('odTimelineList');
  let logs = [];
  try {
    const data = await api.get(`/api/order/${ORDER.id}/audit`);
    logs = (data && data.logs) || [];
    CURRENT_TIMELINE_LOGS = logs;
  } catch (err) {
    if (state) state.textContent = 'Unavailable';
    if (list) list.innerHTML = '<li class="od-timeline-empty">Timeline could not be loaded.</li>';
    return;
  }
  if (state) state.textContent = `${logs.length} event${logs.length === 1 ? '' : 's'}`;
  if (list) {
    if (logs.length === 0) {
      list.innerHTML = '<li class="od-timeline-empty" id="odTimelineEmpty">No events recorded for this order.</li>';
    } else {
      list.innerHTML = renderTimelineItems(logs);
    }
  }
}

function openTimelineZoomModal() {
  let el = document.getElementById('modal-host');
  if (!el) {
    el = document.createElement('div');
    el.id = 'modal-host';
    document.body.appendChild(el);
  }

  const overlay = document.createElement('div');
  overlay.className = 'lib-modal-overlay';
  overlay.id = 'od-timeline-zoom-modal-' + (++overlaySeq);

  const box = document.createElement('div');
  box.className = 'lib-modal-box od-timeline-modal-box';
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');

  const itemsHtml = CURRENT_TIMELINE_LOGS.length > 0
    ? renderTimelineItems(CURRENT_TIMELINE_LOGS)
    : '<li class="od-timeline-empty">No events recorded for this order.</li>';

  box.innerHTML = `
    <div class="od-card-header" style="padding:0.75rem 1rem; border-bottom:1px solid var(--border-color, #2a2a3e);">
      ${iconSvg('receipt', 16)}
      <h3 class="lib-modal-title" style="margin:0; font-size:0.95rem; font-family:var(--font-mono); font-weight:700;">Order History — #${esc(ORDER ? ORDER.order_no : '')}</h3>
    </div>
    <div class="od-timeline-modal-body" style="padding:0.65rem 0.85rem; max-height:55vh; overflow-y:auto;">
      <ol class="od-timeline" style="margin:0; padding:0; list-style:none; display:flex; flex-direction:column; gap:0.4rem;">
        ${itemsHtml}
      </ol>
    </div>
    <div class="lib-modal-actions" style="padding:0.5rem 1rem;">
      <button type="button" class="btn btn-ghost" style="width:100%; justify-content:center;" data-tl-modal-close>Close</button>
    </div>`;

  overlay.appendChild(box);
  el.appendChild(overlay);

  function close() {
    if (window.closeModal) window.closeModal(overlay);
    overlay.remove();
  }

  box.addEventListener('click', (e) => {
    if (e.target.closest('[data-tl-modal-close]')) close();
  });

  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) close();
  });

  if (window.openModal) window.openModal(overlay.id);
}

/* ---- Mount --------------------------------------------------------------------
   spa.js calls mount() with no arguments after every swap; destroy is
   optional (module state resets on the next mount). -------------------------- */
function mount() {
  rootEl = document.querySelector('#view-root');
  openOverlays = [];
  destroyFns = [];
  lockConflictHandled = false;
  splitBillAuth = null;
  presenceInterval = null;
  leftItems = [];
  rightItems = [];
  splitOverlayCtl = null;

  const bridge = rootEl && rootEl.querySelector('#odOrderBridge');
  if (!bridge) return;
  try {
    ORDER = JSON.parse(bridge.textContent);
    SEED_ITEMS = JSON.parse(rootEl.querySelector('#odItemsBridge').textContent) || [];
    PRODUCTS = JSON.parse(rootEl.querySelector('#odProductsBridge').textContent) || [];
  } catch (err) {
    console.error('order_details: failed to parse state bridges', err);
    return;
  }

  const isPending = ORDER.status === 'pending';

  /* Modify → <a> link to /add-items?order_id=<id> (POS register in add mode) */
  /* No JS handler needed — it's a plain link */

  const cancelBtn = rootEl.querySelector('#cancelOrderHeaderBtn');
  if (cancelBtn) cancelBtn.addEventListener('click', () => startCancelOrderFlow(cancelBtn));

  const reprintInvoiceBtn = rootEl.querySelector('#reprintInvoiceBtn');
  if (reprintInvoiceBtn) reprintInvoiceBtn.addEventListener('click', () => reprintInvoice(reprintInvoiceBtn));

  const voidBtn = rootEl.querySelector('#printVoidOrderBtn');
  if (voidBtn) {
    /* V1 parity: decorative — no void-slip reprint endpoint exists. */
    voidBtn.addEventListener('click', () => {
      voidBtn.disabled = true;
      voidBtn.innerHTML = '<span>Printing…</span>';
      setTimeout(() => {
        voidBtn.disabled = false;
        voidBtn.innerHTML = `${iconSvg('printer', 15)}<span>Print Void Slip</span>`;
      }, 900);
    });
  }

  const kitchenBtn = rootEl.querySelector('#reprintKitchenSlipBtn');
  if (kitchenBtn) kitchenBtn.addEventListener('click', () => reprintKitchenSlip(kitchenBtn));

  const billBtn = rootEl.querySelector('#billOutBtn');
  if (billBtn) billBtn.addEventListener('click', () => billOut(billBtn));

  const splitBtn = rootEl.querySelector('#splitBillBtn');
  if (splitBtn) splitBtn.addEventListener('click', () => openSplitBill(splitBtn));

  const backBtn = rootEl.querySelector('#odBackBtn');
  if (backBtn) {
    const ref = new URLSearchParams(window.location.search).get('ref');
    if (ref === 'all_orders' || document.referrer.includes('all_orders')) {
      backBtn.setAttribute('href', '/all_orders');
    }
  }

  const historyBtn = rootEl.querySelector('#odHistoryBtn');
  if (historyBtn) historyBtn.addEventListener('click', openTimelineZoomModal);

  const zoomBtn = rootEl.querySelector('#odTimelineZoomBtn');
  if (zoomBtn) zoomBtn.addEventListener('click', openTimelineZoomModal);

  loadTimeline();

  if (isPending) {
    sendPresence();
    presenceInterval = setInterval(sendPresence, PRESENCE_POLL_MS);
    const onHide = () => {
      if (document.hidden) {
        releasePresence();
      } else if (!lockConflictHandled && ORDER.status === 'pending') {
        /* Tab visible again: re-acquire the lock and restart the heartbeat
           (releasePresence() cleared the interval when the tab hid). */
        sendPresence();
        if (!presenceInterval) presenceInterval = setInterval(sendPresence, PRESENCE_POLL_MS);
      }
    };
    document.addEventListener('visibilitychange', onHide);
    window.addEventListener('pagehide', releasePresence);
    window.addEventListener('beforeunload', releasePresence);
    destroyFns.push(() => {
      clearInterval(presenceInterval);
      document.removeEventListener('visibilitychange', onHide);
      window.removeEventListener('pagehide', releasePresence);
      window.removeEventListener('beforeunload', releasePresence);
    });
  }
}

/* destroy() is required by the spa.js contract: without it the presence
   heartbeat keeps running after a boosted navigation away from this view,
   holding the order lock forever ("Order In Use" with nobody editing). */
function destroy() {
  if (ORDER && ORDER.status === 'pending' && !lockConflictHandled) {
    releasePresence();
  }
  destroyFns.forEach((fn) => {
    try {
      fn();
    } catch (err) {
      /* cleanup must never block the swap */
    }
  });
  destroyFns = [];
  presenceInterval = null;
}

export default { mount, destroy };
