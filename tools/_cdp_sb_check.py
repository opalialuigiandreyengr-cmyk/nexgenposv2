"""CDP scrollbar-style probe: confirm the njX custom scrollbar skin is gone.

Reads computed scrollbar properties (webkit pseudo + scrollbar-width/color)
from the live /home page and reports whether the native fallback applies.
Usage: _cdp_sb_check.py <width> <height> <port>
"""
import json
import os
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _cdp_login_check import CDP, EDGE, PROFILE  # noqa: E402

WIN_W = int(sys.argv[1]) if len(sys.argv) > 1 else 1024
WIN_H = int(sys.argv[2]) if len(sys.argv) > 2 else 678
PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 9338
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
URL = "http://127.0.0.1:" + os.environ.get("POS_CDP_PORT", "5002") + "/home"


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


def main():
    cookie = build_session_cookie()
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
    time.sleep(4.0)

    probe_expr = r"""
    (() => {
      const nav = document.querySelector('.side-nav');
      const wsb = getComputedStyle(document.body, '::-webkit-scrollbar');
      const wtr = getComputedStyle(document.body, '::-webkit-scrollbar-track');
      const wth = getComputedStyle(document.body, '::-webkit-scrollbar-thumb');
      return {
        isScrolling: document.body.classList.contains('is-scrolling'),
        webkitScrollbar: {
          width: wsb.width, height: wsb.height,
          trackBg: wtr.backgroundColor, thumbBg: wth.backgroundColor,
        },
        scrollbarWidth: getComputedStyle(document.body).scrollbarWidth,
        scrollbarColor: getComputedStyle(document.body).scrollbarColor,
        starRuleApplied: getComputedStyle(nav).scrollbarWidth,
      };
    })()
    """

    def probe(label):
        result = cdp.call("Runtime.evaluate", {
            "expression": probe_expr, "returnByValue": True})
        if "exceptionDetails" in result:
            print("EXCEPTION:", json.dumps(result["exceptionDetails"], indent=2)[:2000])
        print(label, json.dumps(result["result"].get("value"), indent=2))

    probe("IDLE:")

    # Trigger a real scroll on the sidebar nav (2px overflow at 1279x642),
    # which fires the capture-phase scroll listener -> body.is-scrolling.
    cdp.call("Runtime.evaluate", {
        "expression": "document.querySelector('.side-nav').scrollTop = 999;"})
    time.sleep(0.3)
    probe("MID-SCROLL:")

    shot = cdp.call("Page.captureScreenshot", {"format": "png"})
    import base64
    png = base64.b64decode(shot["data"])
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs",
                       f"sb_check_{WIN_W}x{WIN_H}.png")
    with open(out, "wb") as f:
        f.write(png)
    print("screenshot (mid-scroll):", out)

    time.sleep(1.0)  # past the 800ms reveal window
    probe("IDLE-AGAIN:")


if __name__ == "__main__":
    main()
