"""CDP probe for the V2 order detail screen (Phase 3, order_details.html).

Verifies against the LIVE server on :5002. Every mutating endpoint is stubbed
at window.fetch so the UAT database is never written; only the presence lock
(/order_presence + /release_order_lock) runs for real and is released at once.

  1. Full load of /order/<id>: shell meta (page=order_details, area=pos),
     status chip, header actions for pending, items table rows + variant
     tags, summary sidebar, timeline card, 3 JSON bridges parse, settle link,
     split button, no Bootstrap leak, no horizontal overflow, no console errors.
  2. Presence lock: REAL take + release; then a stubbed 409 on a fresh load ->
     "Order In Use" modal + page freeze.
  3. Item management: open the modify overlay, verify seeded rows + totals math,
     change qty / remove / add a product, then capture the SUBMIT payload and
     assert the V1 keyed-object shape (newQuantity / deleted / added /
     product_id / reason) plus subtotal+vat+total = 2-decimal numbers.
  4. Split bill: drive the admin-auth modal (credentials tab), stub
     /modify_order, verify both zones render, drag one unit left -> right with
     synthetic pointer events (ghost + drop target), verify zone counts and
     per-side totals move, then capture the /split_bill payload shape.
  5. Cancel order flow: capture the auth -> reason -> confirm sequence and the
     /confirm_cancel_order reason payload.
  6. Printer guard: with /check_printer_status stubbed NOT connected the reprint
     must abort with the guard modal and send no print request; with the guard
     stubbed connected, /reprint_receipt must fire exactly once.
  7. Timeline fed by the real /api/order/<id>/audit feed + final error sweep.

Usage: venv\\Scripts\\python.exe POS_V2\\tools\\_cdp_pos_check_order_details.py [w] [h] [cdp_port]
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
PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 9343
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

# Install the fetch interceptor. Mode 'on' stubs every mutating endpoint and
# records each request; 'lock:conflict' makes /order_presence answer 409;
# 'printer:false' makes the guard fail. Presence/release passthrough is real.
STUB_INSTALL = r"""
(() => {
  if (window.__qaStubInstalled) { window.__qaRequests = []; window.__qaStub = STUB_CONFIG; return; }
  window.__qaStubInstalled = true;
  window.__qaRequests = [];
  window.__qaStub = STUB_CONFIG;
  const origFetch = window.fetch.bind(window);
  window.__qaOrigFetch = origFetch;
  const json = (obj, status) => new Response(JSON.stringify(obj), {
    status: status || 200, headers: { 'Content-Type': 'application/json' } });
  window.fetch = (url, opts) => {
    const u = String(url);
    const body = (opts && opts.body) || null;
    window.__qaRequests.push({ url: u, method: (opts && opts.method) || 'GET', body });
    const s = window.__qaStub;
    if (s.mode !== 'on') return origFetch(url, opts);
    if (u.includes('/check_printer_status')) {
      return Promise.resolve(json({ connected: !!s.printer, dev_mode: false,
        printer_required: true, message: 'QA stub: printer unavailable' }));
    }
    if (u.includes('/order_presence/')) {
      if (s.lock === 'conflict') {
        return Promise.resolve(json({ success: false, lock_conflict: true,
          message: 'QA stub: Order is currently being modified by QA-User' }, 409));
      }
      return origFetch(url, opts);
    }
    if (u.includes('/submit_modify_changes/')) {
      return Promise.resolve(json({ success: true, message: 'QA stub: changes saved' }));
    }
    if (u.includes('/modify_order/')) {
      return Promise.resolve(json({ success: true, message: 'QA stub: authenticated' }));
    }
    if (u.includes('/split_bill/')) {
      return Promise.resolve(json({ success: true, original_order_no: 'QA-ORIGINAL',
        new_order_no: 'QA-NEW', new_order_id: 999999 }));
    }
    if (u.includes('/cancel_split_items/')) {
      return Promise.resolve(json({ success: true, cancelled_count: 1, new_order_total: 100 }));
    }
    if (u.includes('/cancel_order_auth/')) {
      return Promise.resolve(json({ success: true, user: 'qa_admin', role: 'admin' }));
    }
    if (u.includes('/confirm_cancel_order/')) {
      return Promise.resolve(json({ success: true, message: 'QA stub: order cancelled' }));
    }
    if (u.includes('/reprint_receipt/') || u.includes('/reprint_kitchen_slip/') || u.includes('/bill_out/')) {
      return Promise.resolve(json({ success: true, message: 'QA stub: printed' }));
    }
    return origFetch(url, opts);
  };
})()
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
    from app import build_shell_app
    from flask.sessions import SecureCookieSessionInterface
    app = build_shell_app()
    serializer = SecureCookieSessionInterface().get_signing_serializer(app)
    return serializer.dumps({"_user_id": str(row[0]), "_fresh": True})


def db_facts():
    """Pick the orders each probe section needs (read-only)."""
    db_path = os.path.join(REPO, "instance", "pos.db")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    facts = {}
    try:
        facts["pending"] = con.execute(
            'SELECT o.id, o.order_no FROM "order" o '
            "WHERE o.status IN ('pending','open') ORDER BY o.id DESC LIMIT 1").fetchone()
        facts["pending_multi"] = con.execute(
            'SELECT o.id, o.order_no FROM "order" o JOIN order_item oi ON oi.order_id = o.id '
            "WHERE o.status IN ('pending','open') GROUP BY o.id "
            "HAVING COUNT(oi.id) >= 2 ORDER BY o.id DESC LIMIT 1").fetchone()
        facts["completed"] = con.execute(
            'SELECT id, order_no FROM "order" WHERE status=\'completed\' '
            "ORDER BY id DESC LIMIT 1").fetchone()
        facts["audited"] = con.execute(
            "SELECT order_id, COUNT(*) FROM order_audit_logs "
            "GROUP BY order_id ORDER BY MAX(id) DESC LIMIT 1").fetchone()
    except Exception as exc:  # pragma: no cover - probe diagnostics only
        print("DB FACTS ERROR:", exc)
    finally:
        con.close()
    return facts


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

    def wait(seconds):
        time.sleep(seconds)

    def shot(name):
        out = cdp.call("Page.captureScreenshot", {"format": "png"})
        path = os.path.join(LOGS, name)
        with open(path, "wb") as f:
            f.write(base64.b64decode(out["data"]))
        print("screenshot:", path)

    def goto(url, settle=4.0):
        # Page.navigate to the IDENTICAL url does not always give a fresh
        # document, which would carry stale JS state (e.g. the lock freeze)
        # into the next section. Force a reload in that case.
        if evaluate("location.href", "current href") == url:
            cdp.call("Page.reload", {"ignoreCache": True})
        else:
            cdp.call("Page.navigate", {"url": url})
        wait(settle)

    def snap_errors(label):
        errs = evaluate("(() => window.__qaErrors.slice())()", "errors " + label) or []
        if errs:
            print(f"  [errors after {label}]: {json.dumps(errs)}")
        return errs

    def install_stub(cfg):
        return evaluate(STUB_INSTALL.replace("STUB_CONFIG", json.dumps(cfg)),
                        "install fetch stub")

    def wait_for(expression, label, timeout=20.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if evaluate(expression, "wait_for " + label):
                return True
            wait(0.5)
        print("WAIT TIMEOUT:", label)
        return False

    def detail_url(order):
        return f"{BASE}/order/{order[0]}"

    def last_request(fragment):
        return evaluate(
            "(() => { const r = (window.__qaRequests || []).filter((x) => x.url.includes('"
            + fragment + "')).pop(); return r || null; })()", "last request " + fragment)

    facts = db_facts()
    PEND = facts.get("pending")
    MULTI = facts.get("pending_multi") or PEND
    DONE = facts.get("completed")
    AUDITED = facts.get("audited")

    # ---- 1. Full load --------------------------------------------------------
    if not PEND:
        print("SECTION 1 - SKIPPED (no orders in DB)")
    else:
        goto(detail_url(PEND), 5.0)
        wait_for("!!document.getElementById('odItemsTable')", "detail render", 25)
        probe1 = r"""
        (() => {
          const meta = document.querySelector('template[data-view-meta]');
          const rows = [...document.querySelectorAll('#odItemsTable tbody .od-item-row')];
          const bridge = (id) => { try { return JSON.parse(document.getElementById(id).textContent); }
                                   catch (e) { return null; } };
          const order = bridge('odOrderBridge');
          return {
            href: location.href,
            page: meta ? meta.dataset.page : null,
            area: meta ? meta.dataset.area : null,
            title: document.querySelector('.od-order-no')?.textContent?.trim(),
            statusChip: document.getElementById('odStatusChip')?.className?.trim(),
            headerActions: ['modifyOrderBtn', 'cancelOrderHeaderBtn', 'settleOrderLink']
              .map((id) => ({ id, present: !!document.getElementById(id) })),
            floating: ['reprintKitchenSlipBtn', 'splitBillBtn', 'billOutBtn']
              .map((id) => ({ id, present: !!document.getElementById(id) })),
            itemRows: rows.length,
            firstRow: rows.length ? {
              name: rows[0].querySelector('.od-item-name')?.textContent?.trim(),
              qty: rows[0].querySelector('.od-qty-num')?.textContent?.trim(),
              unit: rows[0].querySelector('.od-item-unit')?.textContent?.trim(),
              total: rows[0].querySelector('.od-col-total')?.textContent?.trim(),
              variants: [...rows[0].querySelectorAll('.od-variant-chip')].map((c) => c.textContent.trim()),
            } : null,
            summary: [...document.querySelectorAll('.od-summary-row, .od-summary-total')]
              .map((r) => r.textContent.replace(/\s+/g, ' ').trim()).slice(0, 6),
            timelineList: !!document.getElementById('odTimelineList'),
            timelineState: document.getElementById('odTimelineState')?.textContent?.trim() || null,
            timelineItems: document.querySelectorAll('#odTimelineList .od-timeline-item').length,
            bridges: {
              order: !!(order && order.id),
              items: (() => { const i = bridge('odItemsBridge'); return Array.isArray(i) ? i.length : null; })(),
              products: (() => { const p = bridge('odProductsBridge'); return Array.isArray(p) ? p.length : null; })(),
            },
            bootstrapLeak: document.documentElement.outerHTML.toLowerCase().includes('bootstrap'),
            overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth
              ? document.documentElement.scrollWidth - document.documentElement.clientWidth : 0,
            errors: window.__qaErrors.slice(),
          };
        })()
        """
        print("SECTION 1 - FULL LOAD (order " + str(PEND[1]) + "):")
        print(json.dumps(evaluate(probe1, "full-load probe"), indent=2))
        shot("order_details_cdp_1_load.png")

    # ---- 2. Presence lock: real take/release, then stubbed 409 ---------------
    if not PEND:
        print("SECTION 2 - SKIPPED (no orders in DB)")
    else:
        install_stub({"mode": "off", "lock": "pass", "printer": True})
        probe2a = r"""
        (async () => {
          const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
          const take = await window.__qaOrigFetch('/order_presence/""" + str(PEND[0]) + """', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'X-CSRFToken': csrf, 'Content-Type': 'application/json' }, body: '{}' });
          const tjson = await take.json().catch(() => ({}));
          const rel = await window.__qaOrigFetch('/release_order_lock/""" + str(PEND[0]) + """', {
            method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': csrf } });
          return { order: '""" + str(PEND[1]) + """', take: take.status, body: tjson, release: rel.status };
        })()
        """
        print("SECTION 2a - REAL PRESENCE TAKE + RELEASE:")
        print(json.dumps(evaluate(probe2a, "real lock probe"), indent=2))
        snap_errors("2a")

        # Arm the 409 on the live page: the next 5s presence poll then conflicts.
        goto(detail_url(PEND), 4.0)
        install_stub({"mode": "on", "lock": "conflict", "printer": True})
        wait(7.5)
        probe2b = r"""
        (() => {
          const overlay = [...document.querySelectorAll('.lib-modal-overlay')].pop();
          return {
            pageLocked: !!document.querySelector('.od-page-locked'),
            overlayTitle: overlay ? overlay.querySelector('.lib-modal-title')?.textContent?.trim() : null,
            overlayText: overlay ? overlay.querySelector('.lib-modal-text')?.textContent?.trim() : null,
            presenceCalls: (window.__qaRequests || [])
              .filter((r) => r.url.includes('/order_presence/')).length,
          };
        })()
        """
        print("SECTION 2b - STUBBED 409 -> LOCK NOTICE:")
        print(json.dumps(evaluate(probe2b, "lock modal probe"), indent=2))
        shot("order_details_cdp_2_lock.png")

    # ---- 3. Item management payload ------------------------------------------
    if not MULTI:
        print("SECTION 3 - SKIPPED (no pending order)")
    else:
        goto(detail_url(MULTI), 4.0)
        install_stub({"mode": "on", "lock": "pass", "printer": True})
        wait_for("!!document.getElementById('modifyOrderBtn')", "modify btn", 20)
        evaluate("document.getElementById('modifyOrderBtn').click();", "open modify overlay")
        wait(1.0)
        probe3a = r"""
        (() => {
          const overlay = [...document.querySelectorAll('.lib-modal-overlay')].pop();
          if (!overlay) return { overlayOpen: false };
          const rows = [...overlay.querySelectorAll('#odMgmtTableBody .od-mgmt-row')];
          const num = (id) => Number((document.getElementById(id)?.textContent || '')
            .replace(/[^0-9.\-]/g, ''));
          return {
            overlayOpen: true,
            title: overlay.querySelector('.lib-modal-title')?.textContent?.trim(),
            rows: rows.length,
            rowStates: rows.map((r) => ({
              name: r.querySelector('.od-mgmt-name')?.textContent?.trim(),
              deleted: r.classList.contains('is-deleted'),
              added: r.classList.contains('is-added'),
              qty: r.querySelector('.od-mgmt-qty-value')?.textContent?.trim(),
            })),
            totals: { subtotal: document.getElementById('odMgmtSubtotal')?.textContent?.trim(),
                      vat: document.getElementById('odMgmtVat')?.textContent?.trim(),
                      total: document.getElementById('odMgmtTotal')?.textContent?.trim() },
            math: (() => { const t = num('odMgmtTotal'), v = num('odMgmtVat'), s = num('odMgmtSubtotal');
              return { close: Math.abs((s + v) - t) < 0.02, expectedVat: +(t * 0.12 / 1.12).toFixed(2), vat: v }; })(),
          };
        })()
        """
        print("SECTION 3a - MODIFY OVERLAY (seeded rows + totals math):")
        print(json.dumps(evaluate(probe3a, "mgmt overlay probe"), indent=2))
        shot("order_details_cdp_3_mgmt.png")

        probe3b = r"""
        (async () => {
          const overlay = [...document.querySelectorAll('.lib-modal-overlay')].pop();
          if (!overlay) return { overlayMissing: true };
          const rows = [...overlay.querySelectorAll('#odMgmtTableBody .od-mgmt-row')];
          if (rows.length === 0) return { overlayMissing: false, noRows: true };
          // qty +1 on the first row, delete the last one when there is more
          // than a single line, then add a catalog product.
          rows[0].querySelector('[data-od-mgmt="inc"]').click();
          await new Promise((r) => setTimeout(r, 120));
          let removedId = null;
          if (rows.length > 1) {
            const tail = rows[rows.length - 1];
            removedId = tail.dataset.itemId;
            tail.querySelector('[data-od-mgmt="del"]').click();
            await new Promise((r) => setTimeout(r, 120));
          }
          const search = document.getElementById('odMgmtSearch');
          search.value = '';
          search.dispatchEvent(new Event('input'));
          await new Promise((r) => setTimeout(r, 200));
          const hit = document.querySelector('#odMgmtSearchResults .od-search-item');
          let added = null;
          if (hit) {
            hit.click();
            document.getElementById('odMgmtQty').value = '2';
            document.getElementById('odMgmtAddBtn').click();
            await new Promise((r) => setTimeout(r, 200));
            added = search.value;
          }
          const after = [...document.querySelectorAll('#odMgmtTableBody .od-mgmt-row')];
          return {
            overlayMissing: false, removedId, addedProduct: added,
            rowStates: after.map((r) => ({
              id: r.dataset.itemId,
              deleted: r.classList.contains('is-deleted'),
              added: r.classList.contains('is-added'),
              qty: r.querySelector('.od-mgmt-qty-value')?.textContent?.trim(),
            })),
          };
        })()
        """
        print("SECTION 3b - QTY / REMOVE / ADD EDITS:")
        print(json.dumps(evaluate(probe3b, "mgmt edits probe"), indent=2))
        snap_errors("3b")

        evaluate(r"""
        (() => {
          const overlay = [...document.querySelectorAll('.lib-modal-overlay')].pop();
          overlay.querySelector('[data-od-save-changes]').click();
        })()
        """, "click Save Changes (opens confirm)")
        wait(1.0)
        probe3c = r"""
        (() => {
          const confirmOverlay = [...document.querySelectorAll('.lib-modal-overlay')].pop();
          if (!confirmOverlay) return { confirmShown: false };
          return {
            confirmShown: true,
            title: confirmOverlay.querySelector('.lib-modal-title')?.textContent?.trim(),
            buttons: [...confirmOverlay.querySelectorAll('.lib-modal-actions .btn')]
              .map((b) => b.textContent.trim()),
          };
        })()
        """
        print("SECTION 3c - SAVE CONFIRM MODAL:")
        print(json.dumps(evaluate(probe3c, "save confirm probe"), indent=2))
        evaluate(r"""
        (() => {
          const o = [...document.querySelectorAll('.lib-modal-overlay')].pop();
          const ok = [...o.querySelectorAll('.lib-modal-actions .btn')].find((b) => /save/i.test(b.textContent));
          if (ok) ok.click();
        })()
        """, "confirm save")
        wait(0.4)
        captured = last_request("/submit_modify_changes/")
        if not captured:
            print("SECTION 3d - NO /submit_modify_changes REQUEST CAPTURED")
        else:
            payload = json.loads(captured["body"]) if captured["body"] else {}
            changes = payload.get("changes") or {}
            shape = {k: {kk: vv for kk, vv in v.items()} for k, v in changes.items()}
            print("SECTION 3d - SUBMIT PAYLOAD (" + captured["method"] + " " + captured["url"] + "):")
            print(json.dumps({
                "keys": sorted(changes.keys()),
                "isKeyedObject": isinstance(changes, dict),
                "changes": shape,
                "totals": {k: payload.get(k) for k in ("subtotal", "vat", "total")},
                "totalsAreNumbers": all(isinstance(payload.get(k), (int, float))
                                        for k in ("subtotal", "vat", "total")),
            }, indent=2))
            print(json.dumps(last_request("/submit_modify_changes/"), indent=2)[:900])
        snap_errors("3d")

    # ---- 4. Split bill drag + payload ----------------------------------------
    # Needs a pending order with more than one billable line: the template only
    # renders splitBillBtn then. Skip honestly when the UAT data has none.
    SPLIT_ORDER = facts.get("pending_multi")
    if not SPLIT_ORDER:
        print("SECTION 4 - SKIPPED: no pending order with 2+ billable lines in the DB, so "
              "splitBillBtn is not rendered (template gating verified by the harness). Create "
              "a 2+ item dine-in order from the register to exercise the drag/split live.")
    else:
        goto(detail_url(SPLIT_ORDER), 4.0)
        install_stub({"mode": "on", "lock": "pass", "printer": True})
        wait_for("!!document.getElementById('splitBillBtn')", "split btn", 20)
        evaluate("document.getElementById('splitBillBtn').click();", "open auth modal")
        wait(1.0)
        probe4a = r"""
        (() => {
          const o = [...document.querySelectorAll('.lib-modal-overlay')].pop();
          if (!o) return { authModal: false };
          return {
            authModal: true,
            title: o.querySelector('.lib-modal-title')?.textContent?.trim(),
            tabs: [...o.querySelectorAll('.od-auth-tab')].map((t) => t.textContent.trim()),
            swipePanelVisible: !!o.querySelector('[data-od-auth-panel="swipe"]')?.offsetParent,
          };
        })()
        """
        print("SECTION 4a - ADMIN AUTH MODAL:")
        print(json.dumps(evaluate(probe4a, "auth modal probe"), indent=2))
        evaluate(r"""
        (() => {
          const o = [...document.querySelectorAll('.lib-modal-overlay')].pop();
          o.querySelector('[data-od-auth-mode="credentials"]').click();
          o.querySelector('.od-auth-form input[name="username"]').value = 'qa_admin';
          o.querySelector('.od-auth-form input[name="password"]').value = 'qa_password';
        })()
        """, "switch to credentials + fill")
        wait(0.4)
        evaluate(r"""
        (() => {
          const o = [...document.querySelectorAll('.lib-modal-overlay')].pop();
          o.querySelector('[data-od-auth-confirm]').click();
        })()
        """, "authenticate")
        wait(1.4)
        probe4b = r"""
        (() => {
          const left = document.getElementById('odSplitLeftZone');
          const right = document.getElementById('odSplitRightZone');
          if (!left || !right) return { splitOverlay: false };
          const num = (id) => Number((document.getElementById(id)?.textContent || '')
            .replace(/[^0-9.\-]/g, ''));
          return {
            splitOverlay: true,
            title: [...document.querySelectorAll('.lib-modal-overlay')].pop()
              .querySelector('.lib-modal-title')?.textContent?.trim(),
            leftCards: left.querySelectorAll('.od-split-card').length,
            rightCards: right.querySelectorAll('.od-split-card').length,
            placeholderVisible: getComputedStyle(document.getElementById('odSplitRightPlaceholder') || document.body).display,
            confirmDisabled: document.getElementById('odSplitConfirmBtn')?.disabled,
            totals: { leftTotal: num('odSplitLeftTotal'), rightTotal: num('odSplitRightTotal') },
          };
        })()
        """
        print("SECTION 4b - SPLIT ZONES AFTER AUTH:")
        print(json.dumps(evaluate(probe4b, "split zones probe"), indent=2))
        shot("order_details_cdp_4_split_before.png")

        probe4c = r"""
        (async () => {
          const left = document.getElementById('odSplitLeftZone');
          const right = document.getElementById('odSplitRightZone');
          const card = left.querySelector('.od-split-card');
          if (!card) return { draggable: false };
          const name = card.querySelector('.od-split-name')?.textContent?.trim();
          const qtyBefore = Number(card.querySelector('.od-split-qty')?.textContent?.replace(/\D/g, '') || 0);
          const cr = card.getBoundingClientRect();
          const zr = right.getBoundingClientRect();
          const pt = (x, y) => ({ bubbles: true, cancelable: true, pointerId: 1, isPrimary: true,
                                  button: 0, buttons: 1, clientX: x, clientY: y });
          card.dispatchEvent(new PointerEvent('pointerdown', pt(cr.left + 12, cr.top + 12)));
          await new Promise((r) => setTimeout(r, 60));
          const ghost = document.querySelectorAll('.od-split-ghost').length;
          const dragging = card.classList.contains('is-dragging');
          document.dispatchEvent(new PointerEvent('pointermove',
            pt(zr.left + zr.width / 2, zr.top + 18)));
          await new Promise((r) => setTimeout(r, 60));
          const highlighted = right.classList.contains('is-drop-target');
          document.dispatchEvent(new PointerEvent('pointerup',
            pt(zr.left + zr.width / 2, zr.top + 18)));
          await new Promise((r) => setTimeout(r, 250));
          const num = (id) => Number((document.getElementById(id)?.textContent || '')
            .replace(/[^0-9.\-]/g, ''));
          return {
            draggable: true, item: name, qtyBefore,
            ghostCreated: ghost, sourceDragging: dragging, dropHighlighted: highlighted,
            ghostRemovedAfterDrop: document.querySelectorAll('.od-split-ghost').length === 0,
            leftCards: left.querySelectorAll('.od-split-card').length,
            rightCards: right.querySelectorAll('.od-split-card').length,
            rightQty: right.querySelector('.od-split-card')?.querySelector('.od-split-qty')?.textContent?.trim(),
            totals: { leftTotal: num('odSplitLeftTotal'), rightTotal: num('odSplitRightTotal') },
            confirmEnabled: !document.getElementById('odSplitConfirmBtn')?.disabled,
          };
        })()
        """
        print("SECTION 4c - POINTER DRAG LEFT -> RIGHT (qtyToMove = 1):")
        print(json.dumps(evaluate(probe4c, "drag probe"), indent=2))
        shot("order_details_cdp_4_split_after.png")

        evaluate(r"""
        (() => { document.getElementById('odSplitConfirmBtn').click(); })()
        """, "confirm split")
        wait(1.2)
        splitReq = last_request("/split_bill/")
        if not splitReq:
            print("SECTION 4d - NO /split_bill REQUEST CAPTURED")
        else:
            payload = json.loads(splitReq["body"]) if splitReq["body"] else {}
            print("SECTION 4d - SPLIT PAYLOAD:")
            print(json.dumps({"url": splitReq["url"], "split_items": payload.get("split_items")}, indent=2))
        probe4e = r"""
        (() => {
          const o = [...document.querySelectorAll('.lib-modal-overlay')].pop();
          if (!o) return { successModal: false };
          return {
            successModal: true,
            title: o.querySelector('.lib-modal-title')?.textContent?.trim(),
            text: o.querySelector('.lib-modal-text')?.textContent?.trim(),
            buttons: [...o.querySelectorAll('.lib-modal-actions .btn')].map((b) => b.textContent.trim()),
          };
        })()
        """
        print("SECTION 4e - BILL SPLIT SUCCESS MODAL:")
        print(json.dumps(evaluate(probe4e, "split success probe"), indent=2))
        evaluate(r"""
        (() => {
          const o = [...document.querySelectorAll('.lib-modal-overlay')].pop();
          const stay = [...o.querySelectorAll('.lib-modal-actions .btn')].find((b) => /stay/i.test(b.textContent));
          if (stay) stay.click();
        })()
        """, "stay here (avoid navigating to the stub order id)")
        wait(3.0)

    # ---- 5. Cancel order flow ------------------------------------------------
    if not PEND:
        print("SECTION 5 - SKIPPED (no pending order)")
    else:
        # Navigate through /orders first to guarantee a completely fresh
        # document, avoiding any race with section 3's post-submit auto-reload.
        cdp.call("Page.navigate", {"url": BASE + "/orders"})
        wait(3.0)
        cdp.call("Page.navigate", {"url": detail_url(PEND)})
        wait(4.0)
        install_stub({"mode": "on", "lock": "pass", "printer": True})
        wait_for("!!document.getElementById('cancelOrderHeaderBtn')", "cancel btn", 20)

        # Dispatch a real MouseEvent to trigger the cancel flow
        evaluate(r"""
        (() => {
          const btn = document.getElementById('cancelOrderHeaderBtn');
          if (!btn) return;
          btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
        })()
        """, "click cancel button")
        wait(1.5)

        # Fill credentials and authenticate in the auth modal
        evaluate(r"""
        (() => {
          const o = [...document.querySelectorAll('.lib-modal-overlay')].pop();
          if (!o) return { noOverlay: true };
          const credTab = o.querySelector('[data-od-auth-mode="credentials"]');
          if (credTab) credTab.click();
          const user = o.querySelector('.od-auth-form input[name="username"]');
          const pass = o.querySelector('.od-auth-form input[name="password"]');
          if (user) user.value = 'qa_admin';
          if (pass) pass.value = 'qa_password';
          const confirm = o.querySelector('[data-od-auth-confirm]');
          if (confirm) confirm.click();
          return { authenticated: true };
        })()
        """, "authenticate cancel")
        wait(1.3)
        probe5a = r"""
        (() => {
          const o = [...document.querySelectorAll('.lib-modal-overlay')].pop();
          if (!o) return { reasonModal: false };
          return {
            reasonModal: true,
            title: o.querySelector('.lib-modal-title')?.textContent?.trim(),
            reasons: [...o.querySelectorAll('#od-reason-select option')].map((x) => x.value),
            selected: o.querySelector('#od-reason-select')?.value,
          };
        })()
        """
        print("SECTION 5a - REASON MODAL (after /cancel_order_auth):")
        print(json.dumps(evaluate(probe5a, "reason modal probe"), indent=2))
        evaluate(r"""
        (() => {
          const o = [...document.querySelectorAll('.lib-modal-overlay')].pop();
          const sel = o.querySelector('#od-reason-select');
          sel.value = 'Wrong input';
          o.querySelector('[data-od-reason-confirm]').click();
        })()
        """, "confirm reason")
        # The success path reloads to /orders ~900ms after the stubbed reply, so
        # read the captured sequence immediately or the document is gone.
        wait(0.6)
        probe5b = r"""
        (() => {
          const reqs = window.__qaRequests || [];
          const find = (frag) => reqs.filter((r) => r.url.includes(frag)).pop() || null;
          const confirm = find('/confirm_cancel_order/');
          return {
            sequence: reqs.map((r) => r.method + ' ' + r.url.replace(location.origin, '')),
            auth: !!find('/cancel_order_auth/'),
            printerGuard: !!find('/check_printer_status'),
            confirmBody: confirm ? JSON.parse(confirm.body || '{}') : null,
            href: location.href,
          };
        })()
        """
        print("SECTION 5b - REQUEST SEQUENCE + CANCEL PAYLOAD:")
        print(json.dumps(evaluate(probe5b, "cancel sequence probe"), indent=2))

    # ---- 6. Printer guard on reprint -----------------------------------------
    if not DONE:
        print("SECTION 6 - SKIPPED (no completed order)")
    else:
        goto(detail_url(DONE), 4.0)
        wait_for("!!document.getElementById('reprintInvoiceBtn')", "reprint btn", 20)
        install_stub({"mode": "on", "lock": "pass", "printer": False})
        evaluate("document.getElementById('reprintInvoiceBtn').click();", "reprint (printer down)")
        wait(1.6)
        probe6a = r"""
        (() => {
          const o = [...document.querySelectorAll('.lib-modal-overlay')].pop();
          return {
            guardModal: !!o,
            title: o ? o.querySelector('.lib-modal-title')?.textContent?.trim() : null,
            text: o ? o.querySelector('.lib-modal-text')?.textContent?.trim() : null,
            reprintRequests: (window.__qaRequests || []).filter((r) => r.url.includes('/reprint_receipt/')).length,
            guardChecks: (window.__qaRequests || []).filter((r) => r.url.includes('/check_printer_status')).length,
          };
        })()
        """
        print("SECTION 6a - PRINTER DOWN -> GUARD MODAL, NO PRINT:")
        print(json.dumps(evaluate(probe6a, "guard blocked probe"), indent=2))
        shot("order_details_cdp_6_guard.png")
        evaluate(r"""
        (() => {
          const o = [...document.querySelectorAll('.lib-modal-overlay')].pop();
          const btn = o.querySelector('.lib-modal-actions .btn');
          if (btn) btn.click();
          window.__qaStub.printer = true;
        })()
        """, "dismiss guard + arm printer ok")
        wait(0.6)
        evaluate("document.getElementById('reprintInvoiceBtn').click();", "reprint (printer up)")
        wait(1.6)
        probe6b = r"""
        (() => ({
          reprintRequests: (window.__qaRequests || []).filter((r) => r.url.includes('/reprint_receipt/')).length,
          toast: document.querySelector('.toast, .njx-toast, [data-qa-toast]')?.textContent?.trim() || null,
          buttonEnabled: !document.getElementById('reprintInvoiceBtn').disabled,
          buttonLabel: document.getElementById('reprintInvoiceBtn')?.textContent?.trim(),
        }))()
        """
        print("SECTION 6b - PRINTER READY -> REPRINT FIRED:")
        print(json.dumps(evaluate(probe6b, "guard ok probe"), indent=2))

    # ---- 7. Timeline from the real audit feed + error sweep ------------------
    if AUDITED and AUDITED[0]:
        goto(f"{BASE}/order/{AUDITED[0]}", 4.5)
        wait_for("document.querySelectorAll('#odTimelineList .od-timeline-item').length > 0 || (document.getElementById('odTimelineState') && document.getElementById('odTimelineState').offsetParent === null)",
                 "timeline paint", 20)
    probe7 = r"""
    (() => {
      const items = [...document.querySelectorAll('#odTimelineList .od-timeline-item')];
      return {
        href: location.href,
        timelineItems: items.length,
        sample: items.slice(0, 4).map((i) => ({
          classes: i.className,
          type: i.querySelector('.od-tl-type')?.textContent?.trim(),
          time: i.querySelector('.od-tl-time')?.textContent?.trim(),
          ref: i.querySelector('.od-tl-ref')?.textContent?.trim(),
          desc: i.querySelector('.od-tl-desc')?.textContent?.replace(/\s+/g, ' ').trim(),
          user: i.querySelector('.od-tl-user')?.textContent?.trim(),
        })),
        emptyState: document.getElementById('odTimelineEmpty')?.offsetParent !== null,
        stateText: document.getElementById('odTimelineState')?.textContent?.trim() || null,
        auditFetched: performance.getEntriesByType('resource')
          .map((e) => e.name).filter((n) => n.includes('/audit')).length,
      };
    })()
    """
    print("SECTION 7 - TIMELINE (real /api/order/<id>/audit):")
    print(json.dumps(evaluate(probe7, "timeline probe"), indent=2))
    shot("order_details_cdp_7_timeline.png")

    probe8 = r"""(() => ({ errors: window.__qaErrors.slice() }))()"""
    print("SECTION 8 - CONSOLE ERRORS:")
    print(json.dumps(evaluate(probe8, "errors probe"), indent=2))


if __name__ == "__main__":
    main()
