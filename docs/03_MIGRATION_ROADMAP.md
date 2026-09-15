# POS_V2 — Migration Roadmap

Step-by-step migration from the legacy Bootstrap frontend to POS_V2
(njX UI). Every phase has **entry criteria, a checklist, and exit criteria**;
nothing advances until the gate passes. The legacy tree stays read-only
throughout — verify at each gate: `git status` on legacy paths is clean.

---

## 1. Migration Strategy

- **Strangler pattern:** one template at a time. Adding
  `templates/<name>.html` flips that screen to V2; removing it reverts.
- **Behavior-first porting:** never rewrite money math or endpoints — move
  existing JS logic into ES modules line-by-line, then clean up structure
  only after parity tests pass.
- **Two UIs, one app:** during transition both UIs render from the same
  Flask process and DB; users can be pointed at either via the env flag.
- **SPA shell with child views (htmx):** the parent shell loads once per
  login; each migrated screen also becomes a swappable child view via
  htmx `hx-boost` + `HX-Request` fragment mode (Architecture §4), so
  navigation stops reloading the page. Deep links and hard refreshes
  always keep working.
- **Per-phase UAT sign-off** by role (admin / manager / cashier / crew).

## 2. Feature-Parity Matrix (every V1 screen → V2)

Legend: ✅ required parity · ⛔ out of scope · 🔒 same backend endpoint

| V1 template / screen | V2 template | Core behaviors that must match | Phase |
| --- | --- | --- | --- |
| `login.html` | `login.html` | login, lockout messaging, card-swipe admin override, shift-lock notice | 1 |
| `home.html` | `home.html` | role tiles (`/admin_tiles`), launcher ping | 1 |
| `dashboard.html` | `dashboard.html` | KPI cards, Chart.js charts, date filtering | 2 |
| `eod_closed.html` | `eod_closed.html` | closed-day notice, standalone shell | 2 |
| `pos.html` | `pos.html` | category rail, product grid, cart, qty/modifiers, order type, table pick, dine-in/takeout/delivery, keyboard.js, unit toggle, save order 🔒 `/save_pos_order` | 3 |
| `orders.html` | `orders.html` | live board, SSE `/stream_orders`, presence lock acquire/release 🔒, filters | 3 |
| `order_details.html` | `order_details.html` | item list, modify qty, delete item, reprint slip/kitchen, status timeline | 3 |
| `settlement.html` | `settlement.html` | cash/card/GC/gift-check/cheque, senior/PWD/solo-parent/athlete/MOV/regular + **mixed** discounts, pax-based computation, VAT split, manual SI no., card swipe JSON, change calc, receipt print 🔒 | 3 |
| `add-items.html` | `add-items.html` | add items to existing order, split bill, cancel split items 🔒 | 3 |
| `all_orders.html` | `all_orders.html` | history table, filters, `/api/all_orders` pagination | 3 |
| void/cancel/refund modals | `_modals.html` + page js | admin credential verification 🔒 `/verify_admin_credentials`, reference numbers, print variants 🔒 | 3 |
| `table_management.html` | `table_management.html` | drag/rotate tables, zones CRUD, floor boxes, sections, reservation 🔒 | 4 |
| table transfer/join UI | in `orders.html` | transfer/join table orders 🔒 | 4 |
| `kiosk.html` + `kiosk/*` | ⛔ deferred | blueprint not registered in prod today (finding A6) | — |
| `products.html` | `products.html` | list/archive/activate, edit, recipe view, Excel import 🔒, image upload | 5 |
| `add_product.html` / `edit_product.html` | `products.html` modals | create/edit with category, cost/price, pax, GC flag | 5 |
| `categories.html` | `categories.html` | add/rename, usage guards | 5 |
| `users.html` | `users.html` | CRUD, roles, status, card number, mac id, shift lock | 5 |
| `settings.html` | `settings.html` | receipt header/footer, store info, map pin/photo, service/payment types, hours, printer config + discovery/test 🔒, RLC settings, print size | 5 |
| `backup.html` | `backup.html` | create/list/download/delete backups, GDrive flow 🔒 | 5 |
| `data_retention.html` | `data_retention.html` | retention policy, cleanup 🔒 | 5 |
| `activity_logs.html` | `activity_logs.html` | filter, Excel export 🔒, TRXN numbers | 5 |
| `audit_logs.html` | `audit_logs.html` | void/refund/cancel audit trail | 5 |
| `misc.html` | `misc.html` | misc report links hub | 5 |
| `sales-book-report.html` + 6 discount books | same names | date-range books, print + Excel export 🔒 (senior, PWD, MOV, athlete, solo parent, regular) | 6 |
| daily sales report | `daily_sales_report.html` | summary, export 🔒, Z-reading generation 🔒 | 6 |
| item sales report | `item_sales_report.html` | per-item totals, print today 🔒, export 🔒 | 6 |
| Z/X reading views | in reports/EOD | print Z 🔒, print X 🔒, date-range data 🔒 | 6–7 |
| `rlc_compliance.html` | `rlc_compliance.html` | compliance grid, export 🔒 | 6 |
| cancelled/void/refund + cancelled orders reports | same names | tables, print, export 🔒 | 6 |
| BIR sales summary | `bir_sales_summary.html` | summary + Excel export 🔒 | 6 |
| `cashier_accountability.html` | `cashier_accountability.html` | per-cashier accountability, print 🔒 | 6 |
| `end_of_day.html` | `end_of_day.html` | EOD wizard, missing-date check, progress profile (`eodProgressProfileV1`), RLC generate/send, queue/network status, processor status 🔒 | 7 |
| `transfer_history.html` | `transfer_history.html` | history, retry/resend, status check 🔒 | 7 |

**Cross-cutting parity (all phases):** CSRF meta + fetch wrapper behavior,
`posTheme` persistence, cashier navbar (Orders / All Orders / End of Day),
printer-guard warning, role-based nav visibility, SSE reconnect, slow-page
skeleton states, keyboard navigation, SweetAlert → njX modal semantics
(confirm/cancel, danger styling, async loading state).

---

## 3. Phase Checklists

### Phase 0 — Discovery & audit ✅ (complete)
- [x] Route inventory (~150 routes / 6 blueprints)
- [x] Model inventory (20 models)
- [x] Frontend LOC census + finding list A1–A10
- [x] NJX UI research (njxui.dev docs, npm `njx-ui@1.1.4` verified)
- [x] Skills installed: `frontend-design-ui-ux`, `loop-me`
- [x] Plan, architecture, roadmap documents written

### Phase 1 — Foundation & design system (Week 1)
Entry: plan approved.
- [x] Create `POS_V2/` scaffold per Architecture §2
- [x] `npm pack njx-ui@1.1.4` → extract to `vendor/njx/` (pin exact version)
- [x] `npm pack htmx.org@1.9.12` → extract to `vendor/htmx/` (pin exact version;
      the bare `htmx` package name on npm is an unrelated stub)
- [x] `POS_V2/app.py` with ChoiceLoader override + `/assets` blueprint
- [x] `tokens.css`: brand→njX token mapping (both themes) + area axis
      (`data-area="admin|pos"`: Space Grotesk backoffice / JetBrains Mono +
      Space Mono POS typography) + vendored fonts (space-grotesk,
      jetbrains-mono, space-mono woff2) + `core/theme.js` `data-theme` hook
      reusing the `posTheme` localStorage key (checked via `tools/check_override.py`)
- [x] `shell.html` V2 shell (named shell.html, NOT base.html — legacy
      children must keep resolving the legacy base.html): head (font
      preloads, theme boot), role navbar partial, toast + modal hosts,
      flash-data bridge, footer, SSE connect
- [x] `core/api.js`, `core/theme.js`, `core/sse.js`, `core/store.js`,
      `core/modal.js`, `core/toast.js` (ES modules, framework-free,
      syntax-verified)
- [x] `core/spa.js` htmx SPA layer: global config (`historyCacheSize = 0`),
      CSRF `X-CSRFToken` injection via `htmx:configRequest`, view module
      `mount()`/`destroy()` lifecycle via `htmx:afterSwap`/`htmx:beforeSwap`,
      dirty-form guard (`htmx:beforeRequest`), area switch (`data-area`),
      redirect detection (login / eod_closed), V1 fallback full-load,
      focus-to-`<h1>` + title announce after swap
- [x] `shell.html`: `<body hx-boost="true" hx-target="#view-root"
      hx-swap="innerHTML">`; fragment mode emits only the `content` block +
      `data-view-meta` (title/area/page) when `request.headers.get('HX-Request')`
      is set
- [x] `_navbar.html` with role matrix incl. cashier invariant
- [x] `_icons.html` SVG sprite (55 icons)
- [x] `login.html` standalone V2 (extends nothing — the shell serves
      authenticated chrome only): tokens + njX forms, theme boot,
      flash bridge → toasts (lockout / shift-lock / banned messages),
      password toggle, card-swipe login protocol (V1 parity)
- [x] `home.html` shell child: role tiles straight from the real `main.home`
      context (featured row + Reports/Inventory/System groups), live clock,
      network / cashier+kitchen printer / RLC pending status row (same
      endpoints as V1 script.js), page module `js/pages/home.js`
      (`mount`/`destroy` per the spa.js contract)
- [x] Design-review loop (strict): login + home visually rebuilt per the
      locked design language in `.ulpi/design/DESIGN.md` (technical /
      utilitarian; amber operational accent; Signature = the dark mono
      Operational Ticker; elevation + motion tokens in tokens.css LAYER 3;
      anti-slop pass: no purple glow / gradient text / glassmorphism /
      em-dashes; harness markers extended; screenshots verified light +
      dark, 1440 split + stacked)
- [ ] `tools/parity_harness.py`: crawl V1 routes → status codes + template
      context keys baseline JSON
- [ ] `NexgenPOS_ui.spec` (copy + datas) builds successfully
- [x] Legacy-tree check: `git status` clean on legacy paths

Exit: `/login` (standalone) and `/home` (shell child) serve on :5002; htmx
swap between shell children (fragment mode + view meta) verified via the
override harness; SSE + theme persist; M1 demo.

### Phase 2 — Auth & landing (Week 2)
- [x] `login.html` (incl. banned/locked messages, card swipe) — migrated
      early for the M1 demo (Phase 1)
- [x] `home.html` + role tiles — migrated early for the M1 demo (Phase 1)
- [x] `dashboard.html` (Chart.js v3.9.1 vendored at `vendor/chartjs/`,
      KPI cards, Chart.js charts, date filter — spec:
      `.ulpi/design/pos-dashboard.md`)
- [ ] `eod_closed.html`
- [x] Theme toggle in navbar; `posTheme` shared with V1
- [x] SPA navigation verified: home ↔ dashboard swaps without reload via
      `hx-boost`; browser back/forward re-fetches fresh views; login POST
      redirect lands on a full shell load; direct URL entry still renders
      a full page
- [ ] Playwright smoke: login → home → dashboard (both themes, SPA mode)

Exit: all 4 roles log in; nav visibility matches V1 exactly; UAT sign-off.

### Phase 3 — Cashier core (Weeks 3–4) — highest risk
- [ ] Golden fixtures: 20 seeded orders (all discount combos, payment
      types) with expected totals exported from V1 (`tools/golden/`)
- [ ] `pos.html`: category rail, grid, cart, modifiers, order types,
      table selection, keyboard.js port, unit toggle
- [ ] `orders.html`: SSE live board, presence lock UX, filters
- [ ] `order_details.html`: item ops, reprints
- [ ] `settlement.html` + `pages/settlement.js`: full payment/discount
      matrix; golden fixtures diff = 0
- [ ] `add-items.html` + split-bill flows
- [ ] `all_orders.html`
- [ ] Void/cancel/refund/modify modals with admin verification
- [ ] Printer-guard warning parity; print buttons hit identical endpoints

Exit: full shift simulation (open → 10 mixed orders → settle all → reprints
→ one void/refund/cancel) performed identically in V1 and V2 with matching
DB rows; golden diff 0; M2 demo.

### Phase 4 — Tables & floor plan (Week 5)
- [ ] `table_management.html`: drag/rotate/resize canvas (pointer events),
      zones CRUD, floor boxes, sections
- [ ] Table transfer/join flows from orders board
- [ ] Occupied-table indicators (`/get_occupied_tables`)
- [ ] Reservation flow

Exit: floor plan state (positions/rotations/zones) saved identically;
drag performance ≥ V1 (60 fps on target hardware).

### Phase 5 — Admin CRUD (Week 6)
- [ ] products (+ Excel import, archive/activate, images)
- [ ] categories · users (roles, cards, shift lock)
- [ ] settings: receipt/store/printers (discover/test) /RLC
- [ ] backup · data retention · activity logs · audit logs · misc hub
- [ ] All destructive actions behind njX confirm modals (danger variant)

Exit: CRUD matrix UAT; Excel import round-trip identical; printer discovery
works.

### Phase 6 — Reports (Weeks 7–8)
- [ ] Shared `_report_shell.html`: date-range picker (vendored, offline),
      print/export buttons, table macro
- [ ] Sales-book hub + 6 discount books
- [ ] daily sales · item sales · Z/X readings · RLC compliance ·
      cancelled/void/refund · cancelled orders · BIR summary ·
      cashier accountability
- [ ] Byte-diff harness: export XLSX (pandas output) from V1 and V2
      triggers → identical files (UI only triggers same endpoints)
- [ ] Print triggers hit identical endpoints; no format changes

Exit: M3 — every export/print artifact byte-identical; date-range edge
cases (month boundaries, DST-free PH time) verified.

### Phase 7 — EOD & automation (Week 9)
- [ ] `end_of_day.html` wizard: missing dates, staged progress
      (profile key `eodProgressProfileV1` reused), RLC generate/send
- [ ] Queue/processor/network status widgets (same polling intervals)
- [ ] `transfer_history.html` + retry/resend
- [ ] Background processor status surface

Exit: full EOD dry-run on a test day matches V1 (Z-reading values,
RLC file content, e-journal, queue states).

### Phase 8 — Hardening (Week 10)
- [ ] Performance pass: bundle sizes vs budget, preload hints, TTI on
      target hardware (cold + warm)
- [ ] Accessibility audit (focus, ARIA, contrast, keyboard) per screen
- [ ] Responsive/touch pass: 1920×1080 cashier, 1366×768, tablet kiosk
- [ ] Regression soak: 8-hour mixed-usage script against seeded DB
- [ ] Both-theme screenshot diff suite

Exit: all budgets in Project Plan §3 met; zero P1 bugs open.

### Phase 9 — Validation & cutover (Week 11)
- [ ] Parallel UAT: real staff runs one service period on V2 (V1 reachable)
- [ ] UAT sign-off matrix per role (checklist of all matrix rows)
- [ ] Cutover: set `POS_UI_SHELL=1` in launcher env; rebuild installer with
      v2 spec; distribute
- [ ] **Rollback drill:** timed — flip flag, restart, verify V1 < 60 s
- [ ] Monitoring window (1 week): logs, slow-request log, support channel
- [ ] Post-cutover: freeze V1 templates; schedule cleanup backlog
      (kiosk BP decision, legacy CSS removal after 1 month stable)

Exit: M5 — signed UAT matrix, successful rollback drill, week of clean logs.

---

## 4. Testing Strategy

| Layer | Tool | Gate |
| --- | --- | --- |
| Backend regression (unchanged code) | existing `tests/` pytest | every phase |
| Route/context parity | `tools/parity_harness.py` (status codes + Jinja context keys V1 vs V2) | every phase |
| Money math | golden fixtures (seeded orders → totals diff) | Phase 3 + nightly |
| Artifacts (XLSX/print) | byte/normalized-diff of V1 vs V2 outputs | Phase 6 |
| E2E smoke | Playwright (login → sale → settle → Z-read) | Phase 3+ |
| SPA transitions | htmx swap tests: boost swap without reload, dirty-form guard, back/forward fresh fetch, auth-redirect handling, deep link == htmx-rendered DOM | Phase 1+ |
| Visual | screenshot diff, both themes, 3 viewports | Phase 8 |
| Manual | role × screen UAT matrix | every phase exit |

## 5. Validation & Acceptance

A screen is **done** when:
1. Parity harness row green (context keys + status codes).
2. All 🔒 endpoints exercised and DB side-effects identical to V1
   (verified via SQL diff on a copied DB).
3. UAT checkbox signed by at least one user of each affected role.
4. No console errors; no Bootstrap classes in DOM (`document.querySelectorAll('[class*="col-"], .btn-primary')` etc. audit script).
5. SPA check: in-app navigation swaps only `#view-root` via htmx (shell
   untouched, SSE never reconnects), and direct URL load of the same
   screen renders an identical view.

## 6. Deployment & Rollback Plan

1. **Build:** installer from `NexgenPOS_ui.spec` (legacy spec untouched).
2. **Staging:** one store terminal on V2 for a full service day (Phase 9).
3. **Cutover:** update launcher env → `POS_UI_SHELL=1`; no DB migration,
   no data movement — same `pos.db`, same AppData paths.
4. **Rollback triggers:** any money-math discrepancy, print failure, or
   P1 regression → set `POS_UI_SHELL=0`, restart (≤ 60 s); single-page issues
   → delete that V2 template file.
5. **Decommission:** after 30 stable days, archive V1 templates/CSS
   (kept in git history), remove bootstrap/sweetalert/flatpickr assets in a
   later maintenance release.

## 7. Timeline Summary (Gantt)

```
Week  1   2   3   4   5   6   7   8   9   10  11
P1   ████
P2        ████
P3            ████████
P4                    ████
P5                        ████
P6                            ████████
P7                                    ████
P8                                        ████
P9                                            ████
M1   ▲ M2 ───────▲        M3 ────────────▲ M4 ▲ M5
```
