/* =============================================================================
   core/modal.js — programmatic alert/confirm on the njX modal system
   -----------------------------------------------------------------------------
   Replaces the SweetAlert2 fire/confirm flows used across V1. Builds
   .lib-modal-overlay markup into #modal-host (created on demand; the Step 5
   shell may pre-render the empty host), drives it with the vendored njx.js
   openModal/closeModal, and resolves a Promise with the chosen button value.

   API
   ----
   modal.open({ title, text, buttons, timer })  -> Promise<value | null>
       buttons: [{ label, value, variant ('btn-primary'|'btn-error'|'btn-ghost'),
                   autofocus }]  — null = dismissed (Escape / overlay / close)
   modal.alert(title, text?, options?)           -> Promise<void>
   modal.confirm(title, text?, { danger, confirmLabel, cancelLabel })
                                                 -> Promise<boolean>
   modal.warning(title, text?, { confirmLabel, cancelLabel })
                                                 -> Promise<boolean>
   modal.error(title, text?, options?)            -> Promise<void>
   ========================================================================== */
import { api } from './api.js';

const BUTTON_DEFAULT = { label: 'OK', value: true, variant: 'btn-primary', autofocus: true };
const CANCEL = null;

let counter = 0;

function host() {
  let el = document.getElementById('modal-host');
  if (!el) {
    el = document.createElement('div');
    el.id = 'modal-host';
    document.body.appendChild(el);
  }
  return el;
}

function buildOverlay(config) {
  const overlay = document.createElement('div');
  overlay.className = 'lib-modal-overlay';
  overlay.id = 'modal-' + (++counter);

  const box = document.createElement('div');
  box.className = 'lib-modal-box';
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  box.setAttribute('aria-labelledby', overlay.id + '-title');

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'lib-modal-close';
  closeBtn.setAttribute('aria-label', 'Close');
  closeBtn.textContent = '\u2715';

  if (config.title) {
    const title = document.createElement('h3');
    title.id = overlay.id + '-title';
    title.className = 'lib-modal-title';
    title.textContent = config.title;
    box.appendChild(title);
  }
  if (config.text) {
    const text = document.createElement('p');
    text.className = 'lib-modal-text';
    text.textContent = config.text;
    box.appendChild(text);
  }

  const actions = document.createElement('div');
  actions.className = 'lib-modal-actions';
  config.buttons.forEach((button) => {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'btn ' + (button.variant || BUTTON_DEFAULT.variant);
    el.textContent = button.label;
    if (button.autofocus) el.setAttribute('data-autofocus', '1');
    actions.appendChild(el);
    el.addEventListener('click', () => resolve(button.value));
  });
  box.appendChild(actions);
  box.appendChild(closeBtn);
  overlay.appendChild(box);
  host().appendChild(overlay);

  let settled = false;
  function resolve(value) {
    if (settled) return;
    settled = true;
    window.closeModal(overlay);
    overlay.remove();
    document.removeEventListener('keydown', onKeydown);
    if (config.timer) clearTimeout(config.timer);
    config.onSettle(value);
  }
  function onKeydown(event) {
    if (event.key === 'Escape' && overlay.classList.contains('open')) {
      resolve(CANCEL);
    }
  }

  /* Overlay click (outside the box) and the close button both dismiss. */
  overlay.addEventListener('click', (event) => {
    if (event.target === overlay) resolve(CANCEL);
  });
  closeBtn.addEventListener('click', () => resolve(CANCEL));
  document.addEventListener('keydown', onKeydown);

  window.openModal(overlay.id);
  const focusTarget = actions.querySelector('[data-autofocus]');
  if (focusTarget) focusTarget.focus();
  return { overlay, resolve };
}

export function open(config) {
  return new Promise((onSettle) => {
    const pending = buildOverlay({
      title: config.title,
      text: config.text,
      buttons: (config.buttons || [BUTTON_DEFAULT]).map((b) =>
        Object.assign({}, BUTTON_DEFAULT, b)
      ),
      timer: config.timer,
      onSettle,
    });
    if (config.timer) {
      setTimeout(() => pending.resolve(CANCEL), config.timer);
    }
  });
}

export function alert(title, text, options = {}) {
  return open({
    title,
    text,
    buttons: [{ label: options.confirmLabel || 'OK', value: true, variant: 'btn-primary' }],
    timer: options.timer,
  }).then(() => undefined);
}

function escapeHtml(str) {
  return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export function confirm(title, text, options = {}) {
  return new Promise((onSettle) => {
    let el = document.getElementById('modal-host');
    if (!el) {
      el = document.createElement('div');
      el.id = 'modal-host';
      document.body.appendChild(el);
    }

    const overlay = document.createElement('div');
    overlay.className = 'lib-modal-overlay';
    overlay.id = 'modal-' + (++counter);

    const box = document.createElement('div');
    box.className = 'lib-modal-box is-confirm lib-modal-confirm';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', overlay.id + '-title');
    box.style.cssText = 'max-width: 360px; width: 100%; padding: 1.15rem 1.25rem; border-radius: 12px; box-sizing: border-box;';

    const isDanger = !!options.danger;
    const iconColor = isDanger ? 'var(--color-danger, #EF4444)' : 'var(--color-primary, #14B8A6)';
    const iconBg = isDanger ? 'rgba(239, 68, 68, 0.15)' : 'rgba(20, 184, 166, 0.15)';
    const iconSvg = isDanger
      ? `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`
      : `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`;

    if (title) {
      const titleEl = document.createElement('h3');
      titleEl.id = overlay.id + '-title';
      titleEl.className = 'lib-modal-title';
      titleEl.style.cssText = 'display:flex;align-items:center;margin-bottom:0.5rem;font-size:1.05rem;font-weight:600;';
      titleEl.innerHTML = `<span style="display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:50%;background:${iconBg};color:${iconColor};margin-right:0.65rem;flex-shrink:0;">${iconSvg}</span><span>${escapeHtml(title)}</span>`;
      box.appendChild(titleEl);
    }
    if (text) {
      const textEl = document.createElement('p');
      textEl.className = 'lib-modal-text';
      textEl.style.cssText = 'margin: 0.5rem 0 1rem 0; color: var(--text-muted); font-size: 0.875rem; line-height: 1.4;';
      textEl.textContent = text;
      box.appendChild(textEl);
    }

    const actions = document.createElement('div');
    actions.className = 'lib-modal-actions';
    actions.style.cssText = 'display: flex; gap: 0.5rem; width: 100%; margin-top: 0.75rem;';

    const confirmBtn = document.createElement('button');
    confirmBtn.type = 'button';
    confirmBtn.className = 'btn ' + (isDanger ? 'btn-error' : 'btn-primary');
    confirmBtn.textContent = options.confirmLabel || 'Confirm';
    confirmBtn.style.cssText = 'flex: 1; width: 50%; justify-content: center; text-align: center; margin: 0; padding: 0.6rem 0; font-weight: 600; border-radius: 6px;';

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-ghost';
    cancelBtn.textContent = options.cancelLabel || 'Cancel';
    cancelBtn.style.cssText = 'flex: 1; width: 50%; justify-content: center; text-align: center; margin: 0; padding: 0.6rem 0; font-weight: 600; border-radius: 6px; border: 1px solid var(--border-default, #334155); background: transparent; color: var(--text-primary);';

    actions.appendChild(confirmBtn);
    actions.appendChild(cancelBtn);
    box.appendChild(actions);

    overlay.appendChild(box);
    el.appendChild(overlay);

    let settled = false;
    function resolve(value) {
      if (settled) return;
      settled = true;
      window.closeModal(overlay);
      overlay.remove();
      document.removeEventListener('keydown', onKeydown);
      onSettle(value);
    }
    function onKeydown(event) {
      if (event.key === 'Escape' && overlay.classList.contains('open')) {
        resolve(false);
      } else if (event.key === 'Enter' && overlay.classList.contains('open')) {
        resolve(true);
      }
    }

    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) resolve(false);
    });
    confirmBtn.addEventListener('click', () => resolve(true));
    cancelBtn.addEventListener('click', () => resolve(false));
    document.addEventListener('keydown', onKeydown);

    window.openModal(overlay.id);
    confirmBtn.focus();
  });
}

/**
 * Custom error modal — themed for error messages (insufficient amount,
 * validation errors, etc.). Features:
 *   • radius-lg rounded corners
 *   • No X close button (dismiss only via OK or overlay click)
 *   • Error-themed: red accent bar, alert icon, error color palette
 */
export function error(title, text, options = {}) {
  return new Promise((onSettle) => {
    let el = document.getElementById('modal-host');
    if (!el) {
      el = document.createElement('div');
      el.id = 'modal-host';
      document.body.appendChild(el);
    }

    const overlay = document.createElement('div');
    overlay.className = 'lib-modal-overlay';
    overlay.id = 'modal-' + (++counter);

    const box = document.createElement('div');
    box.className = 'lib-modal-box is-error lib-modal-error';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', overlay.id + '-title');

    if (title) {
      const titleEl = document.createElement('h3');
      titleEl.id = overlay.id + '-title';
      titleEl.className = 'lib-modal-title lib-modal-error-title';
      titleEl.innerHTML = `<span class="lib-modal-error-icon"><svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg></span>${title}`;
      box.appendChild(titleEl);
    }
    if (text) {
      const textEl = document.createElement('p');
      textEl.className = 'lib-modal-text lib-modal-error-text';
      textEl.textContent = text;
      box.appendChild(textEl);
    }

    const actions = document.createElement('div');
    actions.className = 'lib-modal-actions';
    const okBtn = document.createElement('button');
    okBtn.type = 'button';
    okBtn.className = 'btn btn-error';
    okBtn.textContent = options.confirmLabel || 'OK';
    okBtn.setAttribute('data-autofocus', '1');
    actions.appendChild(okBtn);
    box.appendChild(actions);
    // No close (X) button

    overlay.appendChild(box);
    el.appendChild(overlay);

    let settled = false;
    let timerId = options.timer ? setTimeout(() => resolve(null), options.timer) : null;

    function resolve(value) {
      if (settled) return;
      settled = true;
      window.closeModal(overlay);
      overlay.remove();
      document.removeEventListener('keydown', onKeydown);
      if (timerId) clearTimeout(timerId);
      onSettle(value);
    }
    function onKeydown(event) {
      if (event.key === 'Escape' && overlay.classList.contains('open')) {
        resolve(null);
      }
    }

    // Overlay click dismisses
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) resolve(null);
    });
    okBtn.addEventListener('click', () => resolve(true));
    document.addEventListener('keydown', onKeydown);

    window.openModal(overlay.id);
    okBtn.focus();
  }).then(() => undefined);
}

/**
 * Custom success modal — themed for positive confirmations (items cancelled,
 * order completed, print success, etc.). Features:
 *   • radius-lg rounded corners
 *   • No X close button (dismiss only via OK or overlay click)
 *   • Success-themed: green accent bar, check icon, success color palette
 */
export function success(title, text, options = {}) {
  return new Promise((onSettle) => {
    let el = document.getElementById('modal-host');
    if (!el) {
      el = document.createElement('div');
      el.id = 'modal-host';
      document.body.appendChild(el);
    }

    const overlay = document.createElement('div');
    overlay.className = 'lib-modal-overlay';
    overlay.id = 'modal-' + (++counter);

    const box = document.createElement('div');
    box.className = 'lib-modal-box is-success lib-modal-success';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', overlay.id + '-title');

    if (title) {
      const titleEl = document.createElement('h3');
      titleEl.id = overlay.id + '-title';
      titleEl.className = 'lib-modal-title lib-modal-success-title';
      titleEl.innerHTML = `<span class="lib-modal-success-icon"><svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg></span>${title}`;
      box.appendChild(titleEl);
    }
    if (text) {
      const textEl = document.createElement('p');
      textEl.className = 'lib-modal-text lib-modal-success-text';
      textEl.textContent = text;
      box.appendChild(textEl);
    }

    const actions = document.createElement('div');
    actions.className = 'lib-modal-actions';
    const okBtn = document.createElement('button');
    okBtn.type = 'button';
    okBtn.className = 'btn btn-success-modal';
    okBtn.textContent = options.confirmLabel || 'OK';
    okBtn.setAttribute('data-autofocus', '1');
    actions.appendChild(okBtn);
    box.appendChild(actions);
    // No close (X) button

    overlay.appendChild(box);
    el.appendChild(overlay);

    let settled = false;
    let timerId = options.timer ? setTimeout(() => resolve(null), options.timer) : null;

    function resolve(value) {
      if (settled) return;
      settled = true;
      window.closeModal(overlay);
      overlay.remove();
      document.removeEventListener('keydown', onKeydown);
      if (timerId) clearTimeout(timerId);
      onSettle(value);
    }
    function onKeydown(event) {
      if (event.key === 'Escape' && overlay.classList.contains('open')) {
        resolve(null);
      }
    }

    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) resolve(null);
    });
    okBtn.addEventListener('click', () => resolve(true));
    document.addEventListener('keydown', onKeydown);

    window.openModal(overlay.id);
    okBtn.focus();
  }).then(() => undefined);
}

/**
 * Custom warning/confirm modal — themed for auth prompts, confirmations,
 * and info warnings. Features:
 *   • radius-lg rounded corners
 *   • No X close button (dismiss only via buttons or overlay click)
 *   • Warning-themed: amber accent, triangle-alert icon, warning palette
 *   • Returns boolean (true = confirmed, false/null = cancelled)
 */
export function warning(title, text, options = {}) {
  return new Promise((onSettle) => {
    let el = document.getElementById('modal-host');
    if (!el) {
      el = document.createElement('div');
      el.id = 'modal-host';
      document.body.appendChild(el);
    }

    const overlay = document.createElement('div');
    overlay.className = 'lib-modal-overlay';
    overlay.id = 'modal-' + (++counter);

    const box = document.createElement('div');
    box.className = 'lib-modal-box is-warning lib-modal-warning';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', overlay.id + '-title');

    if (title) {
      const titleEl = document.createElement('h3');
      titleEl.id = overlay.id + '-title';
      titleEl.className = 'lib-modal-title lib-modal-warning-title';
      titleEl.innerHTML = `<span class="lib-modal-warning-icon"><svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></span>${title}`;
      box.appendChild(titleEl);
    }
    if (text) {
      const textEl = document.createElement('p');
      textEl.className = 'lib-modal-text lib-modal-warning-text';
      textEl.textContent = text;
      box.appendChild(textEl);
    }

    const actions = document.createElement('div');
    actions.className = 'lib-modal-actions lib-modal-warning-actions';

    const confirmBtn = document.createElement('button');
    confirmBtn.type = 'button';
    confirmBtn.className = 'btn btn-warning';
    confirmBtn.textContent = options.confirmLabel || 'Confirm';
    confirmBtn.setAttribute('data-autofocus', '1');
    actions.appendChild(confirmBtn);

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-ghost lib-modal-warning-cancel';
    cancelBtn.textContent = options.cancelLabel || 'Cancel';
    actions.appendChild(cancelBtn);

    box.appendChild(actions);
    // No close (X) button

    overlay.appendChild(box);
    el.appendChild(overlay);

    let settled = false;
    let timerId = options.timer ? setTimeout(() => resolve(null), options.timer) : null;

    function resolve(value) {
      if (settled) return;
      settled = true;
      window.closeModal(overlay);
      overlay.remove();
      document.removeEventListener('keydown', onKeydown);
      if (timerId) clearTimeout(timerId);
      onSettle(value);
    }
    function onKeydown(event) {
      if (event.key === 'Escape' && overlay.classList.contains('open')) {
        resolve(null);
      }
    }

    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) resolve(null);
    });
    confirmBtn.addEventListener('click', () => resolve(true));
    cancelBtn.addEventListener('click', () => resolve(null));
    document.addEventListener('keydown', onKeydown);

    window.openModal(overlay.id);
    confirmBtn.focus();
  }).then((value) => value === true);
}

/**
 * Custom prompt modal — themed for input prompts (renaming zones, entering values, etc.)
 * Matches error, success, and warning modals.
 */
export function prompt(title, text, options = {}) {
  return new Promise((onSettle) => {
    let el = document.getElementById('modal-host');
    if (!el) {
      el = document.createElement('div');
      el.id = 'modal-host';
      document.body.appendChild(el);
    }

    const overlay = document.createElement('div');
    overlay.className = 'lib-modal-overlay';
    overlay.id = 'modal-' + (++counter);

    const box = document.createElement('div');
    box.className = 'lib-modal-box is-prompt lib-modal-prompt';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', overlay.id + '-title');
    box.style.cssText = 'max-width: 360px; width: 100%; padding: 1.15rem 1.25rem; border-radius: 12px; box-sizing: border-box;';

    if (title) {
      const titleEl = document.createElement('h3');
      titleEl.id = overlay.id + '-title';
      titleEl.className = 'lib-modal-title lib-modal-prompt-title';
      titleEl.style.cssText = 'display:flex;align-items:center;margin-bottom:0.5rem;font-size:1.05rem;font-weight:600;';
      titleEl.innerHTML = `<span class="lib-modal-prompt-icon" style="display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:50%;background:rgba(20,184,166,0.15);color:var(--color-primary, #14B8A6);margin-right:0.65rem;flex-shrink:0;"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg></span><span>${title}</span>`;
      box.appendChild(titleEl);
    }
    if (text) {
      const textEl = document.createElement('p');
      textEl.className = 'lib-modal-text';
      textEl.textContent = text;
      box.appendChild(textEl);
    }

    const inputWrap = document.createElement('div');
    inputWrap.className = 'lib-modal-input-wrap';
    inputWrap.style.cssText = 'margin: 0.75rem 0 1rem 0; width: 100%;';
    const inputEl = document.createElement('input');
    inputEl.type = 'text';
    inputEl.className = 'lib-modal-input';
    inputEl.value = options.defaultValue || '';
    inputEl.placeholder = options.placeholder || '';
    if (options.maxLength) inputEl.maxLength = options.maxLength;
    inputEl.style.cssText = 'width: 100%; padding: 0.6rem 0.75rem; border-radius: 6px; border: 1px solid var(--border-default); background: var(--surface-default); color: var(--text-primary); font-size: 0.875rem; outline: none; box-sizing: border-box;';
    inputWrap.appendChild(inputEl);
    box.appendChild(inputWrap);

    const actions = document.createElement('div');
    actions.className = 'lib-modal-actions';
    actions.style.cssText = 'display: flex; gap: 0.5rem; width: 100%; margin-top: 0.5rem;';

    const saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.className = 'btn btn-primary';
    saveBtn.textContent = options.confirmLabel || 'Save';
    saveBtn.style.cssText = 'flex: 1; width: 50%; justify-content: center; text-align: center; margin: 0; padding: 0.6rem 0; font-weight: 600; border-radius: 6px;';

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-ghost';
    cancelBtn.textContent = options.cancelLabel || 'Cancel';
    cancelBtn.style.cssText = 'flex: 1; width: 50%; justify-content: center; text-align: center; margin: 0; padding: 0.6rem 0; font-weight: 600; border-radius: 6px; border: 1px solid var(--border-default, #334155); background: transparent; color: var(--text-primary);';

    actions.appendChild(saveBtn);
    actions.appendChild(cancelBtn);
    box.appendChild(actions);

    overlay.appendChild(box);
    el.appendChild(overlay);

    let settled = false;
    function resolve(value) {
      if (settled) return;
      settled = true;
      window.closeModal(overlay);
      overlay.remove();
      document.removeEventListener('keydown', onKeydown);
      onSettle(value);
    }
    function onKeydown(event) {
      if (event.key === 'Escape' && overlay.classList.contains('open')) {
        resolve(null);
      }
    }

    inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        resolve(inputEl.value.trim());
      }
    });

    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) resolve(null);
    });
    saveBtn.addEventListener('click', () => resolve(inputEl.value.trim()));
    cancelBtn.addEventListener('click', () => resolve(null));
    document.addEventListener('keydown', onKeydown);

    window.openModal(overlay.id);
    setTimeout(() => { inputEl.focus(); inputEl.select(); }, 100);
  });
}

/**
 * Custom Add Table modal — themed exactly like modal.prompt, confirm, error, success
 */
export function addTableModal(sections = [], activeSection = 'Main') {
  return new Promise((onSettle) => {
    let el = document.getElementById('modal-host');
    if (!el) {
      el = document.createElement('div');
      el.id = 'modal-host';
      document.body.appendChild(el);
    }

    const overlay = document.createElement('div');
    overlay.className = 'lib-modal-overlay';
    overlay.id = 'modal-' + (++counter);

    const box = document.createElement('div');
    box.className = 'lib-modal-box is-prompt lib-modal-prompt';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', overlay.id + '-title');
    box.style.cssText = 'max-width: 380px; width: 100%; padding: 1.15rem 1.25rem; border-radius: 12px; box-sizing: border-box;';

    const titleEl = document.createElement('h3');
    titleEl.id = overlay.id + '-title';
    titleEl.className = 'lib-modal-title';
    titleEl.style.cssText = 'display:flex;align-items:center;margin-bottom:0.5rem;font-size:1.05rem;font-weight:600;';
    titleEl.innerHTML = `<span style="display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:50%;background:rgba(20,184,166,0.15);color:var(--color-primary, #14B8A6);margin-right:0.65rem;flex-shrink:0;"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg></span><span>Add New Table</span>`;
    box.appendChild(titleEl);

    const formWrap = document.createElement('div');
    formWrap.style.cssText = 'margin: 0.85rem 0 1rem 0; width: 100%; display: flex; flex-direction: column; gap: 0.75rem;';

    const secOptions = sections.length
      ? sections.map(s => `<option value="${escapeHtml(s)}" ${s === activeSection ? 'selected' : ''}>${escapeHtml(s)}</option>`).join('')
      : `<option value="Main">Main</option>`;

    formWrap.innerHTML = `
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.65rem;">
        <div>
          <label style="display:block; font-size:0.72rem; font-weight:600; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.04em; margin-bottom:0.3rem;">Table Number</label>
          <input type="text" id="addModalTableNumber" placeholder="e.g. 1, VIP 1" style="width:100%; padding:0.6rem 0.75rem; border-radius:6px; border:1px solid var(--border-default); background:var(--surface-default); color:var(--text-primary); font-size:0.875rem; outline:none; box-sizing:border-box;">
        </div>
        <div>
          <label style="display:block; font-size:0.72rem; font-weight:600; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.04em; margin-bottom:0.3rem;">Room / Section</label>
          <select id="addModalTableSection" style="width:100%; padding:0.6rem 0.75rem; border-radius:6px; border:1px solid var(--border-default); background:var(--surface-default); color:var(--text-primary); font-size:0.875rem; outline:none; box-sizing:border-box;">
            ${secOptions}
          </select>
        </div>
      </div>
      <div>
        <label style="display:block; font-size:0.72rem; font-weight:600; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.04em; margin-bottom:0.4rem;">Table Shape &amp; Capacity</label>
        <div id="addModalTypeGrid" style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.5rem;">
          <div class="tm-type-card selected" data-type="square_small" style="display:flex; flex-direction:column; align-items:center; justify-content:center; gap:0.3rem; padding:0.6rem 0.2rem; border-radius:6px; border:1px solid var(--color-primary, #14B8A6); background:rgba(20,184,166,0.12); cursor:pointer; text-align:center;">
            <span style="font-size:1.2rem; line-height:1;">⬛</span>
            <span style="font-size:0.72rem; font-weight:600; color:var(--color-primary, #14B8A6);">2 Pax</span>
          </div>
          <div class="tm-type-card" data-type="square_medium" style="display:flex; flex-direction:column; align-items:center; justify-content:center; gap:0.3rem; padding:0.6rem 0.2rem; border-radius:6px; border:1px solid var(--border-default); background:var(--surface-default); cursor:pointer; text-align:center;">
            <span style="font-size:1.2rem; line-height:1;">◼️</span>
            <span style="font-size:0.72rem; color:var(--text-muted);">4 Pax</span>
          </div>
          <div class="tm-type-card" data-type="rectangle_large" style="display:flex; flex-direction:column; align-items:center; justify-content:center; gap:0.3rem; padding:0.6rem 0.2rem; border-radius:6px; border:1px solid var(--border-default); background:var(--surface-default); cursor:pointer; text-align:center;">
            <span style="font-size:1.2rem; line-height:1;">▭</span>
            <span style="font-size:0.72rem; color:var(--text-muted);">6 Pax</span>
          </div>
          <div class="tm-type-card" data-type="circle_medium" style="display:flex; flex-direction:column; align-items:center; justify-content:center; gap:0.3rem; padding:0.6rem 0.2rem; border-radius:6px; border:1px solid var(--border-default); background:var(--surface-default); cursor:pointer; text-align:center;">
            <span style="font-size:1.2rem; line-height:1;">⭕</span>
            <span style="font-size:0.72rem; color:var(--text-muted);">4 Pax</span>
          </div>
        </div>
      </div>
    `;
    box.appendChild(formWrap);

    const actions = document.createElement('div');
    actions.className = 'lib-modal-actions';
    actions.style.cssText = 'display: flex; gap: 0.5rem; width: 100%; margin-top: 0.5rem;';

    const saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.className = 'btn btn-primary';
    saveBtn.textContent = 'Add Table';
    saveBtn.style.cssText = 'flex: 1; width: 50%; justify-content: center; text-align: center; margin: 0; padding: 0.6rem 0; font-weight: 600; border-radius: 6px;';

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-ghost';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.style.cssText = 'flex: 1; width: 50%; justify-content: center; text-align: center; margin: 0; padding: 0.6rem 0; font-weight: 600; border-radius: 6px; border: 1px solid var(--border-default, #334155); background: transparent; color: var(--text-primary);';

    actions.appendChild(saveBtn);
    actions.appendChild(cancelBtn);
    box.appendChild(actions);

    overlay.appendChild(box);
    el.appendChild(overlay);

    let selectedType = 'square_small';
    const typeCards = formWrap.querySelectorAll('.tm-type-card');
    typeCards.forEach(card => {
      card.addEventListener('click', function() {
        typeCards.forEach(c => {
          c.style.borderColor = 'var(--border-default)';
          c.style.background = 'var(--surface-default)';
          const nameSpan = c.querySelector('span:last-child');
          if (nameSpan) { nameSpan.style.color = 'var(--text-muted)'; nameSpan.style.fontWeight = '400'; }
        });
        this.style.borderColor = 'var(--color-primary, #14B8A6)';
        this.style.background = 'rgba(20,184,166,0.12)';
        const nameSpan = this.querySelector('span:last-child');
        if (nameSpan) { nameSpan.style.color = 'var(--color-primary, #14B8A6)'; nameSpan.style.fontWeight = '600'; }
        selectedType = this.dataset.type;
      });
    });

    const numInput = formWrap.querySelector('#addModalTableNumber');
    const secSelect = formWrap.querySelector('#addModalTableSection');

    let settled = false;
    function resolve(value) {
      if (settled) return;
      settled = true;
      window.closeModal(overlay);
      overlay.remove();
      document.removeEventListener('keydown', onKeydown);
      onSettle(value);
    }

    function onKeydown(event) {
      if (event.key === 'Escape' && overlay.classList.contains('open')) {
        resolve(null);
      }
    }

    numInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        submitForm();
      }
    });

    function submitForm() {
      const num = numInput.value.trim();
      if (!num) return;
      const sec = secSelect.value;
      resolve({ table_number: num, room_section: sec, table_type: selectedType });
    }

    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) resolve(null);
    });
    saveBtn.addEventListener('click', submitForm);
    cancelBtn.addEventListener('click', () => resolve(null));
    document.addEventListener('keydown', onKeydown);

    window.openModal(overlay.id);
    setTimeout(() => { numInput.focus(); }, 100);
  });
}

/**
 * Custom Table Details modal — themed identically to modal.prompt, confirm, error, success
 */
export function tableDetailsModal(table, order = null) {
  return new Promise((onSettle) => {
    let el = document.getElementById('modal-host');
    if (!el) {
      el = document.createElement('div');
      el.id = 'modal-host';
      document.body.appendChild(el);
    }

    const overlay = document.createElement('div');
    overlay.className = 'lib-modal-overlay';
    overlay.id = 'modal-' + (++counter);

    const isOccupied = !!(order && order.id);
    const isReserved = table.status === 'reserved';
    const statusLabel = isOccupied ? 'Occupied' : (isReserved ? 'Reserved' : 'Available');
    const statusBg = isOccupied ? '#7f1d1d' : (isReserved ? '#78350f' : '#064e3b');
    const statusColor = isOccupied ? '#f87171' : (isReserved ? '#fbbf24' : '#34d399');

    const box = document.createElement('div');
    box.className = 'lib-modal-box lib-modal-prompt';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', overlay.id + '-title');
    box.style.cssText = 'max-width: 380px; width: 100%; padding: 1.15rem 1.25rem; border-radius: 12px; box-sizing: border-box;';

    const titleEl = document.createElement('h3');
    titleEl.id = overlay.id + '-title';
    titleEl.className = 'lib-modal-title';
    titleEl.style.cssText = 'display:flex;align-items:center;margin-bottom:0.5rem;font-size:1.05rem;font-weight:600;';
    titleEl.innerHTML = `<span style="display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:50%;background:rgba(20,184,166,0.15);color:var(--color-primary, #14B8A6);margin-right:0.65rem;flex-shrink:0;"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5z"></path><path d="M2 17l10 5 10-5"></path><path d="M2 12l10 5 10-5"></path></svg></span><span>Table ${escapeHtml(table.table_number)}</span>`;
    box.appendChild(titleEl);

    const body = document.createElement('div');
    body.style.cssText = 'margin: 0.85rem 0 1rem 0; width: 100%; display: flex; flex-direction: column; gap: 0.6rem;';

    let detailsHtml = '';
    if (isOccupied) {
      detailsHtml = `
        <div style="display:flex; justify-content:space-between; align-items:center; padding:0.55rem 0.75rem; background:var(--surface-default); border-radius:6px; border:1px solid var(--border-default);">
          <span style="font-size:0.75rem; color:var(--text-muted);">Status</span>
          <span style="font-size:0.75rem; font-weight:600; padding:0.2rem 0.5rem; border-radius:4px; background:${statusBg}; color:${statusColor};">${statusLabel}</span>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:center; padding:0.55rem 0.75rem; background:var(--surface-default); border-radius:6px; border:1px solid var(--border-default);">
          <span style="font-size:0.75rem; color:var(--text-muted);">Order #</span>
          <span style="font-size:0.8rem; font-weight:600; color:var(--text-primary);">${escapeHtml(order.id)}</span>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:center; padding:0.55rem 0.75rem; background:var(--surface-default); border-radius:6px; border:1px solid var(--border-default);">
          <span style="font-size:0.75rem; color:var(--text-muted);">Customer</span>
          <span style="font-size:0.8rem; font-weight:600; color:var(--text-primary);">${escapeHtml(order.customer_name || 'Walk-in')}</span>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:center; padding:0.55rem 0.75rem; background:var(--surface-default); border-radius:6px; border:1px solid var(--border-default);">
          <span style="font-size:0.75rem; color:var(--text-muted);">Total</span>
          <span style="font-size:0.85rem; font-weight:700; color:var(--color-primary, #14B8A6);">₱${parseFloat(order.total_amount || 0).toFixed(2)}</span>
        </div>
      `;
    } else {
      detailsHtml = `
        <div style="display:flex; justify-content:space-between; align-items:center; padding:0.55rem 0.75rem; background:var(--surface-default); border-radius:6px; border:1px solid var(--border-default);">
          <span style="font-size:0.75rem; color:var(--text-muted);">Status</span>
          <span style="font-size:0.75rem; font-weight:600; padding:0.2rem 0.5rem; border-radius:4px; background:${statusBg}; color:${statusColor};">${statusLabel}</span>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:center; padding:0.55rem 0.75rem; background:var(--surface-default); border-radius:6px; border:1px solid var(--border-default);">
          <span style="font-size:0.75rem; color:var(--text-muted);">Section</span>
          <span style="font-size:0.8rem; font-weight:600; color:var(--text-primary);">${escapeHtml(table.room_section || 'Main')}</span>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:center; padding:0.55rem 0.75rem; background:var(--surface-default); border-radius:6px; border:1px solid var(--border-default);">
          <span style="font-size:0.75rem; color:var(--text-muted);">Type</span>
          <span style="font-size:0.8rem; font-weight:600; color:var(--text-primary);">${escapeHtml(table.table_type || 'Square')}</span>
        </div>
      `;
    }
    body.innerHTML = detailsHtml;
    box.appendChild(body);

    const actions = document.createElement('div');
    actions.className = 'lib-modal-actions';
    actions.style.cssText = 'display: flex; gap: 0.5rem; width: 100%; margin-top: 0.5rem; flex-wrap: wrap;';

    if (isOccupied) {
      const viewBtn = document.createElement('button');
      viewBtn.type = 'button';
      viewBtn.className = 'btn btn-primary';
      viewBtn.textContent = 'View Order';
      viewBtn.style.cssText = 'flex: 1; justify-content: center; text-align: center; margin: 0; padding: 0.6rem 0; font-weight: 600; border-radius: 6px;';
      viewBtn.onclick = () => resolve('view');

      const releaseBtn = document.createElement('button');
      releaseBtn.type = 'button';
      releaseBtn.className = 'btn btn-ghost';
      releaseBtn.textContent = 'Release Table';
      releaseBtn.style.cssText = 'flex: 1; justify-content: center; text-align: center; margin: 0; padding: 0.6rem 0; font-weight: 600; border-radius: 6px; border: 1px solid var(--border-default); background: transparent; color: var(--text-primary);';
      releaseBtn.onclick = () => resolve('release');

      actions.appendChild(viewBtn);
      actions.appendChild(releaseBtn);
    } else {
      const rotateBtn = document.createElement('button');
      rotateBtn.type = 'button';
      rotateBtn.className = 'btn btn-primary';
      rotateBtn.textContent = 'Rotate 90°';
      rotateBtn.style.cssText = 'flex: 1; justify-content: center; text-align: center; margin: 0; padding: 0.6rem 0; font-weight: 600; border-radius: 6px;';
      rotateBtn.onclick = () => resolve('rotate');

      const deleteBtn = document.createElement('button');
      deleteBtn.type = 'button';
      deleteBtn.className = 'btn btn-ghost';
      deleteBtn.textContent = 'Delete Table';
      deleteBtn.style.cssText = 'flex: 1; justify-content: center; text-align: center; margin: 0; padding: 0.6rem 0; font-weight: 600; border-radius: 6px; border: 1px solid #7f1d1d; background: rgba(239,68,68,0.1); color: #f87171;';
      deleteBtn.onclick = () => resolve('delete');

      actions.appendChild(rotateBtn);
      actions.appendChild(deleteBtn);
    }

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'btn btn-ghost';
    closeBtn.textContent = 'Close';
    closeBtn.style.cssText = 'width: 100%; justify-content: center; text-align: center; margin: 0; padding: 0.5rem 0; font-weight: 600; border-radius: 6px; border: 1px solid var(--border-default); background: transparent; color: var(--text-muted); font-size: 0.8rem;';
    closeBtn.onclick = () => resolve(null);
    actions.appendChild(closeBtn);

    box.appendChild(actions);
    overlay.appendChild(box);
    el.appendChild(overlay);

    let settled = false;
    function resolve(value) {
      if (settled) return;
      settled = true;
      window.closeModal(overlay);
      overlay.remove();
      document.removeEventListener('keydown', onKeydown);
      onSettle(value);
    }

    function onKeydown(event) {
      if (event.key === 'Escape' && overlay.classList.contains('open')) {
        resolve(null);
      }
    }

    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) resolve(null);
    });
    document.addEventListener('keydown', onKeydown);

    window.openModal(overlay.id);
  });
}

/**
 * Custom Confirm EOD modal — themed exactly like modal.error, warning, success
 */
export function confirmEodModal(options = {}) {
  return new Promise((onSettle) => {
    let el = document.getElementById('modal-host');
    if (!el) {
      el = document.createElement('div');
      el.id = 'modal-host';
      document.body.appendChild(el);
    }

    const overlay = document.createElement('div');
    overlay.className = 'lib-modal-overlay';
    overlay.id = 'modal-' + (++counter);

    const box = document.createElement('div');
    box.className = 'lib-modal-box is-warning lib-modal-warning';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', overlay.id + '-title');
    box.style.cssText = 'max-width: 380px; width: 100%; padding: 1.15rem 1.25rem; border-radius: 12px; box-sizing: border-box; overflow: hidden;';

    const titleEl = document.createElement('h3');
    titleEl.id = overlay.id + '-title';
    titleEl.className = 'lib-modal-title';
    titleEl.style.cssText = 'display: flex; align-items: center; margin-bottom: 0.35rem; font-size: 1.05rem; font-weight: 600; color: var(--text-primary);';
    titleEl.innerHTML = `<span style="display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: 50%; background: rgba(245, 158, 11, 0.15); color: var(--color-warning, #f59e0b); margin-right: 0.65rem; flex-shrink: 0;"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></span><span>Confirm End of Day</span>`;
    box.appendChild(titleEl);

    const textEl = document.createElement('p');
    textEl.className = 'lib-modal-text';
    textEl.style.cssText = 'margin: 0.25rem 0 0.75rem 0; color: var(--text-muted); font-size: 0.84rem; line-height: 1.4;';
    textEl.textContent = 'Finalize today and generate official compliance records.';
    box.appendChild(textEl);

    if (options.rlcEnabled) {
      const infoEl = document.createElement('div');
      infoEl.style.cssText = 'background: var(--surface-default, var(--bg-main)); border: 1px solid var(--border-default); border-radius: 6px; padding: 0.5rem 0.65rem; margin-bottom: 0.75rem; font-size: 0.75rem; color: var(--text-muted); line-height: 1.35;';
      infoEl.innerHTML = '<strong style="color: var(--text-primary);">Process Scope:</strong> Creates and transmits RLC Compliance File to SFTP server.';
      box.appendChild(infoEl);
    }

    const inputWrap = document.createElement('div');
    inputWrap.style.cssText = 'margin-bottom: 0.75rem; width: 100%;';
    inputWrap.innerHTML = `
      <label style="display: block; font-size: 0.76rem; font-weight: 700; color: var(--text-primary); margin-bottom: 0.25rem;">Type <span style="color: var(--color-danger, #ef4444); font-family: var(--font-mono, monospace); font-weight: 800;">YES</span> to confirm</label>
      <input type="text" id="eodConfirmInput" placeholder="YES" autocomplete="off" style="width: 100%; height: 38px; text-transform: uppercase; font-family: var(--font-mono, 'JetBrains Mono', monospace); font-size: 1.05rem; font-weight: 700; text-align: center; border-radius: 6px; border: 1px solid var(--border-default); background: var(--surface-default, var(--bg-main)); color: var(--text-primary); outline: none; box-sizing: border-box;">
      <p style="font-size: 0.72rem; color: var(--text-muted); margin: 0.25rem 0 0 0;">This action cannot be undone. Ensure all orders are settled.</p>
    `;
    box.appendChild(inputWrap);

    const actions = document.createElement('div');
    actions.className = 'lib-modal-actions';
    actions.style.cssText = 'display: flex; gap: 0.5rem; width: 100%; margin-top: 0.5rem;';

    const confirmBtn = document.createElement('button');
    confirmBtn.type = 'button';
    confirmBtn.className = 'btn btn-warning';
    confirmBtn.disabled = true;
    confirmBtn.textContent = 'Perform End of Day';
    confirmBtn.style.cssText = 'flex: 1; width: 50%; justify-content: center; text-align: center; margin: 0; padding: 0.6rem 0; font-weight: 600; border-radius: 6px; background: var(--color-warning, #f59e0b); color: #fff; border: none; cursor: not-allowed; opacity: 0.5; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.03em;';

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-ghost';
    cancelBtn.textContent = 'Go Back';
    cancelBtn.style.cssText = 'flex: 1; width: 50%; justify-content: center; text-align: center; margin: 0; padding: 0.6rem 0; font-weight: 600; border-radius: 6px; border: 1px solid var(--border-default, #334155); background: transparent; color: var(--text-primary); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.03em;';

    actions.appendChild(confirmBtn);
    actions.appendChild(cancelBtn);
    box.appendChild(actions);

    overlay.appendChild(box);
    el.appendChild(overlay);

    const input = inputWrap.querySelector('#eodConfirmInput');
    input.addEventListener('input', () => {
      const isYes = (input.value || '').trim().toUpperCase() === 'YES';
      confirmBtn.disabled = !isYes;
      confirmBtn.style.opacity = isYes ? '1' : '0.5';
      confirmBtn.style.cursor = isYes ? 'pointer' : 'not-allowed';
    });

    let settled = false;
    function resolve(value) {
      if (settled) return;
      settled = true;
      window.closeModal(overlay);
      overlay.remove();
      document.removeEventListener('keydown', onKeydown);
      onSettle(value);
    }

    function onKeydown(event) {
      if (event.key === 'Escape' && overlay.classList.contains('open')) {
        resolve(false);
      } else if (event.key === 'Enter' && overlay.classList.contains('open')) {
        if (!confirmBtn.disabled) resolve(true);
      }
    }

    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) resolve(false);
    });
    confirmBtn.addEventListener('click', () => resolve(true));
    cancelBtn.addEventListener('click', () => resolve(false));
    document.addEventListener('keydown', onKeydown);

    window.openModal(overlay.id);
    setTimeout(() => { input.focus(); }, 80);
  });
}

/**
 * Custom Daily Bundle modal — themed exactly like modal.success, confirm, prompt
 */
export function dailyBundleModal(options = {}) {
  return new Promise((onSettle) => {
    let el = document.getElementById('modal-host');
    if (!el) {
      el = document.createElement('div');
      el.id = 'modal-host';
      document.body.appendChild(el);
    }

    const overlay = document.createElement('div');
    overlay.className = 'lib-modal-overlay';
    overlay.id = 'modal-' + (++counter);

    const box = document.createElement('div');
    box.className = 'lib-modal-box is-success lib-modal-success';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', overlay.id + '-title');
    box.style.cssText = 'max-width: 380px; width: 100%; padding: 1.15rem 1.25rem; border-radius: 12px; box-sizing: border-box; overflow: hidden;';

    const titleEl = document.createElement('h3');
    titleEl.id = overlay.id + '-title';
    titleEl.className = 'lib-modal-title lib-modal-success-title';
    titleEl.style.cssText = 'display: flex; align-items: center; margin-bottom: 0.35rem; font-size: 1.05rem; font-weight: 600; color: var(--text-primary);';
    titleEl.innerHTML = `<span class="lib-modal-success-icon" style="display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: 50%; background: rgba(34, 197, 94, 0.15); color: var(--color-success, #22c55e); margin-right: 0.65rem; flex-shrink: 0;"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg></span><span>Generate & Send Reports</span>`;
    box.appendChild(titleEl);

    const textEl = document.createElement('p');
    textEl.className = 'lib-modal-text';
    textEl.style.cssText = 'margin: 0.25rem 0 0.75rem 0; color: var(--text-muted); font-size: 0.84rem; line-height: 1.4;';
    textEl.textContent = 'Attach recipient emails (up to 5) before generating reports.';
    box.appendChild(textEl);

    const infoEl = document.createElement('div');
    infoEl.style.cssText = 'background: var(--surface-default, var(--bg-main)); border: 1px solid var(--border-default); border-radius: 6px; padding: 0.5rem 0.65rem; margin-bottom: 0.75rem; font-size: 0.75rem; color: var(--text-muted); line-height: 1.35;';
    infoEl.innerHTML = '<strong style="color: var(--text-primary);">Included files:</strong> Daily Sales, Item Sales, E-Journal, Z-Reading TXT.';
    box.appendChild(infoEl);

    const todayStr = options.date || new Date().toISOString().split('T')[0];
    const formWrap = document.createElement('div');
    formWrap.style.cssText = 'margin-bottom: 0.75rem; width: 100%; display: flex; flex-direction: column; gap: 0.65rem;';

    formWrap.innerHTML = `
      <div>
        <label style="display: block; font-size: 0.76rem; font-weight: 700; color: var(--text-primary); margin-bottom: 0.25rem;">Business Day</label>
        <input type="date" id="dailyBundleDate" value="${todayStr}" style="width: 100%; height: 36px; padding: 0 0.65rem; border-radius: 6px; border: 1px solid var(--border-default); background: var(--surface-default, var(--bg-main)); color: var(--text-primary); font-family: var(--font-mono, monospace); font-size: 0.84rem; outline: none; box-sizing: border-box;">
      </div>
      <div>
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.35rem;">
          <label style="font-size: 0.76rem; font-weight: 700; color: var(--text-primary); margin: 0;">Recipients (<span id="recipientCount">1/5</span>)</label>
          <button type="button" id="btnAddRecipient" style="padding: 0.15rem 0.5rem; font-size: 0.72rem; font-weight: 600; border-radius: 4px; border: 1px solid var(--border-default); background: transparent; color: var(--text-primary); cursor: pointer;">+ Add Email</button>
        </div>
        <div id="recipientsContainer" style="display: flex; flex-direction: column; gap: 0.35rem; max-height: 96px; overflow-y: auto;"></div>
      </div>
    `;
    box.appendChild(formWrap);

    const actions = document.createElement('div');
    actions.className = 'lib-modal-actions';
    actions.style.cssText = 'display: flex; gap: 0.5rem; width: 100%; margin-top: 0.5rem;';

    const sendBtn = document.createElement('button');
    sendBtn.type = 'button';
    sendBtn.className = 'btn btn-success-modal';
    sendBtn.textContent = 'Generate & Send';
    sendBtn.style.cssText = 'flex: 1; width: 50%; justify-content: center; text-align: center; margin: 0; padding: 0.6rem 0; font-weight: 600; border-radius: 6px; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.03em;';

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-ghost';
    cancelBtn.textContent = 'Back';
    cancelBtn.style.cssText = 'flex: 1; width: 50%; justify-content: center; text-align: center; margin: 0; padding: 0.6rem 0; font-weight: 600; border-radius: 6px; border: 1px solid var(--border-default, #334155); background: transparent; color: var(--text-primary); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.03em;';

    actions.appendChild(sendBtn);
    actions.appendChild(cancelBtn);
    box.appendChild(actions);

    overlay.appendChild(box);
    el.appendChild(overlay);

    let emails = options.recipients && options.recipients.length ? [...options.recipients] : [''];
    const container = formWrap.querySelector('#recipientsContainer');
    const countSpan = formWrap.querySelector('#recipientCount');
    const addBtn = formWrap.querySelector('#btnAddRecipient');

    function renderEmails() {
      container.innerHTML = '';
      countSpan.textContent = `${emails.length}/5`;
      emails.forEach((email, idx) => {
        const row = document.createElement('div');
        row.style.cssText = 'display: flex; gap: 0.35rem; align-items: center;';
        row.innerHTML = `
          <input type="email" placeholder="recipient@example.com" value="${escapeHtml(email)}" style="flex: 1; height: 32px; padding: 0 0.55rem; border-radius: 4px; border: 1px solid var(--border-default); background: var(--surface-default, var(--bg-main)); color: var(--text-primary); font-size: 0.8rem; outline: none; box-sizing: border-box;">
          ${emails.length > 1 ? `<button type="button" style="width: 32px; height: 32px; border-radius: 4px; border: 1px solid var(--border-default); background: transparent; color: var(--color-danger, #ef4444); cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 0.9rem;">✕</button>` : ''}
        `;
        const inp = row.querySelector('input');
        inp.addEventListener('input', () => { emails[idx] = inp.value; });
        const del = row.querySelector('button');
        if (del) {
          del.addEventListener('click', () => {
            emails.splice(idx, 1);
            renderEmails();
          });
        }
        container.appendChild(row);
      });
    }
    renderEmails();

    addBtn.addEventListener('click', () => {
      if (emails.length >= 5) {
        warning('Maximum Limit Reached', 'You can attach up to 5 email recipients per transmission.', { confirmLabel: 'I understand' });
        return;
      }
      emails.push('');
      renderEmails();
      setTimeout(() => {
        const inputs = container.querySelectorAll('input');
        if (inputs.length) inputs[inputs.length - 1].focus();
      }, 50);
    });

    let settled = false;
    function resolve(value) {
      if (settled) return;
      settled = true;
      window.closeModal(overlay);
      overlay.remove();
      document.removeEventListener('keydown', onKeydown);
      onSettle(value);
    }

    function onKeydown(event) {
      if (event.key === 'Escape' && overlay.classList.contains('open')) {
        resolve(null);
      }
    }

    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) resolve(null);
    });
    sendBtn.addEventListener('click', () => {
      const dateVal = formWrap.querySelector('#dailyBundleDate').value;
      const valid = emails.map(e => e.trim()).filter(Boolean);
      if (!valid.length) {
        warning('Recipient Required', 'Please enter at least one valid recipient email.', { confirmLabel: 'I understand' });
        return;
      }
      resolve({ date: dateVal, recipients: valid });
    });
    cancelBtn.addEventListener('click', () => resolve(null));
    document.addEventListener('keydown', onKeydown);

    window.openModal(overlay.id);
  });
}

/**
 * Custom EOD Progress Modal — staged processing tracker
 */
export function progressEodModal(options = {}) {
  let el = document.getElementById('modal-host');
  if (!el) {
    el = document.createElement('div');
    el.id = 'modal-host';
    document.body.appendChild(el);
  }

  const overlay = document.createElement('div');
  overlay.className = 'lib-modal-overlay';
  overlay.id = 'modal-' + (++counter);

  const box = document.createElement('div');
  box.className = 'lib-modal-box is-warning lib-modal-warning';
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  box.style.cssText = 'max-width: 400px; width: 100%; padding: 1.15rem 1.25rem; border-radius: 12px; box-sizing: border-box; border-color: var(--color-primary); overflow: hidden;';

  const rlc = !!options.rlcEnabled;
  box.innerHTML = `
    <h3 class="lib-modal-title" style="display: flex; align-items: center; margin-bottom: 0.35rem; font-size: 1.05rem; font-weight: 600; color: var(--color-primary);">
      <span style="display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: 50%; background: rgba(59, 130, 246, 0.15); color: var(--color-primary); margin-right: 0.65rem; flex-shrink: 0;"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg></span>
      <span>Processing End of Day</span>
    </h3>
    <p class="lib-modal-text" style="margin: 0.25rem 0 0.75rem 0; color: var(--text-muted); font-size: 0.84rem;">Please keep this screen open while we complete closeout.</p>
    <div style="display: flex; justify-content: space-between; gap: 0.35rem; margin-bottom: 0.75rem; padding: 0.5rem 0.65rem; background: var(--surface-default, var(--bg-main)); border-radius: 6px; font-family: var(--font-mono, monospace); font-size: 0.75rem; font-weight: 700;">
      <span id="pStep1" style="color: var(--color-primary);">1. Init</span>
      ${rlc ? '<span id="pStep2" style="color: var(--text-muted);">2. RLC</span><span id="pStep3" style="color: var(--text-muted);">3. Send</span>' : ''}
      <span id="pStep4" style="color: var(--text-muted);">${rlc ? '4' : '2'}. Finalize</span>
    </div>
    <div id="pStatus" style="padding: 0.65rem; background: var(--surface-default, var(--bg-main)); border-radius: 6px; font-size: 0.8rem; margin-bottom: 0.75rem; min-height: 38px; display: flex; align-items: center;">
      <strong>Initializing EOD process...</strong>
    </div>
    <div class="lib-modal-actions" style="display: flex; gap: 0.5rem; width: 100%; margin-top: 0.5rem;">
      <button type="button" id="pPrintBtn" class="btn btn-primary" style="display: none; width: 100%; justify-content: center; text-align: center; margin: 0; padding: 0.6rem 0; font-weight: 600; border-radius: 6px; font-size: 0.8rem; text-transform: uppercase;">Close & Print Reports</button>
      <button type="button" id="pCloseBtn" class="btn btn-ghost" style="display: none; width: 100%; justify-content: center; text-align: center; margin: 0; padding: 0.6rem 0; font-weight: 600; border-radius: 6px; border: 1px solid var(--border-default); background: transparent; color: var(--text-primary); font-size: 0.8rem; text-transform: uppercase;">Close</button>
    </div>
  `;

  overlay.appendChild(box);
  el.appendChild(overlay);
  window.openModal(overlay.id);

  return {
    overlay,
    setStatus: (html) => {
      const el = box.querySelector('#pStatus');
      if (el) el.innerHTML = html;
    },
    setStep: (stepNum, color) => {
      const s = box.querySelector('#pStep' + stepNum);
      if (s) s.style.color = color || 'var(--color-primary)';
    },
    showPrintClose: (onPrint) => {
      const btn = box.querySelector('#pPrintBtn');
      if (btn) {
        btn.style.display = 'flex';
        btn.onclick = onPrint;
      }
    },
    showClose: (onClose) => {
      const btn = box.querySelector('#pCloseBtn');
      if (btn) {
        btn.style.display = 'flex';
        btn.onclick = onClose;
      }
    },
    close: () => {
      window.closeModal(overlay);
      overlay.remove();
    }
  };
}

/**
 * Custom End of Day Required Modal — prompts the cashier when previous business dates lack EOD/Z-reading.
 */
export function eodRequiredModal(initialDates = [], onRefresh) {
  return new Promise((onSettle) => {
    let el = document.getElementById('modal-host');
    if (!el) {
      el = document.createElement('div');
      el.id = 'modal-host';
      document.body.appendChild(el);
    }

    const overlay = document.createElement('div');
    overlay.className = 'lib-modal-overlay';
    overlay.id = 'modal-' + (++counter);

    const box = document.createElement('div');
    box.className = 'lib-modal-box is-warning lib-modal-warning';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', overlay.id + '-title');
    box.style.cssText = 'max-width: 480px; width: 100%; padding: 1.25rem 1.4rem; border-radius: 14px; box-sizing: border-box; overflow: hidden; display: flex; flex-direction: column;';

    const titleEl = document.createElement('h3');
    titleEl.id = overlay.id + '-title';
    titleEl.className = 'lib-modal-title lib-modal-warning-title';
    titleEl.style.cssText = 'display: flex; align-items: center; gap: 0.65rem; margin-bottom: 0.35rem; font-size: 1.1rem; font-weight: 700; color: var(--color-warning, #f59e0b);';
    titleEl.innerHTML = `
      <span style="display: inline-flex; align-items: center; justify-content: center; width: 34px; height: 34px; border-radius: 50%; background: rgba(245, 158, 11, 0.15); color: var(--color-warning, #f59e0b); flex-shrink: 0;">
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line><line x1="10" y1="14" x2="14" y2="18"></line><line x1="14" y1="14" x2="10" y2="18"></line></svg>
      </span>
      <span>End of Day Required</span>
    `;
    box.appendChild(titleEl);

    const descEl = document.createElement('p');
    descEl.className = 'lib-modal-text lib-modal-warning-text';
    descEl.style.cssText = 'margin: 0.25rem 0 0.75rem 0; color: var(--text-secondary); font-size: 0.84rem; line-height: 1.4;';
    descEl.textContent = 'Incomplete processing for previous dates. Complete the queue in order to keep End of Day processing compliant.';
    box.appendChild(descEl);

    const noticeEl = document.createElement('div');
    noticeEl.style.cssText = 'background: var(--surface-default, var(--bg-main)); border: 1px solid var(--border-default); border-radius: 8px; padding: 0.6rem 0.75rem; margin-bottom: 0.75rem; font-size: 0.76rem; color: var(--text-muted); line-height: 1.4;';
    noticeEl.innerHTML = '<strong style="color: var(--color-warning, #f59e0b);">BIR & SFTP Notice:</strong> All prior business days must have an official Z-Reading generated before opening active register operations.';
    box.appendChild(noticeEl);

    // Queue Container Header
    const queueHeader = document.createElement('div');
    queueHeader.style.cssText = 'display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem; font-size: 0.78rem; font-weight: 700; color: var(--text-primary);';
    queueHeader.innerHTML = `<span>Unclosed Dates (<span id="eodQueueCount">0</span>)</span>`;
    box.appendChild(queueHeader);

    const queueList = document.createElement('div');
    queueList.id = 'eodQueueList';
    queueList.style.cssText = 'display: flex; flex-direction: column; gap: 0.45rem; max-height: 180px; overflow-y: auto; margin-bottom: 0.75rem; padding-right: 0.25rem;';
    box.appendChild(queueList);

    // Live Progress/Status Log
    const progressBlock = document.createElement('div');
    progressBlock.id = 'eodQueueProgress';
    progressBlock.style.cssText = 'display: none; padding: 0.6rem 0.75rem; background: var(--surface-default, var(--bg-main)); border: 1px solid var(--border-default); border-radius: 8px; font-size: 0.78rem; margin-bottom: 0.75rem;';
    box.appendChild(progressBlock);

    // Actions
    const actions = document.createElement('div');
    actions.className = 'lib-modal-actions';
    actions.style.cssText = 'display: flex; gap: 0.5rem; width: 100%; margin-top: 0.35rem;';

    const processAllBtn = document.createElement('button');
    processAllBtn.type = 'button';
    processAllBtn.className = 'btn btn-warning';
    processAllBtn.innerHTML = '<span>Process Queue</span>';
    processAllBtn.style.cssText = 'flex: 1; justify-content: center; text-align: center; margin: 0; padding: 0.65rem 0.5rem; font-weight: 700; border-radius: 6px; background: var(--color-warning, #f59e0b); color: #fff; border: none; font-size: 0.8rem; text-transform: uppercase; cursor: pointer;';

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'btn btn-ghost';
    closeBtn.textContent = 'Continue to POS';
    closeBtn.style.cssText = 'flex: 1; justify-content: center; text-align: center; margin: 0; padding: 0.65rem 0.5rem; font-weight: 600; border-radius: 6px; border: 1px solid var(--border-default); background: transparent; color: var(--text-muted); font-size: 0.8rem; text-transform: uppercase; cursor: pointer; display: none;';

    actions.appendChild(processAllBtn);
    actions.appendChild(closeBtn);
    box.appendChild(actions);

    overlay.appendChild(box);
    el.appendChild(overlay);

    let dates = [...initialDates];
    let isProcessing = false;
    let printerPollInterval = null;

    function stopPrinterPolling() {
      if (printerPollInterval) {
        clearInterval(printerPollInterval);
        printerPollInterval = null;
      }
    }

    function startPrinterPolling() {
      stopPrinterPolling();
      printerPollInterval = setInterval(async () => {
        try {
          const pRes = await api.get('/check_printer_status?refresh=1');
          const pReady = pRes && (pRes.connected || pRes.dev_mode || pRes.printer_required === false);
          if (pReady) {
            stopPrinterPolling();
            progressBlock.innerHTML = `
              <div style="display: flex; align-items: center; gap: 0.5rem; color: #10b981; font-weight: 700; font-size: 0.82rem; padding: 0.2rem 0;">
                <span>✅ Cashier receipt printer detected and connected! Ready to process.</span>
              </div>
            `;
            setTimeout(() => {
              if (progressBlock.innerHTML.includes('detected and connected')) {
                progressBlock.style.display = 'none';
              }
            }, 3500);
          }
        } catch (_) {}
      }, 3000);
    }

    function renderDates() {
      const countEl = queueHeader.querySelector('#eodQueueCount');
      if (countEl) countEl.textContent = dates.length;

      queueList.innerHTML = '';
      if (!dates.length) {
        queueList.innerHTML = `
          <div style="padding: 1rem; text-align: center; color: #10b981; font-weight: 700; font-size: 0.85rem; background: rgba(16, 185, 129, 0.1); border-radius: 8px;">
            ✅ All previous business dates are closed and up to date!
          </div>
        `;
        processAllBtn.style.display = 'none';
        closeBtn.textContent = 'Continue to POS';
        closeBtn.className = 'btn btn-primary';
        closeBtn.style.display = 'flex';
        closeBtn.style.flex = '1';
        closeBtn.style.background = 'var(--color-primary)';
        closeBtn.style.color = '#fff';
        return;
      }

      processAllBtn.style.display = 'flex';
      closeBtn.style.display = 'none';

      dates.forEach((d, idx) => {
        const row = document.createElement('div');
        row.style.cssText = 'display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; padding: 0.55rem 0.75rem; background: var(--surface-default, var(--bg-main)); border: 1px solid var(--border-default); border-radius: 8px; font-size: 0.8rem;';
        const isFirst = idx === 0;
        const dStr = typeof d === 'string' ? d : (d?.date || '');
        const dDisplay = typeof d === 'string' ? d : (d?.display_date || d?.date || '');
        const dDay = typeof d === 'string' ? '' : (d?.day_name || '');
        const txCount = (d && d.transaction_count != null) ? d.transaction_count : 0;
        const pendingCount = (d && d.pending_count != null) ? d.pending_count : 0;

        let metaHtml = `${txCount} transaction${txCount === 1 ? '' : 's'}`;
        if (pendingCount > 0) {
          metaHtml += ` &bull; <a href="/orders" style="color: #ef4444; font-weight: 700; text-decoration: underline;" title="Click to view pending orders in Orders module">${pendingCount} pending order${pendingCount === 1 ? '' : 's'}</a>`;
        } else {
          metaHtml += ` &bull; <span style="color: #10b981; font-weight: 600;">0 pending</span>`;
        }

        row.innerHTML = `
          <div style="display: flex; flex-direction: column;">
            <span style="font-weight: 700; color: var(--text-primary);">${escapeHtml(dDisplay)}${dDay ? ` <span style="font-weight: normal; color: var(--text-muted); font-size: 0.74rem;">(${escapeHtml(dDay)})</span>` : ''}</span>
            <span style="font-size: 0.72rem; color: var(--text-muted); font-family: var(--font-mono, monospace);">${metaHtml}</span>
          </div>
          <button type="button" class="btn-process-single" data-date="${escapeHtml(dStr)}" style="padding: 0.3rem 0.65rem; font-size: 0.74rem; font-weight: 700; border-radius: 6px; border: 1px solid var(--color-warning, #f59e0b); background: ${isFirst ? 'rgba(245, 158, 11, 0.15)' : 'transparent'}; color: ${isFirst ? 'var(--color-warning, #f59e0b)' : 'var(--text-muted)'}; cursor: ${isFirst ? 'pointer' : 'not-allowed'}; opacity: ${isFirst ? '1' : '0.5'};" ${isFirst ? '' : 'disabled'}>
            Process
          </button>
        `;

        const btn = row.querySelector('.btn-process-single');
        if (btn && isFirst) {
          btn.addEventListener('click', () => processSingleDate(dStr));
        }
        queueList.appendChild(row);
      });
    }

    renderDates();

    async function processSingleDate(targetItem) {
      if (isProcessing) return false;
      const dateStr = typeof targetItem === 'string' ? targetItem : (targetItem?.date || '');
      if (!dateStr) return false;

      isProcessing = true;
      processAllBtn.disabled = true;
      processAllBtn.style.opacity = '0.5';
      const singleBtns = queueList.querySelectorAll('.btn-process-single');
      singleBtns.forEach(b => { b.disabled = true; b.style.opacity = '0.5'; });

      // 0. Hard Block: Cashier receipt printer must be connected and ready
      try {
        let pRes = await api.get('/check_printer_status?refresh=1');
        let pRetries = 2;
        while (pRes && pRes.checking && pRetries > 0) {
          await new Promise(r => setTimeout(r, 600));
          pRes = await api.get('/check_printer_status');
          pRetries--;
        }

        const pReady = pRes && (pRes.connected || pRes.dev_mode || pRes.printer_required === false);
        if (!pReady) {
          const pMsg = (pRes && pRes.message) ? pRes.message : 'Cashier receipt printer is not connected or offline.';

          // Re-enable UI state immediately so user can click Process Queue again once printer is plugged in
          isProcessing = false;
          processAllBtn.disabled = false;
          processAllBtn.style.opacity = '1';
          const singleBtns = queueList.querySelectorAll('.btn-process-single');
          singleBtns.forEach((b, idx) => {
            if (idx === 0) { b.disabled = false; b.style.opacity = '1'; }
          });

          progressBlock.style.display = 'block';
          progressBlock.innerHTML = `
            <div style="display: flex; flex-direction: column; gap: 0.35rem; color: #ef4444; font-weight: 700; font-size: 0.8rem;">
              <div style="display: flex; align-items: center; justify-content: space-between;">
                <span style="display: flex; align-items: center; gap: 0.4rem;">
                  <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M6 9V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v5"/><rect x="6" y="14" width="12" height="8" rx="1"/></svg>
                  <span>Printer Connection Required (EOD Blocked)</span>
                </span>
                <button type="button" id="btnRecheckPrinter" style="padding: 0.2rem 0.55rem; font-size: 0.72rem; font-weight: 700; border-radius: 5px; border: 1px solid #ef4444; background: rgba(239, 68, 68, 0.12); color: #ef4444; cursor: pointer;">
                  🔄 Re-check
                </button>
              </div>
              <div style="font-weight: normal; color: var(--text-muted); font-size: 0.76rem; line-height: 1.35;">
                ${escapeHtml(pMsg)}. Please plug in USB, turn on, and verify paper. <span style="color: var(--color-primary, #14B8A6); font-weight: 600;">(Auto-detecting connection every 3s...)</span>
              </div>
            </div>
          `;

          const recheckBtn = progressBlock.querySelector('#btnRecheckPrinter');
          if (recheckBtn) {
            recheckBtn.addEventListener('click', async () => {
              recheckBtn.disabled = true;
              recheckBtn.textContent = 'Checking...';
              try {
                const checkRes = await api.get('/check_printer_status?refresh=1');
                const isOk = checkRes && (checkRes.connected || checkRes.dev_mode || checkRes.printer_required === false);
                if (isOk) {
                  stopPrinterPolling();
                  progressBlock.innerHTML = `
                    <div style="display: flex; align-items: center; gap: 0.5rem; color: #10b981; font-weight: 700; font-size: 0.82rem; padding: 0.2rem 0;">
                      <span>✅ Cashier receipt printer detected and connected! Ready to process.</span>
                    </div>
                  `;
                } else {
                  if (typeof notify !== 'undefined' && notify.error) {
                    notify.error('Printer still disconnected: ' + ((checkRes && checkRes.message) || 'Printer offline'));
                  }
                }
              } catch (_) {
              } finally {
                recheckBtn.disabled = false;
                recheckBtn.textContent = '🔄 Re-check';
              }
            });
          }

          startPrinterPolling();

          await modalExport.error(
            'Printer Connection Required',
            `End of Day is BLOCKED: The cashier receipt printer is not connected or is offline.\n\nStatus: ${pMsg}\n\nPlease plug in USB, turn on, and verify paper. System is auto-detecting status (or click Re-check).`,
            { confirmLabel: 'I understand' }
          );
          return false;
        }
      } catch (pErr) {
        // Fallthrough if status check API fails
      }

      progressBlock.style.display = 'block';
      progressBlock.innerHTML = `
        <div style="display: flex; flex-direction: column; gap: 0.3rem;">
          <div style="display: flex; align-items: center; justify-content: space-between; color: var(--color-primary); font-weight: 600;">
            <span style="display: flex; align-items: center; gap: 0.4rem; font-size: 0.8rem;">
              <svg class="spin" style="animation: spin 1s linear infinite; flex-shrink: 0;" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="2" x2="12" y2="6"></line><line x1="12" y1="18" x2="12" y2="22"></line><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"></line><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"></line><line x1="2" y1="12" x2="6" y2="12"></line><line x1="18" y1="12" x2="22" y2="12"></line><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"></line><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"></line></svg>
              <span id="queueProgressStage">Processing End of Day for ${escapeHtml(dateStr)}...</span>
            </span>
            <span id="queueProgressPct" style="font-size: 0.78rem; font-weight: 700; color: var(--color-primary); font-family: var(--font-mono, monospace);">0%</span>
          </div>
          <div style="width: 100%; height: 6px; background: rgba(255,255,255,0.08); border-radius: 3px; overflow: hidden; margin: 0.2rem 0;">
            <div id="queueProgressBar" style="width: 0%; height: 100%; background: linear-gradient(90deg, var(--color-primary, #14B8A6), #10b981); transition: width 0.2s ease; border-radius: 3px;"></div>
          </div>
        </div>
      `;

      const barEl = progressBlock.querySelector('#queueProgressBar');
      const pctEl = progressBlock.querySelector('#queueProgressPct');
      const stageEl = progressBlock.querySelector('#queueProgressStage');

      const updateProgressUI = (pct, stage) => {
        if (barEl) barEl.style.width = pct + '%';
        if (pctEl) pctEl.textContent = pct + '%';
        if (stageEl && stage) stageEl.textContent = stage;
      };

      try {
        // 1. Try Asynchronous SSE Progress Streaming
        let asyncTaskStarted = false;
        try {
          const initRes = await api.post('/api/eod/start_async', { date: dateStr });
          if (initRes && initRes.success && initRes.task_id) {
            asyncTaskStarted = true;
            const taskId = initRes.task_id;
            const success = await new Promise((resSolve) => {
              const evtSource = new EventSource('/api/eod/stream?task_id=' + taskId);
              evtSource.onmessage = async (evt) => {
                try {
                  const data = JSON.parse(evt.data);
                  if (data.pct != null) updateProgressUI(data.pct, data.stage);
                  if (data.status === 'completed') {
                    evtSource.close();
                    const resData = data.result || {};
                    const zCounterText = resData.z_counter ? ` (Z-Counter: #${resData.z_counter})` : '';

                    // Auto-print Z-Reading receipt
                    let printNote = '';
                    try {
                      const printRes = await api.get(`/print_zreading?from_date=${dateStr}&to_date=${dateStr}&is_initial=true`);
                      if (printRes && (printRes.success || printRes.status === 'success')) {
                        printNote = ' 🖨️ Z-Reading printed!';
                      } else {
                        const pErrStr = (printRes && (printRes.error || printRes.message)) || 'Printer connection failed';
                        printNote = ` ⚠️ Print error (${escapeHtml(pErrStr)}) <button type="button" class="btn-retry-zprint" data-date="${escapeHtml(dateStr)}" style="padding: 0.15rem 0.45rem; font-size: 0.72rem; font-weight: 700; border-radius: 4px; border: 1px solid #f59e0b; background: rgba(245, 158, 11, 0.15); color: #f59e0b; cursor: pointer; margin-left: 0.3rem;">🖨️ Retry Print</button>`;
                      }
                    } catch (pErr) {
                      const pErrStr = (pErr && pErr.message) || 'Printer connection failed';
                      printNote = ` ⚠️ Print error (${escapeHtml(pErrStr)}) <button type="button" class="btn-retry-zprint" data-date="${escapeHtml(dateStr)}" style="padding: 0.15rem 0.45rem; font-size: 0.72rem; font-weight: 700; border-radius: 4px; border: 1px solid #f59e0b; background: rgba(245, 158, 11, 0.15); color: #f59e0b; cursor: pointer; margin-left: 0.3rem;">🖨️ Retry Print</button>`;
                    }

                    const isSftpEnabled = resData.rlc_enabled === true || resData.rlc_enabled === 1 || resData.rlc_enabled === 'true' || resData.rlc_enabled === '1';
                    let sftpNote = '';
                    if (!isSftpEnabled || resData.rlc_skipped) {
                      sftpNote = ' (RLC SFTP not set)';
                    } else if (resData.rlc_transfer_success === true || resData.rlc_transfer_success === 'true') {
                      sftpNote = ' 📤 RLC SFTP uploaded!';
                    } else {
                      const msg = String(resData.rlc_message || resData.error_message || resData.message || '').toLowerCase();
                      if (resData.no_internet || msg.includes('no internet') || msg.includes('offline') || msg.includes('network')) {
                        sftpNote = ' ⚠️ RLC SFTP (No Internet - Queued for Auto-Retry)';
                      } else if (resData.timeout_error || msg.includes('server') || msg.includes('connect') || msg.includes('host') || msg.includes('timeout') || msg.includes('ssh') || msg.includes('unreachable')) {
                        sftpNote = ' ⚠️ RLC SFTP (Server Unavailable - Queued for Auto-Retry)';
                      } else if (msg.includes('auth') || msg.includes('login') || msg.includes('password')) {
                        sftpNote = ' ⚠️ RLC SFTP (Authentication Failed)';
                      } else {
                        sftpNote = ' ⚠️ RLC SFTP (Upload Error - Queued for Auto-Retry)';
                      }
                    }

                    progressBlock.innerHTML = `
                      <div style="display: flex; align-items: center; gap: 0.5rem; color: #10b981; font-weight: 700;">
                        <span>✅ Finalized ${escapeHtml(dateStr)} successfully${escapeHtml(zCounterText)}.${printNote}${escapeHtml(sftpNote)}</span>
                      </div>
                    `;

                    const retryZBtn = progressBlock.querySelector('.btn-retry-zprint');
                    if (retryZBtn) {
                      retryZBtn.addEventListener('click', async () => {
                        retryZBtn.disabled = true;
                        retryZBtn.textContent = 'Printing...';
                        try {
                          const pRes = await api.get(`/print_zreading?from_date=${dateStr}&to_date=${dateStr}&is_initial=true`);
                          if (pRes && (pRes.success || pRes.status === 'success')) {
                            if (typeof notify !== 'undefined' && notify.success) notify.success(`Z-Reading printed for ${dateStr}!`);
                            retryZBtn.outerHTML = '<span style="color: #10b981;">🖨️ Z-Reading printed!</span>';
                          } else {
                            const errM = (pRes && (pRes.error || pRes.message)) || 'Printer connection failed';
                            if (typeof notify !== 'undefined' && notify.error) notify.error(errM);
                            await modalExport.error('Printer Connection Error', `Could not print Z-Reading for ${dateStr}:\n\n${errM}\n\nPlease verify that your receipt printer is connected and powered on.`, { confirmLabel: 'I understand' });
                          }
                        } catch (rErr) {
                          const errM = (rErr && rErr.message) || 'Printer connection failed';
                          if (typeof notify !== 'undefined' && notify.error) notify.error(errM);
                          await modalExport.error('Printer Connection Error', `Could not print Z-Reading for ${dateStr}:\n\n${errM}\n\nPlease verify that your receipt printer is connected and powered on.`, { confirmLabel: 'I understand' });
                        } finally {
                          if (retryZBtn && retryZBtn.isConnected) {
                            retryZBtn.disabled = false;
                            retryZBtn.textContent = '🖨️ Retry Print';
                          }
                        }
                      });
                    }

                    dates = dates.filter(d => (typeof d === 'string' ? d : d.date) !== dateStr);
                    renderDates();
                    resSolve(true);
                  } else if (data.status === 'failed') {
                    evtSource.close();
                    const errMsg = data.error || 'Processing failed';
                    progressBlock.innerHTML = `
                      <div style="display: flex; align-items: center; gap: 0.5rem; color: #ef4444; font-weight: 700;">
                        <span>❌ Failed: ${escapeHtml(errMsg)}</span>
                      </div>
                    `;
                    resSolve(false);
                  }
                } catch (_) {}
              };
              evtSource.onerror = () => {
                evtSource.close();
                resSolve(null); // trigger fallback
              };
            });

            if (success !== null) return success;
          }
        } catch (_) {}

        // Fallback: Synchronous EOD Processing
        updateProgressUI(40, `Finalizing ${dateStr}...`);
        const res = await api.post('/perform_end_of_day_for_date', { date: dateStr });
        updateProgressUI(90, `Finalizing Z-Reading & printing...`);
        if (res && (res.success || res.status === 'success')) {
          const zCounterText = res.z_counter ? ` (Z-Counter: #${res.z_counter})` : '';

          let printNote = '';
          try {
            const printRes = await api.get(`/print_zreading?from_date=${dateStr}&to_date=${dateStr}&is_initial=true`);
            if (printRes && (printRes.success || printRes.status === 'success')) {
              printNote = ' 🖨️ Z-Reading slip printed!';
            } else {
              const pErrStr = (printRes && (printRes.error || printRes.message)) || 'Printer connection failed';
              printNote = ` ⚠️ Print error (${escapeHtml(pErrStr)}) <button type="button" class="btn-retry-zprint-sync" data-date="${escapeHtml(dateStr)}" style="padding: 0.15rem 0.45rem; font-size: 0.72rem; font-weight: 700; border-radius: 4px; border: 1px solid #f59e0b; background: rgba(245, 158, 11, 0.15); color: #f59e0b; cursor: pointer; margin-left: 0.3rem;">🖨️ Retry Print</button>`;
            }
          } catch (_pErr) {
            const pErrStr = (_pErr && _pErr.message) || 'Printer connection failed';
            printNote = ` ⚠️ Print error (${escapeHtml(pErrStr)}) <button type="button" class="btn-retry-zprint-sync" data-date="${escapeHtml(dateStr)}" style="padding: 0.15rem 0.45rem; font-size: 0.72rem; font-weight: 700; border-radius: 4px; border: 1px solid #f59e0b; background: rgba(245, 158, 11, 0.15); color: #f59e0b; cursor: pointer; margin-left: 0.3rem;">🖨️ Retry Print</button>`;
          }

          const isFallbackSftpEnabled = res.rlc_enabled === true || res.rlc_enabled === 1 || res.rlc_enabled === 'true' || res.rlc_enabled === '1';
          let sftpNote = '';
          if (res.rlc_skipped || !isFallbackSftpEnabled) {
            sftpNote = ' (RLC SFTP not set)';
          } else if (res.rlc_transfer_success === true || res.rlc_transfer_success === 'true' || res.transfer_success === true) {
            sftpNote = ' 📤 RLC SFTP uploaded!';
          } else {
            const msg = String(res.rlc_message || res.message || res.error || res.error_message || '').toLowerCase();
            if (res.no_internet || msg.includes('no internet') || msg.includes('offline')) {
              sftpNote = ' ⚠️ RLC SFTP (No Internet - Queued for Auto-Retry)';
            } else if (res.timeout_error || msg.includes('server') || msg.includes('connect') || msg.includes('host') || msg.includes('timeout') || msg.includes('ssh') || msg.includes('unreachable')) {
              sftpNote = ' ⚠️ RLC SFTP (Server Unavailable - Queued for Auto-Retry)';
            } else {
              sftpNote = ' ⚠️ RLC SFTP (Upload Error - Queued for Auto-Retry)';
            }
          }

          updateProgressUI(100, `Completed!`);
          progressBlock.innerHTML = `
            <div style="display: flex; align-items: center; gap: 0.5rem; color: #10b981; font-weight: 700;">
              <span>✅ Finalized ${escapeHtml(dateStr)} successfully${escapeHtml(zCounterText)}.${printNote}${escapeHtml(sftpNote)}</span>
            </div>
          `;

          const retrySyncBtn = progressBlock.querySelector('.btn-retry-zprint-sync');
          if (retrySyncBtn) {
            retrySyncBtn.addEventListener('click', async () => {
              retrySyncBtn.disabled = true;
              retrySyncBtn.textContent = 'Printing...';
              try {
                const pRes = await api.get(`/print_zreading?from_date=${dateStr}&to_date=${dateStr}&is_initial=true`);
                if (pRes && (pRes.success || pRes.status === 'success')) {
                  if (typeof notify !== 'undefined' && notify.success) notify.success(`Z-Reading printed for ${dateStr}!`);
                  retrySyncBtn.outerHTML = '<span style="color: #10b981;">🖨️ Z-Reading printed!</span>';
                } else {
                  const errM = (pRes && (pRes.error || pRes.message)) || 'Printer connection failed';
                  if (typeof notify !== 'undefined' && notify.error) notify.error(errM);
                  await modalExport.error('Printer Connection Error', `Could not print Z-Reading for ${dateStr}:\n\n${errM}\n\nPlease verify that your receipt printer is connected and powered on.`, { confirmLabel: 'I understand' });
                }
              } catch (rErr) {
                const errM = (rErr && rErr.message) || 'Printer connection failed';
                if (typeof notify !== 'undefined' && notify.error) notify.error(errM);
                await modalExport.error('Printer Connection Error', `Could not print Z-Reading for ${dateStr}:\n\n${errM}\n\nPlease verify that your receipt printer is connected and powered on.`, { confirmLabel: 'I understand' });
              } finally {
                if (retrySyncBtn && retrySyncBtn.isConnected) {
                  retrySyncBtn.disabled = false;
                  retrySyncBtn.textContent = '🖨️ Retry Print';
                }
              }
            });
          }

          dates = dates.filter(d => (typeof d === 'string' ? d : d.date) !== dateStr);
          renderDates();

          if (dates.length === 0) {
            progressBlock.innerHTML = `
              <div style="display: flex; align-items: center; gap: 0.5rem; color: #10b981; font-weight: 700;">
                <span>✅ All prior business dates completed! Register is now ready.</span>
              </div>
            `;
            if (typeof onRefresh === 'function') {
              try { await onRefresh(); } catch (_) {}
            }
            setTimeout(() => {
              resolve(true);
            }, 1000);
          }
          return true;
        } else {
          const errMsg = (res && (res.error || res.message)) || 'Error processing date';
          progressBlock.innerHTML = `
            <div style="display: flex; align-items: center; gap: 0.5rem; color: #ef4444; font-weight: 700;">
              <span>❌ Failed: ${escapeHtml(errMsg)}</span>
            </div>
          `;
          return false;
        }
      } catch (err) {
        const errMsg = err?.message || 'Error processing date';
        progressBlock.innerHTML = `
          <div style="display: flex; align-items: center; gap: 0.5rem; color: #ef4444; font-weight: 700;">
            <span>❌ Failed: ${escapeHtml(errMsg)}</span>
          </div>
        `;
        return false;
      } finally {
        isProcessing = false;
        processAllBtn.disabled = false;
        processAllBtn.style.opacity = '1';
        const updatedBtns = queueList.querySelectorAll('.btn-process-single');
        updatedBtns.forEach(b => { b.disabled = false; b.style.opacity = '1'; });
      }
    }

    processAllBtn.addEventListener('click', async () => {
      if (isProcessing || !dates.length) return;
      while (dates.length > 0) {
        const nextItem = dates[0];
        const nextDateStr = typeof nextItem === 'string' ? nextItem : (nextItem?.date || '');
        if (!nextDateStr) {
          dates.shift();
          renderDates();
          continue;
        }
        const success = await processSingleDate(nextDateStr);
        if (!success) {
          // Stop immediately on error to prevent endless retry loop
          break;
        }
        await new Promise(r => setTimeout(r, 400));
      }
    });

    let settled = false;
    function resolve(value) {
      if (settled) return;
      settled = true;
      stopPrinterPolling();
      window.closeModal(overlay);
      overlay.remove();
      document.removeEventListener('keydown', onKeydown);
      onSettle(value);
    }

    function onKeydown(event) {
      if (!dates.length && event.key === 'Escape' && overlay.classList.contains('open')) {
        resolve(true);
      }
    }

    closeBtn.addEventListener('click', () => resolve(true));
    document.addEventListener('keydown', onKeydown);

    window.openModal(overlay.id);
  });
}

export function logoutConfirmModal() {
  return confirm(
    'End Shift & Sign Out',
    'Are you sure you want to end your shift and log out? This will automatically print your Cashier Accountability report.',
    {
      confirmLabel: 'Confirm',
      cancelLabel: 'Cancel',
      danger: true
    }
  );
}

const modalExport = { open, alert, confirm, error, warning, success, prompt, addTableModal, tableDetailsModal, confirmEodModal, dailyBundleModal, progressEodModal, eodRequiredModal, logoutConfirmModal };
if (typeof window !== 'undefined') {
  window.modal = modalExport;
}
export default modalExport;
