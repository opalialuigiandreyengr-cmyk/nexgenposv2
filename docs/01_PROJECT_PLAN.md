# POS_V2 — Project Plan

**Project:** NexGen POS frontend overhaul (Bootstrap → njX UI) with full backend preservation
**Date:** August 27, 2026 · **Status:** Plan approved for Phase 1 kickoff
**Duration:** 11 weeks (≈ 60 engineer-days) · **Mode:** parallel build, zero-risk cutover

---

## 1. Executive Summary

The existing NexGen POS is a feature-complete, BIR-aware restaurant POS:
Flask + SQLite backend with ~150 routes, ESC/POS printing, EOD/RLC
compliance automation, and a desktop (pywebview/PyInstaller) shell. Its
weakness is the frontend: **~21,000 lines of CSS and ~11,500 lines of JS**
layered over Bootstrap, with thousands of lines of inline `<script>`/`<style>`
inside 42 Jinja templates, two competing theme systems, and a CDN dependency
that violates the offline-first requirement.

POS_V2 replaces the entire presentation layer with
**njX UI** (`njx-ui@1.1.4` — CSS-first, zero dependencies, ~308 KB,
9 themes via a single `data-theme` attribute). The backend is consumed
**untouched** through a template-resolution override, which means:

- every route, CSRF rule, session behavior, and DB write is preserved by
  construction (same handler renders the new markup);
- the UI becomes an **SPA**: one persistent parent shell (navbar, theme,
  toasts, live order feed) with server-rendered **child views** swapped in
  by **htmx** (vendored, ~14 KB) — no full page reloads after login;
- migration is incremental, page-by-page, with per-page rollback;
- feature parity is verifiable screen-by-screen against the live V1 UI.

---

## 2. Current System Audit (Phase 0 results)

### 2.1 Backend inventory (preserved 100%)

| Area | Evidence | Notes |
| --- | --- | --- |
| App factory | `website/__init__.py` (791 LOC) | CSRF middleware, SQLite pragmas (WAL, `busy_timeout=5000`, `synchronous=NORMAL`, 8 MB cache), startup schema auto-repair (`auto_add_missing_columns` + 12 targeted patches), performance-index guarantee, card-number hash migration, startup lock reset, default-admin seed, slow-request logging, mtime cache-busting |
| Blueprints | auth (4 routes), main (85), orders (32), sales_reports (26), eod (29) ≈ **~150 routes** | kiosk blueprint (5 routes) is defined in `website/kiosk.py` but **never registered** — dead/legacy code (finding A6) |
| Models | `website/models.py` — 20 models | User, Product, Recipe, Category, GiftCertificate, Order, OrderItem, Settlement, OrderAuditLog, Z/XReading, RLCFile, RestaurantTable, TableZone, FloorBox, ReceiptSettings, RLCSettings, ActivityLog; sequential number generators (order/invoice/VOID/REFUND/CANCEL/TRXN/GC) |
| Printing | `website/printer.py` (5,256 LOC) | ESC/POS network + Windows printers, cash drawer, receipt/slip/kitchen formats |
| Reports | `website/salesbook.py` (2,893), `sales_reports.py` (4,733) | pandas/reportlab Excel exports + printed reports; PH VAT 12%; senior/PWD/solo-parent/athlete/MOV/regular + mixed discounts |
| Compliance | `eod_routes.py` (3,008), `rlc_apps/`, `ejournal.py` | Z-reading, BIR reset counter, RLC file generation + SFTP transfer queue, e-journal |
| Background | `unified_background_processor.py` | queue polling every 20 s (EOD, RLC, backups) |
| Security | CSRF before-request, bcrypt, card-hash, `protect_from_tampering`, permissions matrix, session hardening, failed-login lockout | All must continue to apply unchanged |
| Hosting | `app.py` → waitress (20 threads) or Flask debug; pywebview GUI; single-instance mutex; PyInstaller specs | POS_V2 must remain packageable the same way |
| Realtime | SSE `/stream_orders`; polling for printer/queue/EOD status | Keep SSE; no WebSockets introduced |
| Tests | `tests/test_transactions.py`, `tests/test_security_regressions.py` | Baseline regression gate |

### 2.2 Frontend inventory (to be replaced)

- **42 templates ≈ 30,000 LOC**, several with massive inline blocks:
  `end_of_day.html` 3,307 · `orders.html` 1,961 · `add-items.html` 1,785 ·
  `pos.html` 1,742 (~1,200 inline JS) · `order_details.html` 1,704 ·
  `settings.html` 1,347 · `base.html` 1,052.
- **JS ≈ 11,500 LOC**: `settlement.js` 4,216 · `orders.js` 2,063 ·
  `base.js` 1,603 (theme init, CSRF fetch wrapper, printer guard, EOD
  progress) · `script.js` 1,280 · `dashboard.js` 497 · `keyboard.js` 492.
- **CSS ≈ 21,000 LOC**: `style.css` 5,210 · `modules.css` 2,695 ·
  `order_details.css` 1,777 · `settlement.css` 1,601 · `settings-v2.css`
  1,316 · plus 15+ per-page `-v2` sheets.
- **Libraries:** Bootstrap 5 (local), FontAwesome 5 (local), SweetAlert2,
  Chart.js, **Flatpickr from CDN** (offline violation).
- **Theming:** dual system — `data-theme` (`posTheme` localStorage key) for
  the app and `data-admin-theme` for admin pages, via `--dk-*` CSS variables.

### 2.3 Audit findings → V2 responses

| # | Finding | V2 response |
| --- | --- | --- |
| A1 | Thousands of lines of inline JS/CSS in templates | Strict rule: no inline `<script>`/`<style>` > 10 lines; everything in ES modules / per-page CSS |
| A2 | Bootstrap + custom CSS fight (heavy `!important`, recurring brace-imbalance regressions) | njX UI tokens are the only design surface; page CSS may only consume `var(--…)` tokens |
| A3 | Two competing theme systems | Single `data-theme` on `<html>` (njX-native), reusing the existing `posTheme` localStorage key; admin pages inherit the same theme |
| A4 | Flatpickr loaded from CDN | Vendor locally or replace with native `input[type=date]` + custom range picker |
| A5 | SweetAlert2 dependency | Replace with njX `lib-modal` + `njxFireToast` wrappers (same confirm/alert semantics) |
| A6 | `kiosk.py` blueprint never registered (dead code) | Exclude from V2 scope; document as legacy; parity matrix marks it N/A |
| A7 | No minification/build step | Optional lightweight bundler (esbuild) for V2 assets only; legacy assets untouched |
| A8 | Mixed legacy vs “-v2” admin page architectures | One uniform V2 shell & layout for all 40+ screens |
| A9 | Accessibility inconsistent | WCAG 2.1 AA checklist per screen (focus order, ARIA, contrast, keyboard) |
| A10 | Font-Awesome recovery hack in `base.html` (font-load race) | njX UI uses inline SVG icons; no webfont race |

---

## 3. Goals & Success Criteria

| Goal | Metric |
| --- | --- |
| Feature parity | 100% of the parity matrix (Roadmap doc §2) green in UAT |
| Speed | POS screen interactive < 800 ms cold on target hardware; **SPA view swaps ≤ 150 ms after fragment fetch, zero full reloads after login**; total CSS+JS shipped per page ≤ 250 KB gz; zero CDN requests |
| Backend untouched | `git diff` of legacy tree is empty at every milestone; existing pytest suite passes |
| Maintainability | No template > 400 LOC; no JS module > 500 LOC; tokens-only styling |
| Quality | WCAG 2.1 AA on core cashier flows; 0 known brace/selector regressions |
| Rollback | V1 reachable within 60 s via `POS_UI_SHELL=0` restart |

## 4. Scope

**In scope:** all rendered screens (cashier, admin, reports, EOD, table
management, login/home/dashboard), shared components, theming, icons,
client-side state, SSE client, printing *triggers* (UI only), packaging
integration (PyInstaller data entries for POS_V2 assets).

**Out of scope:** backend business logic changes, DB schema changes, API
contract changes, printer firmware behavior, RLC/EOD algorithms, Android
kiosk app, marketing deliverables.

## 5. Phase Overview & Timeline

| Phase | Name | Weeks | Deliverable | Exit gate |
| --- | --- | --- | --- | --- |
| 0 | Discovery & audit | done | This document set | Findings signed off |
| 1 | Foundation & design system | 1 | POS_V2 scaffold, vendored njX + htmx, tokens, V2 SPA shell + navbar, htmx SPA layer (`hx-boost` + `core/spa.js`, fragment mode), core JS (api/csrf/theme/sse/toast), parity harness | V2 shell renders `/login`, `/home`; htmx swap between them without reload; harness runs |
| 2 | Auth & landing | 2 | login, home, dashboard (Chart.js), eod_closed | Role-based nav verified for admin/manager/cashier/crew |
| 3 | Cashier core | 3–4 | POS screen, keyboard, cart, orders, order details, add-items, settlement, void/cancel/refund/modify modals, reprints | Full sale lifecycle incl. mixed discounts + all payment types; SSE live updates |
| 4 | Tables & kiosk | 5 | table management editor (zones/floor boxes/drag/rotate), transfer/join, presence lock UI, kiosk pages | Floor-plan parity with V1 |
| 5 | Admin CRUD | 6 | products (+ Excel import), categories, users, settings (receipt/store/printers/RLC), backup, data retention, logs | CRUD + validation parity |
| 6 | Reports | 7–8 | sales-book suite (6 discount books), daily/item sales, Z/X readings, RLC compliance, cancelled/void/refund, BIR summary, cashier accountability + all exports/prints | Exported XLSX & printed output byte-comparable with V1 |
| 7 | EOD & automation | 9 | EOD wizard + progress profile, RLC transfer UI, queue/network status, transfer history, retry/resend | EOD dry-run identical to V1 |
| 8 | Hardening | 10 | Perf budget, a11y audit, responsive/touch, regression soak | Budgets in §3 met |
| 9 | Validation & cutover | 11 | Parallel-run UAT, launcher switch, rollback drill, decommission plan | Signed UAT matrix + rollback drill passed |

## 6. Dependencies

- **njx-ui@1.1.4** on npm (verified published) → vendor into `POS_V2/vendor/njx/`.
- **htmx@1.9.12** on npm → vendor into `POS_V2/vendor/htmx/` (offline, pinned).
- Existing `website/` package importable from `POS_V2/app.py` (same venv).
- Ports: V2 dev server on **5002**; V1 stays on 5000/5001.
- Chart.js vendored copy (already in `static/chartjs/`) — reused, not re-downloaded.
- Icons: inline SVG set (Lucide-style) replacing FontAwesome in V2 screens.

## 7. Top Risks & Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| `settlement.js` (4,216 LOC) hides subtle discount/VAT rules | Wrong money math | Port as behavior-first: golden-file tests of totals from seeded orders before/after; never rewrite formulas, move them |
| Receipt/print drift | BIR compliance | Printing stays 100% backend; V2 only triggers same endpoints; print-output diff tests |
| njX UI immaturity (v1.x) | Missing component | Vendor the CSS; a thin `components.css` fills gaps (POS keypad, table canvas) |
| Inline JS extraction breaks flows | Regression | One-screen-at-a-time with V1 kept live; parity harness per screen |
| SQLite concurrency during parallel V1+V2 run | Locks | Same DB file with existing WAL + busy_timeout already handles multi-terminal; V2 is same app instance in cutover, separate instance only in dev |
| PyInstaller packaging misses V2 assets | Broken installer | Phase 1 adds datas entries to a **copy** of the spec (new spec file, legacy spec untouched) |
| Theme regression (known historical pain) | Visual bugs | Single-theme architecture + screenshot diff checks on both themes |
| SPA shell: stale state / memory leaks between views, auth redirects lost inside fragment fetches | Confusing bugs, broken sessions | htmx history cache **disabled** (every navigation fetches fresh — LAN latency makes this free, and it kills stale-money-data bugs by construction); `core/spa.js` provides the dirty-form guard, view `mount`/`destroy` lifecycle via htmx events, redirect detection, and V1 fallback; deep links always render full pages |

## 8. Milestones

- **M1 (end W1):** V2 shell live behind a flag.
- **M2 (end W4):** A cashier can work a full shift entirely in V2.
- **M3 (end W8):** All reports & exports byte-parity.
- **M4 (end W10):** Hardened, budget-met build.
- **M5 (end W11):** Cutover complete, rollback drill passed, V1 frozen.

## 9. Team & Cadence

- Daily: parity harness run + commit checklist.
- Weekly: phase gate review against exit criteria; UAT sign-off log updated.
- Tools: pytest (backend regressions), Playwright scripts for smoke flows
  (login → sale → settle → Z-reading), manual UAT matrix per role.

Detailed per-phase checklists: see [03_MIGRATION_ROADMAP.md](03_MIGRATION_ROADMAP.md).
