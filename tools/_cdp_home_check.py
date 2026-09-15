"""CDP home-board check: layout probe at a real viewport, authenticated.

Launches its own headless Edge, injects a signed Flask session cookie for
the first active admin user (read-only), navigates to /home and reports the
shell geometry (sidebar state/width, topbar, outlet), the stats board values
and the recent-orders rows, plus horizontal-overflow detection.

Usage: _cdp_home_check.py <width> <height> <port>
"""
import base64
import json
import os
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _cdp_login_check import CDP, EDGE, PROFILE  # noqa: E402  (reuses the ws client)

WIN_W = int(sys.argv[1]) if len(sys.argv) > 1 else 1440
WIN_H = int(sys.argv[2]) if len(sys.argv) > 2 else 900
PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 9334
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
URL = "http://127.0.0.1:" + os.environ.get("POS_CDP_PORT", "5002") + "/home"


def build_session_cookie():
    """Signed Flask session for the first active admin user (read-only)."""
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
    if not row:
        raise RuntimeError("no active admin user in the database")

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app import build_shell_app
    from flask.sessions import SecureCookieSessionInterface

    app = build_shell_app()
    serializer = SecureCookieSessionInterface().get_signing_serializer(app)
    cookie = serializer.dumps({"_user_id": str(row[0]), "_fresh": True})
    return cookie, row[1]


def main():
    cookie, username = build_session_cookie()
    print(f"auth: {username} (admin), cookie {len(cookie)} bytes")

    subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
         f"--window-size={WIN_W},{WIN_H}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    cdp = CDP(PORT)
    cdp.connect()
    cdp.call("Page.enable")
    cdp.call("Runtime.enable")
    cdp.call("Network.enable")
    cdp.call("Emulation.setDeviceMetricsOverride", {
        "width": WIN_W, "height": WIN_H, "deviceScaleFactor": 1, "mobile": False,
    })
    cdp.call("Network.setCookie", {
        "name": "session", "value": cookie, "url": "http://127.0.0.1:5002/",
        "httpOnly": True, "sameSite": "Lax", "path": "/",
    })
    cdp.call("Page.navigate", {"url": URL})
    time.sleep(4.5)  # boot + overview fetch + first paint settled

    expr = r"""
    (() => {
      const r = (sel) => {
        const el = document.querySelector(sel);
        if (!el) return null;
        const b = el.getBoundingClientRect();
        return [Math.round(b.width), Math.round(b.left), Math.round(b.top), Math.round(b.height)];
      };
      const side = document.querySelector('.shell-side');
      const text = (sel) => {
        const el = document.querySelector(sel);
        return el ? el.textContent.trim() : null;
      };
      return {
        viewport: [window.innerWidth, window.innerHeight],
        sideNavState: document.body.dataset.sideNav || '(unset)',
        sidebar: r('.shell-side'),
        topbar: r('.shell-topbar'),
        topbarTitle: text('#topbar-title'),
        outlet: r('#view-root'),
        h1: text('#view-root h1'),
        stats: ['#homeStatSales', '#homeStatOrders', '#homeStatItems', '#homeStatProducts']
          .map((sel) => text(sel)),
        recentRows: document.querySelectorAll('#homeRecentOrders .home-recent-row').length,
        recentFirst: (() => {
          const row = document.querySelector('#homeRecentOrders .home-recent-row');
          return row ? row.textContent.replace(/\s+/g, ' ').trim() : null;
        })(),
        horizontalOverflow: document.documentElement.scrollWidth - window.innerWidth,
        activeSideLink: (() => {
          const a = document.querySelector('.side-link.is-active');
          return a ? a.getAttribute('href') : null;
        })(),
        sideLinks: document.querySelectorAll('.side-link').length,
        captions: [...document.querySelectorAll('.side-caption')].map((c) => c.textContent.trim()),
        sideLinkLabelDisplay: getComputedStyle(document.querySelector('.side-link-label')).display,
        sideCaptionDisplay: getComputedStyle(document.querySelector('.side-caption')).display,
      };
    })()
    """
    result = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    if "exceptionDetails" in result:
        print("EXCEPTION:", json.dumps(result["exceptionDetails"], indent=2)[:2000])
    print(json.dumps(result["result"].get("value"), indent=2))

    shot = cdp.call("Page.captureScreenshot", {"format": "png"})
    png = base64.b64decode(shot["data"])
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs",
                       f"home_cdp_{WIN_W}x{WIN_H}.png")
    with open(out, "wb") as f:
        f.write(png)
    print("screenshot:", out)


if __name__ == "__main__":
    main()
