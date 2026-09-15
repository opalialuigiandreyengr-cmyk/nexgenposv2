# POS_V2 — Architecture

## 1. Architecture Overview

```
┌────────────────────────────────────────────────────────────────────┐
│                     Desktop shell (unchanged)                      │
│        app.py · pos_gui_launcher.py · pywebview · waitress         │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ creates
┌──────────────────────────────▼─────────────────────────────────────┐
│              Flask app — website.create_app()  [UNCHANGED]         │
│  CSRF middleware · sessions · SQLite WAL · schema auto-repair ·    │
│  blueprints: auth / main / orders / sales_reports / eod (~150 rts) │
├────────────────────────────────────────────────────────────────────┤
│  POS_V2/app.py wraps the app AFTER creation (no legacy edits):     │
│    app.jinja_loader = ChoiceLoader([                               │
│        FileSystemLoader(POS_V2/templates),      ← new first        │
│        app.jinja_loader,                        ← V1 fallback      │
│    ])                                                              │
│    + Blueprint('assets', url_prefix='/assets')                      │
│      → /assets/... (static) and /assets/vendor/... (libs)          │
├────────────────────────────────────────────────────────────────────┤
│ Presentation layer (the ONLY thing that changes)                   │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ SPA SHELL — parent shell.html loaded once: navbar, theme,      │  │
│  │ toasts, SSE client, htmx-boosted links, outlet               │  │
│  │ <main id="view-root">                                        │  │
│  │   └─ CHILD VIEWS — server-rendered HTML fragments swapped    │  │
│  │      in-place by htmx (hx-boost + HX-Request fragment mode)  │  │
│  └──────────────────────────────────────────────────────────────┘  │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │ templates  │  │  static     │  │ vendor/                  │  │
│  │ Jinja pages │  │ js/css/svg   │  │ njx (njx-ui@1.1.4 CSS) + │  │
│  │             │  │              │  │ htmx (htmx@1.9.12)       │  │
│  └─────────────┘  └──────────────┘  └──────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

**Why this integration wins**

1. **Zero backend diff.** Same route handlers render the new templates, so
   request validation, CSRF, permissions, DB writes, printing, and logging
   are bit-identical to today.
2. **Per-page migration.** A screen is “migrated” simply by adding a
   same-named template to `templates/`; deleting it restores V1 for that
   page instantly.
3. **One process, one DB.** No parallel servers fighting over SQLite;
   sessions and login work across both UIs during transition.
4. **Kill switch.** `POS_UI_SHELL=0` skips the loader override entirely.
5. **SPA experience.** The parent shell loads once per login; every later
   navigation is a declarative htmx swap of the child-view fragment — warm
   CSS/JS, no font re-races, live SSE feed never reconnects (§4).

`POS_V2/app.py` (implemented in Phase 1 — see `tools/check_override.py`
for the automated verification of everything below):

```python
import os
from jinja2 import ChoiceLoader, FileSystemLoader
from flask import Blueprint, send_from_directory
from website import create_app          # existing package — untouched

HERE = os.path.dirname(os.path.abspath(__file__))

TEMPLATES_DIR = os.path.join(HERE, "templates")
STATIC_DIR = os.path.join(HERE, "static")
VENDOR_DIR = os.path.join(HERE, "vendor")

def build_shell_app():
    app = create_app()
    if os.environ.get("POS_UI_SHELL", "1") == "1":
        shell_loader = FileSystemLoader(TEMPLATES_DIR)
        app.jinja_loader = ChoiceLoader([shell_loader, app.jinja_loader])

        # Explicit routes, not Blueprint static_folder: sibling routes
        # would otherwise deploy WITHOUT the /assets prefix.
        assets = Blueprint("assets", __name__, url_prefix="/assets")
        @assets.route("/<path:filename>")
        def static_files(filename):
            return send_from_directory(STATIC_DIR, filename)
        @assets.route("/vendor/<path:filename>")
        def vendor_files(filename):
            return send_from_directory(VENDOR_DIR, filename)
        app.register_blueprint(assets)

        # ASSET_VERSION + asset(filename) helper (cache-busting
        # ?v= parity with the legacy STATIC_ASSET_VERSION hook)
    return app
```

Templates call `url_for('static', ...)` for legacy assets (still allowed)
and `asset('…')` (the context-processor helper → `/assets/…?v=…`)
for new ones; vendored libs use `asset('vendor/njx/…')` and
`asset('vendor/htmx/…')`. Jinja globals injected by the existing
context processor (`csrf_token`, `permissions`, `has_permission`,
`rlc_enabled`) are available unchanged.

---

## 2. Directory Structure

```
POS_V2/
├── app.py                     # V2 entry: wraps website.create_app()
├── README.md
├── docs/                      # These planning documents
├── vendor/
│   ├── njx/                   # njx-ui@1.1.4 (npm pack → extracted)
│   │   ├── njx.min.css        #   full bundle (~308 KB, gz-friendly)
│   │   ├── njx.js             #   optional helpers (modal/sidebar/toast…)
│   │   └── LICENSE
│   └── htmx/                  # htmx@1.9.12 (npm pack → extracted)
│       ├── htmx.min.js        #   ~14 KB gz — the SPA swap engine
│       └── LICENSE
├── templates/
│   ├── shell.html             # SPA shell (NOT base.html — legacy children
│   │                          #   must never resolve it): njX <head> +
│   │                          #   theme boot, role nav, hosts + flash
│   │                          #   bridge, hx-boost on <body>, outlet
│   │                          #   <main id="view-root">; fragment mode
│   │                          #   when the HX-Request header is present ✓
│   ├── login.html           # standalone pre-auth page (extends nothing;
│   │                          #   shell serves authenticated chrome only) ✓
│   │                          #   design: split instrument panel, amber rail
│   ├── home.html            # role-tile Start board (shell child) ✓
│   │                          #   design: Operational Ticker signature
│   ├── dashboard.html · eod_closed.html
│   ├── pos.html · orders.html · all_orders.html · order_details.html
│   ├── settlement.html · add-items.html · table_management.html
│   ├── end_of_day.html · transfer_history.html
│   ├── products.html · categories.html · users.html · settings.html
│   ├── backup.html · data_retention.html · misc.html
│   ├── activity_logs.html · audit_logs.html
│   ├── sales-book-report.html · *-sales-book pages (6)
│   ├── kiosk.html · kiosk/ …  (only if kiosk BP gets registered)
│   └── partials/
│       ├── _navbar.html       # role-aware top nav (cashier invariant!) ✓
│       ├── _icons.html        # inline SVG icon macros ✓
│       ├── _toasts.html · _modals.html · _confirm.html
│       ├── _table.html        # sortable/filterable data-table macro
│       ├── _pagination.html · _empty-state.html · _skeletons.html
│       └── _icons.html        # inline SVG sprite macros
├── static/
│   ├── css/
│   │   ├── tokens.css         # brand mapping onto njX vars (--color-*) +
│   │   │                      #   area typography (admin/pos, §6)
│   │   ├── shell.css          # SPA shell frame (layout, tokens-only) ✓
│   │   ├── components.css     # POS-specific composites (keypad, cart,
│   │   │                      #   floor-plan canvas, receipt preview)
│   │   ├── admin/<page>.css   # backoffice sheets (Space Grotesk area) —
│   │   │                      #   login.css ✓ home.css ✓
│   │   └── pos/<page>.css     # POS sheets (mono area), tokens-only
│   ├── js/
│   │   ├── core/              # framework-free ES modules
│   │   │   ├── api.js         # fetch wrapper: CSRF, JSON, errors (non-htmx calls)
│   │   │   ├── spa.js         # htmx bootstrap & SPA rules: CSRF header
│   │   │   │                  #   injection, history cache OFF, view
│   │   │   │                  #   lifecycle (mount/destroy via htmx
│   │   │   │                  #   events), dirty-form guard, redirect
│   │   │   │                  #   handling, focus/a11y on swap, V1 fallback
│   │   │   ├── theme.js       # data-theme + posTheme persistence
│   │   │   ├── flash.js       # drains #flash-data bridge → toasts ✓
│   │   │   ├── sse.js         # EventSource wrapper w/ reconnect
│   │   │   ├── store.js       # tiny pub/sub store factory
│   │   │   ├── modal.js       # openModal/confirm/alert (SWAL parity)
│   │   │   ├── toast.js       # njxFireToast wrapper
│   │   │   └── icons.js       # SVG icon injector
│   │   └── pages/<page>.js    # one module per screen (≤500 LOC) —
│   │                          #   login.js ✓ home.js ✓ (mount/destroy)
│   ├── fonts/                 # vendored woff2: space-grotesk, jetbrains-mono, space-mono
│   └── img/                   # logos, brand assets
├── tools/
│   ├── parity_harness.py      # boots app, crawls V1 vs V2 routes,
│   │                          #   diffs template context + response codes
│   └── golden/                # seeded fixtures for money-math diffs
├── tests/                     # V2-only tests (Playwright smoke + unit)
└── NexgenPOS_ui.spec         # copy of PyInstaller spec + POS_V2 datas
```

Rules:

- `templates/**` must contain **no inline `<script>`/`<style>` blocks**
  beyond tiny Jinja-driven data bridges
  (`<script type="application/json" id="page-data">`), which is the sanctioned
  way to hand server-rendered context to ES modules.
- Every page template inherits `shell.html`. Blocks: `title`, `styles`
  (head extras, rare), `content`, `page_js`; attribute-only blocks
  `area` (`'admin'` default | `'pos'`) and `page_module` feed the view
  meta via `self.*()`. Page CSS goes at the TOP of the `content` block
  so it loads in both full and fragment mode (the browser fetches
  `<link>` tags inserted by swaps). The shell emits a
  `<template data-view-meta>` carrying the page title, `data-area` and
  `data-page` — the module `core/spa.js` lazy-imports `pages/<page>.js`
  and mounts it on every swap.
- Page CSS may only use njX tokens (`var(--…)`) plus layout primitives —
  never hard-coded colors.

## 3. Module Boundaries & Separation of Concerns

| Layer | Owns | Never does |
| --- | --- | --- |
| Flask routes (legacy) | Authz, validation, DB, printing, files | Know anything about V2 markup |
| Jinja templates | Structure, server-rendered state, role-gated visibility | Business math, direct fetch |
| `core/*.js` | HTTP, SSE, theme, dialogs, store plumbing | Page-specific logic |
| `pages/*.js` | One screen’s behavior, reads `#page-data`, calls `core/api` | Talks to other pages |
| `css/admin/*`, `css/pos/*` | Screen layout | Overrides njX internals |
| `vendor/njx` | Component classes & tokens | Edited — extend via `components.css` |

## 4. SPA Shell & htmx (parent / child views)

POS_V2 runs as a **single-page application with server-rendered child
views** (app-shell / MPV pattern) — no client-side framework, no build
toolchain, zero backend changes. The swap engine is **htmx 1.9.12**
(vendored, ~14 KB gz): declarative `hx-*` attributes replace hundreds of
lines of event-listening JS, which matters on a POS terminal that runs
for weeks on a low-latency LAN. One thin module, `core/spa.js`, holds
every SPA rule so templates stay markup-only.

### 4.1 Parent shell (loaded once per login)

`shell.html` renders the persistent parent:

- `nav` (role-aware, always-dark in both themes), theme toggle, toast
  host, modal host, flash bridge (`#flash-data` → `core/flash.js`),
  CSRF meta, font preloads; the printer-guard banner and global
  shortcuts arrive with their screen migrations;
- `<body hx-boost="true" hx-target="#view-root" hx-swap="innerHTML">` —
  every same-origin GET link and GET form becomes an SPA navigation that
  swaps only the outlet. An explicit `hx-target` is honored even for
  boosted requests (verified in the vendored htmx source), so the shell,
  the SSE socket and the theme survive every navigation;
- `<main id="view-root">` — the child-view outlet;
- shell-scoped services: `core/sse.js` (`/stream_orders` stays connected
  across every navigation), `core/theme.js`, `core/toast.js`, and the
  shell store (`session user/role`, `nav state`, `printer status`).

### 4.2 Child views (swapped without reload)

1. `hx-boost` intercepts same-origin GET links/forms; targets marked
   `data-full`, `download`, or `target="_blank"` are skipped
   (`hx-boost="false"` or htmx's built-in exclusions).
2. Boosted requests carry htmx's native `HX-Request: true` header.
3. **Fragment mode is template-side only:** when `shell.html` sees
   `request.headers.get('HX-Request')` it emits *only* the `content`
   block plus `<template data-view-meta>` carrying the page title,
   `data-area` (`'pos' | 'admin'`) and `data-page` —
   no `<html>`/`<head>` chrome. Jinja has access to `request`, so this
   needs **zero backend change**; every V2 template gets SPA support by
   simply extending `shell.html`. (Responses without the header remain
   full pages, so curl/deep-link/SEO behavior is unchanged.)
4. htmx swaps the fragment into `#view-root` and pushes history
   automatically. `core/spa.js` listens to `htmx:afterSwap` to apply
   `view-meta` (`document.title`), lazy-import the view module named by
   the meta's `data-page` and call its `mount()`, and move focus to the
   view's `<h1>`.
5. Back/forward trigger a fresh fetch (history cache disabled — §4.3).

### 4.3 SPA rules (`core/spa.js` — the only place they live)

- **CSRF parity.** `htmx:configRequest` injects the `X-CSRFToken` header
  from the shell meta, mirroring the V1 fetch wrapper in `base.js`.
- **Area switch.** Each view declares its area; `spa.js` applies it as
  `data-area="admin|pos"` on `<html>` after every swap so `tokens.css`
  flips the typography roles per area (§6) while both areas keep the
  single shared light/dark theme.
- **No client cache.** `htmx.config.historyCacheSize = 0`: every swap —
  including back/forward — fetches fresh HTML. On a LAN this costs
  nothing and eliminates stale-money-data bugs by construction.
- **Deep links & hard refresh always work:** without `HX-Request` the
  same route renders the full page. SPA is an enhancement, never a
  dependency.
- **Dirty guard.** `htmx:beforeRequest` handler checks the current view's
  registered unsaved state (cart, settlement wizard, floor-plan edits);
  blocks navigation behind an njX confirm modal.
- **Lifecycle hygiene.** `htmx:beforeSwap` runs the outgoing view's
  `destroy()` (timers, observers, listeners); shell-level services
  survive navigation.
- **Redirect handling.** htmx follows redirects natively; `spa.js`
  inspects the swapped response — shell-less HTML or the login view ⇒
  full `location` reload to `/login`; `eod_closed` ⇒ full navigation.
- **Non-SPA requests kept:** POST form submits (settlement, EOD) stay
  native unless explicitly `hx-post`-ed for a better flow; file downloads
  (exports/backups) use `data-full` so the browser handles them.
- **Mixed migration safe.** If a boosted request hits a not-yet-migrated
  V1 route (response lacks `#view-root`), `spa.js` performs a normal
  full-page load.
- **Multi-terminal invariant preserved:** each browser window/tab is its
  own SPA instance, exactly like V1.

## 5. State Management

No framework; a disciplined vanilla pattern:

1. **Shell-scoped state (SPA)** — survives view swaps: current user/role,
   nav active state, printer-guard status, the SSE subscription.
2. **View-scoped state** — created in `mount()`, discarded in `destroy()`
   (cart, floor-plan editor, settlement wizard).
3. **Server state** — single source of truth stays in the DB; views fetch
   via `api.js` and re-render sections. Live updates arrive through
   `sse.js` (`/stream_orders`) or the existing polling endpoints (printer
   guard, queue/processor status) with the same intervals as V1.
4. **Store factory** — `store.js`: `createStore(initial)` returns
   `{ get, set, subscribe }` with shallow-merge semantics. Persistence
   only where V1 does it (`localStorage` keys: `posTheme`,
   `eodProgressProfileV1` — same keys, so V1/V2 share user state).
5. **Form state** — native `FormData` + constraint validation; CSRF auto-
   injected by `api.js` (mirrors the V1 fetch wrapper in `base.js`).
6. **Cross-view transfer** — via URL params only (e.g. settle order N:
   `/settle_order/N`); no hidden global hand-offs, so deep links stay
   fully functional (matches V1 multi-window terminal behavior).

## 6. Theming

- Single attribute: `<html data-theme="…">` (njX-native).
- Boot script in `<head>` reads `localStorage['posTheme']` before first
  paint (same anti-flash strategy as V1’s `initializeTheme()`), mapping
  V1 storage values → njX themes: `'light'→light`, `'dark'→dark`,
  unset → light (the classic POS default); extended palette
  (blue/green/purple) offered in Settings later without backend change.
- Second axis — `<html data-area="admin|pos">` separates **backoffice**
  from **POS**: the shell sets it on full loads and `core/spa.js` after
  every htmx swap. It re-points the njX role tokens `--font-sans` /
  `--font-heading` per area; both areas share the one light/dark theme.
- Typography roles: backoffice = **Space Grotesk** (clean & analytical);
  POS = **JetBrains Mono** for UI text + **Space Mono** for display /
  headings (tactical & numerical); `--font-mono` maps to JetBrains Mono
  in both areas. All three families are vendored woff2 (offline-first).
- `tokens.css` maps brand colors (logo teal/slate family) onto
  `--color-primary`, `--color-accent`, neutrals, and both themes’
  surfaces/text/form tokens; radii keep the njX scale (5–20px).
- **Locked design language** lives in `.ulpi/design/DESIGN.md` (technical /
  utilitarian; amber = sole warm operational accent; Signature = the dark
  mono Operational Ticker on home + the amber rail / mono build tag on
  login; voice bans em-dashes). Elevation + motion tokens are LAYER 3 of
  `tokens.css` (`--shadow-sm/md/lg`, `--ring-focus`, `--ease-out`,
  `--dur-*`). Page CSS may declare literals once as custom props (e.g.
  `--tile` hues on home) and consume them via `color-mix`.
- Historical constraint honored: navbar keeps a dark surface in light mode
  (documented design decision) via `.nav-dark` on the shell nav.
- Cashier navbar invariant: V2 `_navbar.html` always renders Orders /
  All Orders / End of Day for the cashier role.

## 7. Component Mapping (Bootstrap → njX UI)

| V1 (Bootstrap) | V2 (njX UI) |
| --- | --- |
| `.row/.col-*`, containers | `.row/.col-*`, `.grid-auto-*`, `.grid-sidebar-l`, `.container-*` |
| `.card`, `.card-body` | `.card`, `.card-glass`, `.card-stat` (dashboard KPIs) |
| `.modal` + JS | `.lib-modal-overlay` + `openModal()/closeModal()` |
| SweetAlert2 confirm/alert | `core/modal.js` → `lib-modal-box` variants (is-error/is-warning…) |
| `.alert`, toasts | `.notification`, `.tip--*`, `njxFireToast()` via `core/toast.js` |
| `.navbar` | `.nav .nav-solid/.nav-dark` + role-aware partial |
| `.dropdown` | `<details class="dropdown">` |
| `.table` | `.table.is-striped.is-hoverable` + `_table.html` macro (sort/filter/pagination) |
| `.badge/.tag` | `.tag`, `.tag-filled-*`, `.badge`, `.dot` |
| `.accordion` | `.collapse/.accordion-flush` |
| `.nav-tabs` | `.tab-nav` (+ `.tab-nav-pills/.tab-nav-card`) |
| `.spinner-border` | `.loader`, `.skeleton` (loading states for tables/cards) |
| `.progress` | `.progress-bar` (EOD progress visuals) |
| Offcanvas/sidebar | `.sidebar .sidebar-push/.sidebar-mini` (admin menu) |
| FontAwesome icons | inline SVG via `_icons.html` (no webfont race) |
| Flatpickr (CDN) | native `input[type=date]` + `_daterange` partial (vendored, offline) |
| Chart.js | kept as-is (vendored already) for dashboard/reports |

## 8. Performance Strategy

- **Budgets:** per page ≤ 250 KB gz CSS+JS; POS screen TTI < 800 ms on
  target hardware; ≤ 2 CSS files and ≤ 2 JS modules per page.
- **SPA navigation:** view swap = one HTML fragment fetch + htmx swap of
  `#view-root` — shell CSS/JS stay warm and SSE/theme/navbar are
  untouched; swap cost ≤ 150 ms after fetch on target hardware.
- **Back/forward:** re-fetches a fresh page (htmx history cache disabled)
  — correct-by-default for money screens; negligible cost on a LAN.
- njX bundle served once with long-lived cache via the existing
  `?v=` cache-busting mechanism (`STATIC_ASSET_VERSION` also hashes
  `/static_v2` since the after-request hook keys on `?v`).
- `<link rel="preload">` for tokens.css + page CSS; `defer` for page JS;
  `type="module"` everywhere (no legacy IE support needed — desktop kiosk
  is Chromium-based).
- Skeleton loaders during SPA transitions and on data tables.
- SSE kept; polling intervals unchanged; no new background traffic.

## 9. Accessibility & Responsiveness

- WCAG 2.1 AA: focus-visible rings, focus-trapped modals, `aria-live`
  toasts, labeled inputs (`.field-label`), ≥ 44 px touch targets on
  cashier flows, contrast-checked token mapping.
- njX responsive tokens auto-scale typography/spacing down to 320 px;
  floor plan + POS grid get explicit breakpoints (≥1200 desktop,
  ≤768 stacked cart).
- Keyboard parity: every V1 shortcut (on-screen keyboard, settlement keys)
  documented and re-implemented in the page module.
- SPA a11y: `core/spa.js` moves focus to the view's `<h1>` after every
  htmx swap and announces the new title via an `aria-live` region;
  browser back/forward behaves like normal pages.

## 10. Packaging & Deployment

- Dev: `python POS_V2\app.py` → waitress on **:5002**, `POS_DEBUG=1`.
- Prod: launcher picks entry via env (`POS_UI_SHELL`); desktop shortcut/VBS
  unchanged. `NexgenPOS_ui.spec` = copy of `NexgenPOS.spec` with
  `datas += [('POS_V2/templates', ...), ('POS_V2/static', ...),
  ('POS_V2/vendor', ...)]` — legacy specs untouched.
- Rollback: set `POS_UI_SHELL=0` and restart (≤ 60 s), or delete the migrated
  template file for a single-page rollback.

## 11. Backend Refactors — Only If Necessary

Allowed (each optional, additive, non-breaking):
1. none planned for Phase 1–7.
Candidates flagged during audit (deferred unless a blocker appears):
- Extract shared Jinja context builders in `main.py` (4,210 LOC) — only if a
  V2 page cannot obtain data it needs; would be new helper functions, never
  altered existing ones.
- Register or remove the dormant `kiosk` blueprint — decision deferred to
  post-cutover cleanup; V2 ships without it, matching current production
  behavior exactly.
