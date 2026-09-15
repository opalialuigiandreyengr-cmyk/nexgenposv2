"""Follow-up: fresh load at 1024x678 — inspect KPI grid + chart wrapper styles."""
import base64
import json
import os
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _cdp_login_check import CDP, EDGE, PROFILE  # noqa: E402

PORT = 9345
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = "http://127.0.0.1:" + os.environ.get("POS_CDP_PORT", "5002")


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
         "--window-size=1024,678", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    cdp = CDP(PORT)
    cdp.connect()
    cdp.call("Page.enable")
    cdp.call("Runtime.enable")
    cdp.call("Network.enable")
    cdp.call("Network.setCookie", {
        "name": "session", "value": cookie, "url": BASE + "/",
        "httpOnly": True, "sameSite": "Lax", "path": "/",
    })
    cdp.call("Page.navigate", {"url": BASE + "/dashboard"})
    time.sleep(4.5)

    expr = r"""
    (() => {
      const kpis = document.querySelector('.dash-kpis');
      const charts = document.querySelector('.dash-charts');
      const wrap = document.querySelector('#salesChartWrap');
      const canvas = document.getElementById('salesChart');
      const mq560 = window.matchMedia('(max-width: 560px)');
      const mq900 = window.matchMedia('(max-width: 900px)');
      const mq1100 = window.matchMedia('(max-width: 1100px)');
      return {
        viewport: [window.innerWidth, window.innerHeight],
        dpr: window.devicePixelRatio,
        media: {
          mq560: mq560.matches,
          mq900: mq900.matches,
          mq1100: mq1100.matches,
        },
        kpiGrid: getComputedStyle(kpis).gridTemplateColumns,
        kpiCount: document.querySelectorAll('.dash-kpi').length,
        chartsGrid: getComputedStyle(charts).gridTemplateColumns,
        chartWrapHeight: getComputedStyle(wrap).height,
        chartWrapDisplay: getComputedStyle(wrap).display,
        canvas: [Math.round(canvas.clientWidth), Math.round(canvas.clientHeight)],
        canvasStyle: [canvas.style.width, canvas.style.height],
        overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth
          ? document.documentElement.scrollWidth - document.documentElement.clientWidth : 0,
      };
    })()
    """
    result = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    print(json.dumps(result["result"].get("value"), indent=2))

    shot = cdp.call("Page.captureScreenshot", {"format": "png"})
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs",
                           "dash_followup_1024.png"), "wb") as f:
        f.write(base64.b64decode(shot["data"]))
    print("screenshot saved")


if __name__ == "__main__":
    main()
