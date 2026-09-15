"""CDP probe: settlement fit on a 1024x768 POS terminal.

Loads /settle_order/<id> in headless Edge at exactly 1024x768, measures
document overflow and key element geometry, and saves a screenshot.

Usage: venv\\Scripts\\python.exe POS_V2\\tools\\_cdp_settle_fit_check.py [order_id] [port]
"""
import json
import os
import subprocess
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TOOLS))
from _cdp_login_check import CDP, EDGE, PROFILE  # noqa: E402

PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 9351
ORDER_ID = sys.argv[1] if len(sys.argv) > 1 else "7021"
# mode "settle" probes /settle_order/<id>; mode "view" probes /order/<id>.
# Env var, not argv: _cdp_login_check (imported below) claims argv for itself.
MODE = os.environ.get("PROBE_MODE", "settle")
REPO = os.path.dirname(os.path.dirname(TOOLS))
BASE = "http://127.0.0.1:" + os.environ.get("POS_CDP_PORT", "5001")
SHOT = os.path.join(
    TOOLS, "..", "..", "logs",
    "od_fit_1024.png" if MODE == "view" else "settle_fit_1024.png")
SHOT = os.path.normpath(SHOT)


def build_session_cookie():
    """Signed Flask session cookie for the first active admin (probe-only)."""
    import sqlite3

    db_path = os.path.join(REPO, "instance", "pos.db")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT id FROM user WHERE role='admin' AND status='active' "
            "ORDER BY id LIMIT 1").fetchone()
    finally:
        con.close()
    from app import build_shell_app
    from flask.sessions import SecureCookieSessionInterface
    app = build_shell_app()
    serializer = SecureCookieSessionInterface().get_signing_serializer(app)
    return serializer.dumps({"_user_id": str(row[0]), "_fresh": True})


def main():
    cookie = build_session_cookie()
    subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
         "--window-size=1024,768", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(2)
    cdp = CDP(PORT)
    cdp.connect()
    cdp.call("Page.enable")
    cdp.call("Runtime.enable")
    cdp.call("Emulation.setDeviceMetricsOverride", {
        "width": 1024, "height": 768, "deviceScaleFactor": 1, "mobile": False,
    })
    cdp.call("Network.enable")
    cdp.call("Network.setCookie", {
        "name": "session", "value": cookie, "url": BASE + "/",
        "httpOnly": True, "sameSite": "Lax", "path": "/",
    })
    cdp.call("Page.navigate", {
        "url": f"{BASE}/order/{ORDER_ID}" if MODE == "view"
        else f"{BASE}/settle_order/{ORDER_ID}"})
    time.sleep(4)

    expr = r"""(() => {
      const d = document.documentElement;
      const q = (s) => document.querySelector(s);
      const rect = (s) => { const el = q(s); if (!el) return null;
        const r = el.getBoundingClientRect();
        return { w: Math.round(r.width), h: Math.round(r.height),
                 x: Math.round(r.x), y: Math.round(r.y) }; };
      return {
        viewport: { w: window.innerWidth, h: window.innerHeight },
        scroll: { w: d.scrollWidth, h: d.scrollHeight },
        horizOverflow: d.scrollWidth > d.clientWidth,
        vertOverflowPx: d.scrollHeight - d.clientHeight,
        grid: rect('.stl-grid'),
        gridCols: q('.stl-grid') ? getComputedStyle(q('.stl-grid')).gridTemplateColumns : null,
        receipt: rect('.stl-col-summary'),
        payment: rect('.stl-col-payment'),
        dock: rect('.stl-floating-controls'),
        paymentGridCols: q('.stl-payment-grid')
          ? getComputedStyle(q('.stl-payment-grid')).gridTemplateColumns : null,
        nav: rect('.stl-nav-picker') || rect('.od-nav-picker'),
        navBoxH: (() => {
          const b = q('.stl-nav-box') || q('.od-nav-box');
          return b ? Math.round(b.getBoundingClientRect().height) : null;
        })(),
        navActiveBg: (() => {
          const a = q('.stl-nav-box.active') || q('.od-nav-box.active');
          return a ? getComputedStyle(a).backgroundColor : null;
        })(),
      };
    })()"""
    result = cdp.call("Runtime.evaluate", {
        "expression": expr, "returnByValue": True})
    if "exceptionDetails" in result:
        print("EXCEPTION:", json.dumps(result["exceptionDetails"], indent=2)[:1200])
        return 1
    data = result["result"].get("value") or {}
    print(json.dumps(data, indent=2))

    shot = cdp.call("Page.captureScreenshot", {"format": "png"})
    if "data" in shot:
        import base64
        with open(SHOT, "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))
        print("screenshot:", SHOT)

    ok = (not data.get("horizOverflow")) and data.get("vertOverflowPx", 999) <= 40
    print("FIT-CHECK:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
