/* =============================================================================
   core/spa.js — htmx SPA layer (the only place SPA rules live)
   -----------------------------------------------------------------------------
   Loaded as the LAST core module (type="module", after htmx.min.js).
   htmx is driven declaratively from shell.html:
       <body hx-boost="true" hx-target="#view-root" hx-swap="innerHTML">
   An explicit hx-target is respected even for boosted requests (verified in
   the vendored htmx source), so only #view-root is swapped — the shell,
   the SSE socket and the theme survive every navigation. Boosted requests
   push history automatically and send HX-Boosted: true.

   Rules implemented here:
   1. historyCacheSize = 0          — every back/forward refetches fresh,
                                      money data must never come from cache
   2. CSRF injection                 — htmx:configRequest -> X-CSRFToken
   3. Dirty-form guard               — htmx:beforeRequest blocks navigation
                                      while a form has unsaved edits
   4. View lifecycle                 — beforeSwap destroys the outgoing view
                                      module, afterSwap mounts the incoming
                                      one (lazy-import of js/pages/<page>.js)
   5. Area switch                    — data-view-meta carries the area
                                      ('pos' | 'admin'); applied to <html>
                                      so tokens.css typography follows
   6. Title + focus                  — document.title from view meta; focus
                                      moves to the view <h1> for a11y
   7. V1 fallback                    — a full <!DOCTYPE html> response (e.g.
                                      a login_required redirect target) is
                                      not swapped; the browser reloads it
   ========================================================================== */
import { getCsrfToken, api } from './api.js';
import modal from './modal.js';
import { notify } from './toast.js';
import store from './store.js';
import './export.js';

const VIEW_ROOT_SELECTOR = '#view-root';
const META_SELECTOR = 'template[data-view-meta]';

let currentView = null; /* { name, api } of the mounted page module */

/* ---------------------------------------------------------------------------
   1. Global htmx config
   ------------------------------------------------------------------------- */
if (window.htmx) {
  window.htmx.config.historyCacheSize = 0;
  window.htmx.config.scrollIntoViewOnBoost = false; /* focus-to-h1 handles the viewport */
  /* Fragments keep their <link rel="stylesheet"> tags: the default parser
     (DOMParser as text/html) relocates <link> into the parsed head, so
     per-view CSS never reaches the DOM after a swap. The template-fragment
     path preserves them; the browser fetches + applies the inserted tags. */
  window.htmx.config.useTemplateFragments = true;
}

/* ---------------------------------------------------------------------------
   1b. Sidebar shell state — rail below 1280px, expanded above; an explicit
   user choice persists in localStorage. Before JS runs, shell.css shows the
   same rail default on narrow screens, so there is no expanded flash.
   ------------------------------------------------------------------------- */
const SIDE_NAV_KEY = 'posSideNav';
const SIDE_NAV_NARROW = '(max-width: 1279px)';

function applySideNavState() {
  /* POS shell has no sidebar — skip silently when toggle is absent. */
  const toggle = document.querySelector('[data-side-toggle]');
  if (!toggle) return;

  const saved = localStorage.getItem(SIDE_NAV_KEY);
  const state =
    saved === 'expanded' || saved === 'rail'
      ? saved
      : window.matchMedia(SIDE_NAV_NARROW).matches
        ? 'rail'
        : 'expanded';
  document.body.dataset.sideNav = state;

  const expanded = state === 'expanded';
  toggle.setAttribute('aria-expanded', String(expanded));
  const label = expanded ? 'Collapse sidebar' : 'Expand sidebar';
  toggle.title = label;
  toggle.setAttribute('aria-label', label);
}

/* One delegated listener: htmx swaps never leak duplicate handlers.
   POS shell has no [data-side-toggle] — the guard in applySideNavState
   ensures this listener is a no-op when the sidebar is absent. */
document.addEventListener('click', (event) => {
  const logoutLink = event.target.closest('.js-logout-link, a[href="/logout"], a[href$="/logout"]');
  if (logoutLink) {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    const isCashier = document.body.dataset.userRole === 'cashier' ||
                      Boolean(document.querySelector('.pos-topbar')) ||
                      (document.querySelector('.pos-topbar-role')?.textContent.toLowerCase() || '').includes('cashier');

    if (!isCashier) {
      window.location.href = '/logout';
      return;
    }

    modal.logoutConfirmModal().then((confirmed) => {
      if (confirmed) {
        notify.success('Printing cashier accountability report...');
        api.post('/print_cashier_accountability', {})
          .then((res) => {
            if (res && (res.success || res.status === 'success')) {
              notify.success('Cashier accountability report printed.');
            } else {
              notify.error((res && (res.message || res.error)) || 'Could not print accountability report.');
            }
          })
          .catch(() => {
            notify.error('Could not print accountability report.');
          })
          .finally(() => {
            setTimeout(() => {
              window.location.href = '/logout';
            }, 600);
          });
      }
    });
    return;
  }

  const toggle = event.target.closest('[data-side-toggle]');
  if (toggle) {
    const next = document.body.dataset.sideNav === 'expanded' ? 'rail' : 'expanded';
    localStorage.setItem(SIDE_NAV_KEY, next);
    applySideNavState();
    return;
  }

  const dropToggle = event.target.closest('.side-dropdown-toggle');
  if (dropToggle) {
    event.preventDefault();
    const dropdown = dropToggle.closest('.side-dropdown');
    if (dropdown) {
      const isOpen = dropdown.classList.toggle('is-open');
      dropToggle.setAttribute('aria-expanded', String(isOpen));
    }
    return;
  }

  const subItem = event.target.closest('.side-dropdown-item');
  if (subItem) {
    const tab = subItem.dataset.tab;
    if (tab) {
      try {
        localStorage.setItem('pos_settings_active_tab', tab);
      } catch (e) {}

      const isSettings = window.location.pathname.replace(/\/+$/, '') === '/settings';
      if (isSettings) {
        event.preventDefault();
        if (typeof window.switchSettingsSection === 'function') {
          window.switchSettingsSection(tab, true);
        } else {
          const panes = document.querySelectorAll('.settings-pane');
          panes.forEach((p) => p.classList.remove('active'));
          const targetPane = document.querySelector(`#pane-${tab}`);
          if (targetPane) {
            targetPane.classList.add('active');
            if (window.history && window.history.replaceState) {
              window.history.replaceState(null, '', `#${tab}`);
            }
          }
          document.querySelectorAll('.side-dropdown-item').forEach((item) => {
            const isActive = item.dataset.tab === tab;
            item.classList.toggle('is-active', isActive);
            if (isActive) item.setAttribute('aria-current', 'page');
            else item.removeAttribute('aria-current');
          });
        }
      }
    }
  }
}, true);



/* ---------------------------------------------------------------------------
   2. CSRF parity — every htmx request carries the session token
   ------------------------------------------------------------------------- */
document.addEventListener('htmx:configRequest', (event) => {
  event.detail.headers['X-CSRFToken'] = getCsrfToken();
});

/* ---------------------------------------------------------------------------
   3. Dirty-form guard — mark forms on first edit; confirm before leaving
   ------------------------------------------------------------------------- */
/* ---------------------------------------------------------------------------
   3. Dirty-form guard — mark forms on first edit; confirm before leaving
   ------------------------------------------------------------------------- */
document.addEventListener(
  'input',
  (event) => {
    const form = event.target.closest('form');
    if (form && !form.dataset.dirty) form.dataset.dirty = '1';
  },
  true
);

document.addEventListener(
  'submit',
  (event) => {
    const form = event.target.closest('form') || event.target;
    if (form && form.dataset) {
      delete form.dataset.dirty;
    }
  },
  true
);

document.addEventListener('htmx:beforeRequest', (event) => {
  if (!event.detail.requestConfig.boosted) return; /* only guard navigation */
  const elt = event.detail.elt;
  if (elt && (elt.type === 'submit' || elt.closest('button[type="submit"]'))) return;
  const dirty = document.querySelector('form[data-dirty="1"]');
  if (!dirty) return;

  event.preventDefault();
  const config = event.detail.requestConfig;
  modal
    .confirm('Discard unsaved changes?', 'This form has unsaved edits.', {
      danger: true,
      confirmLabel: 'Discard',
    })
    .then((ok) => {
      if (!ok) return;
      delete dirty.dataset.dirty;
      window.htmx.ajax(config.verb, config.path, { source: event.detail.elt });
    });
});

/* ---------------------------------------------------------------------------
   3b. View transition animation — zoom-out old content, zoom-in new content.
   Only fires for boosted (SPA) navigation requests.
   Rapid clicks are safe: the flag prevents double-firing.
   --------------------------------------------------------------------------- */
let isAnimating = false;

/* ---------------------------------------------------------------------------
   3c. Cross-shell navigation — admin (shell.html) and POS (pos_shell.html)
   are separate shells with different chrome (sidebar vs top bar). When a
   boosted link crosses the shell boundary (area changes), the htmx fragment
   swap would produce broken layout. Detect this early and do a full page
   load instead — the browser fetches the correct shell from the server.
   ------------------------------------------------------------------------- */
let crossShellRedirect = false;

document.addEventListener('htmx:beforeSwap', (event) => {
  if (!event.detail.boosted) return;

  /* Cross-shell check: if the response targets a different area, do a
     full page load instead of an htmx fragment swap. */
  const currentArea = document.documentElement.dataset.area || 'admin';
  const xhr = event.detail.xhr;
  const body = xhr.responseText || '';
  const metaMatch = body.match(/data-view-meta[^>]*data-area="(\w+)"/);
  const newArea = metaMatch ? metaMatch[1] : currentArea;
  if (newArea !== currentArea) {
    crossShellRedirect = true;
    event.detail.shouldSwap = false;
    window.location.href = event.detail.pathInfo.requestPath;
    return;
  }

  const root = document.querySelector(VIEW_ROOT_SELECTOR);
  if (!root) return;

  /* Hide old content instantly so there's no blink during the swap. */
  root.style.opacity = '0';
  isAnimating = true;
});

document.addEventListener('htmx:afterSwap', (event) => {
  if (!event.detail.boosted || crossShellRedirect) return;
  const root = document.querySelector(VIEW_ROOT_SELECTOR);
  if (!root) return;

  /* Force the browser to commit opacity:0 before triggering the animation
     so the zoom-in starts from a truly invisible state (no flash). */
  void root.offsetHeight;

  root.style.opacity = '';
  root.classList.add('view-entering');
  root.addEventListener(
    'animationend',
    () => {
      root.classList.remove('view-entering');
      isAnimating = false;
    },
    { once: true },
  );
});

/* ---------------------------------------------------------------------------
   4 + 5. View lifecycle and area switch
   ------------------------------------------------------------------------- */
function readMeta() {
  const root = document.querySelector(VIEW_ROOT_SELECTOR);
  const meta = root ? root.querySelector(META_SELECTOR) : null;
  return {
    title: meta ? meta.dataset.title || '' : '',
    area: meta && meta.dataset.area ? meta.dataset.area : 'admin',
    page: meta ? meta.dataset.page || null : null,
    version: meta ? meta.dataset.version || '' : '',
  };
}

async function mountView(meta, path) {
  document.documentElement.dataset.area = meta.area;
  store.set('area', meta.area);
  if (meta.title) document.title = meta.title;
  syncChrome(meta, path);

  if (meta.page) {
    try {
      /* Versioned so page modules cache-bust like every other asset: the
         server pins /assets to max-age=86400 (immutable with ?v=), and an
         unversioned import URL would serve stale modules for a day. */
      const module = await import(`/assets/js/pages/${meta.page}.js?v=${meta.version}`);
      currentView = { name: meta.page, api: module.default };
      if (currentView.api && typeof currentView.api.mount === 'function') {
        const root = document.querySelector(VIEW_ROOT_SELECTOR);
        try {
          await currentView.api.mount(root);
        } catch (mountErr) {
          console.warn('[spa] non-fatal page mount warning for', meta.page, mountErr);
        }
      }
    } catch (error) {
      console.warn('[spa] view module import notice for', meta.page, error);
    }
  }

  /* Focus the view heading so keyboard + screen-reader users land on it. */
  const root = document.querySelector(VIEW_ROOT_SELECTOR);
  const heading = root && root.querySelector('h1');
  if (heading) {
    heading.setAttribute('tabindex', '-1');
    heading.focus({ preventScroll: true });
  }
}

/* ---------------------------------------------------------------------------
   4b. Chrome sync — topbar title + sidebar active state follow every swap
   (the server renders them for full loads; this keeps them true for
   htmx-swapped fragments too). Mirrors the Jinja active rules: path match.
   ------------------------------------------------------------------------- */
function syncChrome(meta, path) {
  /* Admin shell topbar title */
  const titleNode = document.getElementById('topbar-title');
  if (titleNode) {
    titleNode.textContent = meta.title || titleNode.dataset.default || 'NEXGEN POS';
  }
  const normalized = (path || '/').replace(/\/+$/, '') || '/';
  const isSettings = normalized.startsWith('/settings');

  let activeItemToFocus = null;

  /* Admin shell sidebar active state */
  document.querySelectorAll('.side-link').forEach((link) => {
    if (link.classList.contains('side-dropdown-toggle')) return;
    const href = (link.getAttribute('href') || '/').replace(/\/+$/, '') || '/';
    const isActive = href === normalized;
    link.classList.toggle('is-active', isActive);
    if (isActive) {
      link.setAttribute('aria-current', 'page');
      if (!isSettings) activeItemToFocus = link;
    } else {
      link.removeAttribute('aria-current');
    }
  });

  /* Admin shell dropdown active state */
  document.querySelectorAll('.side-dropdown').forEach((dropdown) => {
    const toggle = dropdown.querySelector('.side-dropdown-toggle');
    if (isSettings) {
      dropdown.classList.add('is-open', 'is-active');
      if (toggle) {
        toggle.classList.add('is-active');
        toggle.setAttribute('aria-expanded', 'true');
      }
    } else {
      dropdown.classList.remove('is-active');
      dropdown.classList.remove('is-open');
      if (toggle) {
        toggle.classList.remove('is-active');
        toggle.setAttribute('aria-expanded', 'false');
      }
    }

    let activeTab = (window.location.hash || '').replace('#', '').replace('pane-', '');
    if (!activeTab && window.location.href.includes('#')) {
      activeTab = (window.location.href.split('#')[1] || '').replace('pane-', '');
    }
    if (!activeTab) {
      try {
        const saved = localStorage.getItem('pos_settings_active_tab');
        if (saved) activeTab = saved;
      } catch (e) {}
    }
    activeTab = activeTab || 'receipt';

    if (isSettings && typeof window.switchSettingsSection === 'function') {
      window.switchSettingsSection(activeTab, false);
    } else {
      dropdown.querySelectorAll('.side-dropdown-item').forEach((item) => {
        const itemTab = item.dataset.tab;
        const isActive = isSettings && itemTab === activeTab;
        item.classList.toggle('is-active', isActive);
        if (isActive) {
          item.setAttribute('aria-current', 'page');
          activeItemToFocus = item;
        } else {
          item.removeAttribute('aria-current');
        }
      });
    }
  });

  /* POS shell topbar nav active state */
  document.querySelectorAll('.pos-nav-link').forEach((link) => {
    const href = (link.getAttribute('href') || '/').replace(/\/+$/, '') || '/';
    const isActive = href === normalized;
    link.classList.toggle('is-active', isActive);
    if (isActive) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });

  if (activeItemToFocus) {
    activeItemToFocus.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    setTimeout(() => {
      try {
        activeItemToFocus.focus({ preventScroll: true });
      } catch (e) {}
    }, 50);
  }
}

function destroyView() {
  if (!currentView) return;
  try {
    if (typeof currentView.api.destroy === 'function') currentView.api.destroy();
  } catch (error) {
    console.error('[spa] view destroy failed for', currentView.name, error);
  }
  currentView = null;
}

document.addEventListener('htmx:beforeSwap', (event) => {
  if (!event.detail.boosted || crossShellRedirect) return;
  destroyView();
});

document.addEventListener('htmx:afterSwap', (event) => {
  if (!event.detail.boosted || crossShellRedirect) return;
  const meta = readMeta();
  if (!meta.title && !meta.page && !meta.area) {
    /* Fragment without view meta — treat as a broken swap and reload. */
    window.location.href = event.detail.pathInfo.requestPath;
    return;
  }
  mountView(meta, event.detail.pathInfo.requestPath);
});

/* ---------------------------------------------------------------------------
   7. V1 fallback — full-document responses are not swapped into the shell
   ------------------------------------------------------------------------- */
document.addEventListener('htmx:beforeSwap', (event) => {
  if (!event.detail.boosted || crossShellRedirect) return;
  const xhr = event.detail.xhr;
  const contentType = xhr.getResponseHeader('content-type') || '';
  if (
    contentType.includes('text/html') &&
    xhr.responseText.trimStart().startsWith('<!DOCTYPE')
  ) {
    /* e.g. a login_required redirect chain ended on a legacy full page. */
    event.detail.shouldSwap = false;
    window.location.href = xhr.responseURL || event.detail.pathInfo.requestPath;
  }
});

/* ---------------------------------------------------------------------------
   Boot — the initial full load (deep link) mounts its view module too
   ------------------------------------------------------------------------- */
(function boot() {
  applySideNavState();
  const root = document.querySelector(VIEW_ROOT_SELECTOR);
  if (root && root.querySelector(META_SELECTOR)) {
    mountView(readMeta(), window.location.pathname);
  }
})();

window.POS = window.POS || {};
window.POS.spa = {
  get current() {
    return currentView;
  },
};
