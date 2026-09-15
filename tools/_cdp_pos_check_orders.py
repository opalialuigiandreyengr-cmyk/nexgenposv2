"""CDP probe for the V2 live order board (Phase 3 exit check, orders.html).

Verifies against the LIVE server on :5002:
  1. Full load of /orders: shell meta (page=orders, area=pos), type picker,
     always-dark floor plan canvas, room-section sidebar, live chip state,
     users bridge, zero console errors, no horizontal overflow, no
     bootstrap strings in the board DOM.
  2. Order type switch: aria-pressed sync + board re-render per section.
  3. Server-select modal on an EMPTY table (stubbed occupancy): title,
     search filter, pick -> navigation to /pos with order_type/table_number/
     server_id/server_name params.
  4. Occupied table + presence lock: stubbed /order_presence 409 ->
     'Order In Use' modal; 200 -> navigation to /settle_order/<id>.
  5. REAL lock endpoints: POST /order_presence/<id> then
     /release_order_lock/<id> (no stub, immediately released).
  6. Synthetic pos:sse {type:'update'} + 'update' -> dataset.boardRefresh
     increments (SSE -> debounced realtime refresh contract).
  7. Success banner: /orders?order_success=.. renders + dismiss button.
  8. Table actions stack + transfer mode smoke + ESC cancels modes.
  9. Zero console errors accumulated across the whole session.

Usage: venv\Scripts\python.exe POS_V2\tools\_cdp_pos_check_orders.py [w] [h] [port]
"""
import base64
import json
import os
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _cdp_login_check import CDP, EDGE, PROFILE  # noqa: E402

WIN_W = int(sys.argv[1]) if len(sys.argv) > 1 else 1440
WIN_H = int(sys.argv[2]) if len(sys.argv) > 2 else 900
PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 9342
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = "http://127.0.0.1:" + os.environ.get("POS_CDP_PORT", "5002")
LOGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")

ERROR_CAPTURE = r"""
window.__qaErrors = [];
window.addEventListener('error', (e) => window.__qaErrors.push('error: ' + e.message));
window.addEventListener('unhandledrejection', (e) => window.__qaErrors.push('rejection: ' + (e.reason && e.reason.message)));
const origErr = console.error.bind(console);
console.error = (...args) => { window.__qaErrors.push('console: ' + args.map(String).join(' ')); origErr(...args); };
"""


def build_session_cookie():
    db_path = os.path.join(REPO, "instance", "pos.db")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        table = next(t for t in tables if t.lower() in ("user", "users"))
        row = con.execute(
            f"SELECT id, username FROM {table} "
            "WHERE role='admin' AND status='active' ORDER BY id LIMIT 1").fetchone()
    finally:
        con.close()
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app import build_shell_app
    from flask.sessions import SecureCookieSessionInterface
    app = build_shell_app()
    serializer = SecureCookieSessionInterface().get_signing_serializer(app)
    return serializer.dumps({"_user_id": str(row[0]), "_fresh": True})


def latest_order():
    """Newest order id + a pending/open one if any (for the real lock exercise)."""
    db_path = os.path.join(REPO, "instance", "pos.db")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        pend = con.execute(
            'SELECT id, order_no FROM "order" '
            "WHERE status IN ('pending','open') ORDER BY id DESC LIMIT 1").fetchone()
        latest = con.execute(
            'SELECT id, order_no FROM "order" ORDER BY id DESC LIMIT 1').fetchone()
    except Exception:
        pend = latest = None
    finally:
        con.close()
    return (pend or latest)


def main():
    cookie = build_session_cookie()
    edge = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
         f"--window-size={WIN_W},{WIN_H}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        run_probe(cookie)
    finally:
        edge.terminate()


def run_probe(cookie):
    cdp = CDP(PORT)
    cdp.connect()
    cdp.call("Page.enable")
    cdp.call("Runtime.enable")
    cdp.call("Emulation.setDeviceMetricsOverride", {
        "width": WIN_W, "height": WIN_H, "deviceScaleFactor": 1, "mobile": False,
    })
    cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": ERROR_CAPTURE})
    cdp.call("Network.setCookie", {
        "name": "session", "value": cookie, "url": BASE + "/",
        "httpOnly": True, "sameSite": "Lax", "path": "/",
    })

    def evaluate(expression, label):
        result = cdp.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in result:
            print("EXCEPTION:", label, json.dumps(result["exceptionDetails"], indent=2)[:1500])
            return None
        return result["result"].get("value")

    def click(selector):
        return evaluate(f"document.querySelector({json.dumps(selector)}).click();", "click " + selector)

    def wait(seconds):
        time.sleep(seconds)

    def shot(name):
        out = cdp.call("Page.captureScreenshot", {"format": "png"})
        path = os.path.join(LOGS, name)
        with open(path, "wb") as f:
            f.write(base64.b64decode(out["data"]))
        print("screenshot:", path)

    def goto(url, settle=4.0):
        cdp.call("Page.navigate", {"url": url})
        wait(settle)

    def wait_for(expression, label, timeout=20.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if evaluate(expression, "wait_for " + label):
                return True
            wait(0.5)
        print("WAIT TIMEOUT:", label)
        return False

    ORDER = latest_order()

    # ---- 1. Full load --------------------------------------------------------
    goto(BASE + "/orders", 5.0)
    wait_for("!!document.querySelector('.dinein-pos-table, .orders-empty')",
             "board render", 25)
    probe1 = r"""
    (() => {
      const meta = document.querySelector('template[data-view-meta]');
      const boxes = [...document.querySelectorAll('.order-type-box')];
      const live = document.getElementById('ordersLiveChip');
      const users = (() => { try {
        const raw = document.getElementById('ordersUsersData').textContent;
        const arr = JSON.parse(raw);
        return { count: arr.length, ok: arr.every((u) => u.id && u.username) };
      } catch (e) { return { count: -1, ok: false }; } })();
      const board = document.querySelector('.dinein-table-board');
      const html = document.documentElement.outerHTML.toLowerCase();
      return {
        href: location.href,
        page: meta ? meta.dataset.page : null,
        area: meta ? meta.dataset.area : null,
        title: document.querySelector('h1.orders-title')?.textContent?.trim(),
        typeBoxes: boxes.map((b) => ({ type: b.dataset.type, pressed: b.getAttribute('aria-pressed') })),
        activeType: boxes.find((b) => b.classList.contains('active'))?.dataset.type || null,
        floorPlan: !!document.getElementById('dineInFloorPlanCanvas'),
        roomSidebar: !!document.getElementById('ordersRoomList'),
        roomItems: document.querySelectorAll('.orders-room-item').length,
        tablesRendered: document.querySelectorAll('.dinein-pos-table').length,
        occupied: document.querySelectorAll('.dinein-pos-table.is-occupied').length,
        liveChip: live ? { state: live.dataset.state, text: document.getElementById('ordersLiveText').textContent } : null,
        usersBridge: users,
        manualDrawerVisible: !!document.getElementById('btnManualDrawer'),
        successBanner: !!document.getElementById('ordersSuccessBanner'),
        bootstrapLeak: html.includes('bootstrap'),
        overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth
          ? document.documentElement.scrollWidth - document.documentElement.clientWidth : 0,
        errors: window.__qaErrors.slice(),
      };
    })()
    """
    print("SECTION 1 - FULL LOAD:")
    print(json.dumps(evaluate(probe1, "full-load probe"), indent=2))
    shot("orders_cdp_1440x900.png")

    # ---- 2. Type switch ------------------------------------------------------
    evaluate(r"""
    (() => {
      const target = [...document.querySelectorAll('.order-type-box')].find((b) => b.dataset.type !== 'dinein');
      if (target) { window.__qaSwitchType = target.dataset.type; target.click(); }
      else window.__qaSwitchType = null;
    })()
    """, "click non-dinein type")
    wait(2.8)
    probe2 = r"""
    (() => {
      const t = window.__qaSwitchType;
      if (!t) return { switched: false };
      const box = document.querySelector(`.order-type-box[data-type="${t}"]`);
      const board = document.getElementById('dineInTableBoard');
      const visibleTables = [...document.querySelectorAll('.dinein-pos-table')];
      const sections = [...new Set(visibleTables.map((el) => el.dataset.roomSection))];
      const emptyState = !!document.querySelector('.orders-empty');
      return {
        switched: true, type: t,
        pressed: box.getAttribute('aria-pressed'),
        active: box.classList.contains('active'),
        boardVisible: board.style.display !== 'none',
        visibleSections: sections, emptyState,
      };
    })()
    """
    print("SECTION 2 - TYPE SWITCH:")
    print(json.dumps(evaluate(probe2, "type switch probe"), indent=2))
    click('.order-type-box[data-type="dinein"]')
    wait(2.5)

    # ---- 3. Server-select modal (empty table, stubbed) ------------------------
    evaluate(r"""
    (() => {
      window.__qaFetchMode = 'empty';   // /get_occupied_tables -> no occupied tables
      window.__qaLockMode = 'none';     // /order_presence -> real pass-through
      const origFetch = window.fetch.bind(window);
      window.fetch = (url, opts) => {
        const u = String(url);
        if (u.includes('/get_occupied_tables')) {
          return Promise.resolve(new Response(JSON.stringify({
            occupied_tables: [], table_orders: {} }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }));
        }
        if (u.includes('/order_presence/') && window.__qaLockMode !== 'none') {
          if (window.__qaLockMode === 'conflict') {
            return Promise.resolve(new Response(JSON.stringify({
              success: false, lock_conflict: true,
              message: 'QA stub: order locked by QA-User' }),
              { status: 409, headers: { 'Content-Type': 'application/json' } }));
          }
          return Promise.resolve(new Response(JSON.stringify({ success: true }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }));
        }
        return origFetch(url, opts);
      };
      document.dispatchEvent(new CustomEvent('pos:sse', { detail: { type: 'update' } }));
    })()
    """, "install fetch stub (empty occupancy) + force refresh")
    wait(3.0)
    clicked = evaluate(r"""
    (() => {
      const table = document.querySelector('.dinein-pos-table:not(.is-occupied)');
      if (!table) return null;
      window.__qaEmptyTableLabel = table.querySelector('.dinein-pos-table-label').textContent.trim();
      table.click();
      return window.__qaEmptyTableLabel;
    })()
    """, "click first empty table")
    wait(1.2)
    probe3a = r"""
    (() => {
      const overlay = document.querySelector('.lib-modal-overlay');
      if (!overlay) return { overlayOpen: false };
      const title = overlay.querySelector('.lib-modal-title');
      const list = overlay.querySelector('.orders-pick-list');
      return {
        overlayOpen: true,
        title: title ? title.textContent.trim() : null,
        userButtons: overlay.querySelectorAll('.server-user-pick-btn').length,
        searchPresent: !!overlay.querySelector('.orders-pick-search'),
        overlayId: overlay.id,
      };
    })()
    """
    print("SECTION 3a - SERVER SELECT OVERLAY (table " + str(clicked) + "):")
    print(json.dumps(evaluate(probe3a, "server select probe"), indent=2))
    probe3b = evaluate(r"""
    (() => {
      const input = document.querySelector('.lib-modal-overlay .orders-pick-search');
      if (!input) return { search: false };
      const before = document.querySelectorAll('.server-user-pick-btn').length;
      input.value = 'z';
      input.dispatchEvent(new Event('input'));
      const after = document.querySelectorAll('.server-user-pick-btn').length;
      return { search: true, before, after: after };
    })()
    """, "search filter probe")
    print("SECTION 3b - SEARCH FILTER (expect after <= before):")
    print(json.dumps(probe3b, indent=2))
    evaluate(r"""
    (() => {
      const input = document.querySelector('.lib-modal-overlay .orders-pick-search');
      if (input) { input.value = ''; input.dispatchEvent(new Event('input')); }
      const btn = document.querySelector('.server-user-pick-btn');
      if (btn) btn.click();
    })()
    """, "pick first user (navigates to /pos)")
    wait(4.5)
    probe3c = r"""
    (() => ({
      href: location.href,
      orderType: document.getElementById('orderType')?.value || null,
      tableNumber: document.getElementById('tableNumber')?.value || null,
      selectedServerId: window.selectedServerId || null,
      selectedServerName: window.selectedServerName || null,
    }))()
    """
    print("SECTION 3c - NAVIGATION PARAMS ON /pos:")
    print(json.dumps(evaluate(probe3c, "pos nav probe"), indent=2))

    # ---- 4. Occupied table + presence lock -----------------------------------
    goto(BASE + "/orders", 4.0)
    wait_for("!!document.querySelector('.dinein-pos-table, .orders-empty')", "board render", 25)
    if not ORDER:
        print("SECTION 4 - SKIPPED (no order rows in DB)")
    else:
        evaluate(r"""
        (() => {
          const first = document.querySelector('.dinein-pos-table');
          if (!first) return;
          window.__qaTableLabel = first.querySelector('.dinein-pos-table-label').textContent.trim();
          window.__qaOrderId = """ + str(ORDER[0]) + """;
          window.__qaFetchMode = 'occupied';
          window.__qaLockMode = 'conflict';
          const origFetch = window.fetch.bind(window);
          window.fetch = (url, opts) => {
            const u = String(url);
            if (u.includes('/get_occupied_tables')) {
              const label = window.__qaTableLabel, oid = window.__qaOrderId;
              return Promise.resolve(new Response(JSON.stringify({
                occupied_tables: [label],
                table_orders: { [label]: [{ id: oid, order_no: 'QA-' + oid,
                  customer_name: 'QA Stub', total: 120.5, item_count: 2 }] } }),
                { status: 200, headers: { 'Content-Type': 'application/json' } }));
            }
            if (u.includes('/order_presence/') && window.__qaLockMode !== 'none') {
              if (window.__qaLockMode === 'conflict') {
                return Promise.resolve(new Response(JSON.stringify({
                  success: false, lock_conflict: true,
                  message: 'QA stub: order locked by QA-User' }),
                  { status: 409, headers: { 'Content-Type': 'application/json' } }));
              }
              return Promise.resolve(new Response(JSON.stringify({ success: true }),
                { status: 200, headers: { 'Content-Type': 'application/json' } }));
            }
            return origFetch(url, opts);
          };
          document.dispatchEvent(new CustomEvent('pos:sse', { detail: { type: 'update' } }));
        })()
        """, "stub occupied + lock conflict + force refresh")
        wait(3.2)
        probe4a = evaluate(r"""
        (() => {
          const table = [...document.querySelectorAll('.dinein-pos-table')]
            .find((t) => t.querySelector('.dinein-pos-table-label')?.textContent.trim() === window.__qaTableLabel);
          if (!table) return { tableFound: false };
          table.click();
          return { tableFound: true, occupied: table.classList.contains('is-occupied') };
        })()
        """, "click stubbed-occupied table (conflict)")
        wait(1.2)
        probe4b = r"""
        (() => {
          const overlay = document.querySelector('.lib-modal-overlay');
          if (!overlay) return { lockModal: false };
          return {
            lockModal: true,
            title: overlay.querySelector('.lib-modal-title')?.textContent?.trim() || null,
            text: overlay.querySelector('.lib-modal-text')?.textContent?.trim() || null,
          };
        })()
        """
        print("SECTION 4a - LOCK CONFLICT MODAL (table " +
              str(evaluate("window.__qaTableLabel || null", "table label")) + "):")
        print(json.dumps(probe4a, indent=2))
        print(json.dumps(evaluate(probe4b, "lock modal probe"), indent=2))
        click('.lib-modal-actions .btn')
        wait(0.5)
        evaluate("window.__qaLockMode = 'ok';", "arm lock ok")
        probe4c = evaluate(r"""
        (() => {
          const table = [...document.querySelectorAll('.dinein-pos-table')]
            .find((t) => t.querySelector('.dinein-pos-table-label')?.textContent.trim() === window.__qaTableLabel);
          if (!table) return { tableFound: false };
          table.click();
          return { tableFound: true };
        })()
        """, "click stubbed-occupied table (ok)")
        wait(4.5)
        probe4d = r"""
        (() => ({
          href: location.href,
          settlePath: location.pathname.startsWith('/settle_order/'),
        }))()
        """
        print("SECTION 4b - LOCK OK -> NAVIGATION:")
        print(json.dumps(probe4c, indent=2))
        print(json.dumps(evaluate(probe4d, "settle nav probe"), indent=2))
        shot("orders_cdp_lock.png")

    # ---- 5. REAL lock endpoints ----------------------------------------------
    goto(BASE + "/orders", 4.0)
    wait_for("!!document.querySelector('.dinein-pos-table, .orders-empty')", "board render", 25)
    if not ORDER:
        print("SECTION 5 - SKIPPED (no order rows in DB)")
    else:
        probe5 = r"""
        (async () => {
          const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
          const take = await fetch('/order_presence/""" + str(ORDER[0]) + """', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'X-CSRFToken': csrf, 'Content-Type': 'application/json' }, body: '{}' });
          const tjson = await take.json().catch(() => ({}));
          let released = null;
          if (take.status === 200) {
            const rel = await fetch('/release_order_lock/""" + str(ORDER[0]) + """', {
              method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': csrf } });
            released = rel.status;
          }
          return { order: '""" + str(ORDER[1]) + """', presence: take.status, body: tjson, released };
        })()
        """
        print("SECTION 5 - REAL LOCK EXERCISE (order " + str(ORDER[1]) + "):")
        print(json.dumps(evaluate(probe5, "real lock probe"), indent=2))

    # ---- 6. Synthetic SSE -> boardRefresh counter ------------------------------
    probe6 = r"""
    (async () => {
      const read = () => Number(document.documentElement.dataset.boardRefresh || 0);
      const before = read();
      document.dispatchEvent(new CustomEvent('pos:sse', { detail: { type: 'update' } }));
      await new Promise((r) => setTimeout(r, 2600));
      const afterObj = read();
      document.dispatchEvent(new CustomEvent('pos:sse', { detail: 'update' }));
      await new Promise((r) => setTimeout(r, 2600));
      const afterStr = read();
      return { before, afterObj, afterStr };
    })()
    """
    print("SECTION 6 - SYNTHETIC pos:sse -> boardRefresh (expect increments):")
    print(json.dumps(evaluate(probe6, "sse probe"), indent=2))

    # ---- 7. Success banner ----------------------------------------------------
    goto(BASE + "/orders?order_success=true&order_no=SO-QA-1&printed=true&order_type=dinein", 3.5)
    probe7a = r"""
    (() => {
      const banner = document.getElementById('ordersSuccessBanner');
      if (!banner) return { banner: false };
      return {
        banner: true,
        text: banner.textContent.replace(/\s+/g, ' ').trim(),
        orderNo: document.getElementById('ordersSuccessOrderNo').textContent,
        dismiss: !!document.getElementById('ordersSuccessDismiss'),
      };
    })()
    """
    print("SECTION 7a - SUCCESS BANNER:")
    print(json.dumps(evaluate(probe7a, "banner probe"), indent=2))
    click('#ordersSuccessDismiss')
    wait(0.8)
    probe7b = r"""
    (() => ({ bannerGone: !document.getElementById('ordersSuccessBanner') }))()
    """
    print("SECTION 7b - BANNER DISMISSED:")
    print(json.dumps(evaluate(probe7b, "banner dismiss probe"), indent=2))
    goto(BASE + "/orders", 3.5)
    probe7c = r"""
    (() => ({ bannerAbsent: !document.getElementById('ordersSuccessBanner') }))()
    """
    print("SECTION 7c - PLAIN /orders HAS NO BANNER:")
    print(json.dumps(evaluate(probe7c, "banner absent probe"), indent=2))

    # ---- 8. Table actions + transfer mode + ESC -------------------------------
    wait_for("!!document.querySelector('.dinein-pos-table, .orders-empty')", "board render", 25)
    click('#tableActionsToggle')
    wait(0.6)
    probe8a = r"""
    (() => {
      const stack = document.getElementById('tableActionsStack');
      return {
        stackOpen: stack.classList.contains('is-open'),
        expanded: document.getElementById('tableActionsToggle').getAttribute('aria-expanded'),
        buttons: ['btnTransferTables', 'btnReserveTables', 'btnJoinTables', 'btnManualDrawer']
          .map((id) => ({ id, present: !!document.getElementById(id) })),
      };
    })()
    """
    print("SECTION 8a - ACTIONS STACK:")
    print(json.dumps(evaluate(probe8a, "actions stack probe"), indent=2))
    click('#btnTransferTables')
    wait(0.7)
    probe8b = r"""
    (() => {
      const board = document.getElementById('dineInTableBoard');
      const occupied = document.querySelectorAll('.dinein-pos-table.is-occupied');
      return {
        transferActive: document.getElementById('btnTransferTables').classList.contains('active'),
        boardMode: board.classList.contains('transfer-mode-active'),
        occupiedSources: [...occupied].filter((t) => t.classList.contains('transfer-source')).length,
        targets: document.querySelectorAll('.dinein-pos-table.transfer-target').length,
      };
    })()
    """
    print("SECTION 8b - TRANSFER MODE ON:")
    print(json.dumps(evaluate(probe8b, "transfer on probe"), indent=2))
    evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));", "ESC")
    wait(0.5)
    probe8c = r"""
    (() => {
      const board = document.getElementById('dineInTableBoard');
      return {
        transferCleared: !document.getElementById('btnTransferTables').classList.contains('active'),
        boardCleared: !board.classList.contains('transfer-mode-active'),
        sourceCleared: document.querySelectorAll('.dinein-pos-table.transfer-source').length === 0,
      };
    })()
    """
    print("SECTION 8c - ESC CANCELS TRANSFER MODE:")
    print(json.dumps(evaluate(probe8c, "transfer off probe"), indent=2))
    click('#btnReserveTables')
    wait(0.6)
    probe8d = r"""
    (() => ({
      reserveActive: document.getElementById('btnReserveTables').classList.contains('active'),
      boardMode: document.getElementById('dineInTableBoard').classList.contains('reserve-mode-active'),
    }))()
    """
    print("SECTION 8d - RESERVE MODE ON:")
    print(json.dumps(evaluate(probe8d, "reserve probe"), indent=2))
    evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));", "ESC")
    wait(0.4)

    # ---- 9. Errors summary ----------------------------------------------------
    probe9 = r"""
    (() => ({ errors: window.__qaErrors.slice() }))()
    """
    print("SECTION 9 - CONSOLE ERRORS:")
    print(json.dumps(evaluate(probe9, "errors probe"), indent=2))
    shot("orders_cdp_end.png")


if __name__ == "__main__":
    main()
