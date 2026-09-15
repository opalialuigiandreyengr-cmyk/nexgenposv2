# POS_V2 — NexGen POS Frontend Overhaul

New, optimized frontend for the existing NexGen POS system, built on
[njX UI](https://njxui.dev/) (`npm i njx-ui`, v1.1.4) with **zero Bootstrap**
and a **single-page app (SPA) shell**: one persistent parent shell with
child views swapped in dynamically by **htmx** — no full page reloads after
login.
The existing backend (Flask, SQLite, printing, RLC/EOD automation) is reused
**as-is** — nothing in the legacy codebase is modified.

## Documents

| Doc | Contents |
| --- | --- |
| [docs/01_PROJECT_PLAN.md](docs/01_PROJECT_PLAN.md) | Audit findings, goals, scope, phases, timeline, checklists, risks |
| [docs/02_ARCHITECTURE.md](docs/02_ARCHITECTURE.md) | System architecture, directory structure, module boundaries, state management, NJX UI mapping |
| [docs/03_MIGRATION_ROADMAP.md](docs/03_MIGRATION_ROADMAP.md) | Phased migration roadmap, feature-parity matrix, testing, validation, cutover & rollback |

## Ground Rules

1. **Read-only legacy** — no edits to `website/`, `templates/`, `static/`, `app.py`.
2. **Same Flask app** — POS_V2 mounts on the existing app via an external
   Jinja `ChoiceLoader` override (see Architecture doc), so every route,
   CSRF rule, session, and DB behavior is preserved exactly.
3. **Instant rollback** — an environment flag (`POS_UI_SHELL`) selects V1 or V2
   template resolution at startup.
4. **Offline-first** — every asset is vendored locally; no CDN at runtime.
5. **SPA shell via htmx** — parent layout (navbar, theme, toasts, SSE) loads
   once; child views are server-rendered HTML fragments swapped in-place by
   **htmx** (`hx-boost`, vendored v1.9.12) plus a thin `core/spa.js` config
   layer. Deep links and hard refreshes still work as full pages.
