/* =============================================================================
   core/toast.js — toast host + API (replaces SweetAlert2 toasts)
   -----------------------------------------------------------------------------
   The vendored njX CSS styles `.toast .toast-<variant>` pills, but the
   bundled njx.js showToast() emits `.lib-toast` markup that njX never
   styles — so this wrapper builds the correct classes directly.
   The host is a fixed bottom-right stack, created on demand (the Step 5
   shell may pre-render an empty <div id="toast-host"></div>).
   ========================================================================== */
const VARIANTS = {
  success: 'success',
  error: 'error',
  warning: 'warning',
  info: 'primary',
  dark: 'dark',
};
const DEFAULT_DURATION = 2800;

function ensureStyles() {
  if (typeof document === 'undefined' || document.getElementById('toast-injected-css')) return;
  const style = document.createElement('style');
  style.id = 'toast-injected-css';
  style.textContent = `
    #toast-host {
      position: fixed !important;
      right: 1rem !important;
      bottom: 1rem !important;
      z-index: 9500 !important;
      display: flex !important;
      flex-direction: column !important;
      align-items: flex-end !important;
      gap: 0.5rem !important;
      pointer-events: none !important;
    }
    #toast-host .toast,
    .toast {
      display: inline-flex !important;
      align-items: center !important;
      gap: 0.6rem !important;
      padding: 0.65rem 1rem !important;
      border-radius: 8px !important;
      font-family: inherit !important;
      font-size: 0.85rem !important;
      font-weight: 600 !important;
      color: #ffffff !important;
      background: #0f172a !important;
      border: 1px solid #1e293b !important;
      box-shadow: 0 10px 25px rgba(0, 0, 0, 0.45) !important;
      pointer-events: auto !important;
      cursor: pointer !important;
      user-select: none !important;
      max-width: 420px !important;
    }
    #toast-host .toast .toast-message,
    .toast .toast-message {
      color: #ffffff !important;
    }
    #toast-host .toast-success,
    .toast-success {
      background: #064e3b !important;
      border: 1px solid #059669 !important;
      color: #ffffff !important;
    }
    #toast-host .toast-error,
    .toast-error {
      background: #7f1d1d !important;
      border: 1px solid #dc2626 !important;
      color: #ffffff !important;
    }
    #toast-host .toast-warning,
    .toast-warning {
      background: #78350f !important;
      border: 1px solid #d97706 !important;
      color: #ffffff !important;
    }
    #toast-host .toast-primary,
    .toast-primary,
    #toast-host .toast-info,
    .toast-info {
      background: #1e293b !important;
      border: 1px solid #3b82f6 !important;
      color: #ffffff !important;
    }
  `;
  document.head.appendChild(style);
}

function host() {
  ensureStyles();
  let el = document.getElementById('toast-host');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast-host';
    document.body.appendChild(el);
  }
  return el;
}

function escapeHtml(str) {
  return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

const ICONS = {
  success: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#34d399" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;"><polyline points="20 6 9 17 4 12"></polyline></svg>`,
  error: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#f87171" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`,
  warning: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fbbf24" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>`,
  info: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#60a5fa" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>`,
  dark: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>`
};

export function toast(message, variant = 'success', duration = DEFAULT_DURATION) {
  ensureStyles();
  const v = VARIANTS[variant] ? variant : 'dark';
  const el = document.createElement('div');
  el.className = 'toast toast-' + (VARIANTS[v] || 'dark');
  el.setAttribute('role', 'status');
  el.style.cssText = 'color: #ffffff !important; pointer-events: auto; cursor: pointer;';
  el.innerHTML = `${ICONS[v] || ICONS.dark}<span class="toast-message" style="color: #ffffff !important;">${escapeHtml(message)}</span>`;
  host().appendChild(el);

  let timer = setTimeout(dismiss, duration);

  function dismiss() {
    clearTimeout(timer);
    el.style.transition = 'opacity .2s ease, transform .2s ease';
    el.style.opacity = '0';
    el.style.transform = 'translateY(6px)';
    setTimeout(() => el.remove(), 220);
  }

  el.addEventListener('click', dismiss);
  return dismiss;
}

export const notify = {
  success: (message, duration) => toast(message, 'success', duration),
  error: (message, duration) => toast(message, 'error', duration),
  warning: (message, duration) => toast(message, 'warning', duration),
  info: (message, duration) => toast(message, 'info', duration),
  dark: (message, duration) => toast(message, 'dark', duration),
};

export default toast;
