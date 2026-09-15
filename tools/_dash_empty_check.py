"""CDP probe: empty-state overlays vs. real chart data on the V2 dashboard.

Toggles the sales range to Monthly and products to Month (both carry REAL
data in the current DB) and asserts at the DOM level that:
  - #salesChartEmpty / #productChartEmpty are hidden (hidden attr true,
    computed display none, visibility hidden)
  - both canvases are displayed with real dimensions

Usage: venv\\Scripts\\python.exe POS_V2\\tools\\_dash_empty_check.py [port]
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

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9347
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
         "--window-size=1440,900", "about:blank"],
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

    def evaluate(expression, label):
        result = cdp.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True})
        if "exceptionDetails" in result:
            print("EXCEPTION:", label,
                  json.dumps(result["exceptionDetails"], indent=2)[:1200])
            return None
        return result["result"].get("value")

    def click(selector):
        evaluate(
            "document.querySelector(" + json.dumps(selector) + ").click();",
            "click " + selector)

    # Baseline: today (empty in the current DB) - overlays must be VISIBLE.
    probe = r"""
    (() => {
      const probe = (id) => {
        const el = document.getElementById(id);
        const cs = getComputedStyle(el);
        return {
          hidden: el.hidden,
          display: cs.display,
          visibility: cs.visibility,
          text: el.textContent.trim().slice(0, 60),
        };
      };
      const sales = document.getElementById('salesChart');
      const prod = document.getElementById('productChart');
      return {
        salesEmpty: probe('salesChartEmpty'),
        productEmpty: probe('productChartEmpty'),
        salesCanvas: {
          display: getComputedStyle(sales).display,
          size: [Math.round(sales.clientWidth), Math.round(sales.clientHeight)],
        },
        productCanvas: {
          display: getComputedStyle(prod).display,
          size: [Math.round(prod.clientWidth), Math.round(prod.clientHeight)],
        },
        errors: (window.__qaErrors || []).slice(),
      };
    })()
    """
    print("BASELINE (today - expected: overlays visible, canvases hidden):")
    print(json.dumps(evaluate(probe, "baseline"), indent=2))

    # Toggle to ranges with REAL data: Monthly sales + Month products.
    click("#salesMonthly")
    time.sleep(1.2)
    click("#productMonth")
    time.sleep(1.2)
    print("AFTER TOGGLE (Monthly / Month - expected: overlays hidden, "
          "canvases displayed):")
    print(json.dumps(evaluate(probe, "toggled"), indent=2))

    value = evaluate(r"""
    (() => ({
      salesRange: document.getElementById('dashSalesRange').textContent,
      productRange: document.getElementById('dashProductRange').textContent,
      navCount: performance.getEntriesByType('navigation').length,
    }))()
    """, "range captions")
    print("RANGES:")
    print(json.dumps(value, indent=2))

    # Theme flip on the real-data ranges: charts re-init from the palette
    # and the overlays must STAY hidden in dark mode (regression guard for
    # the display:flex-overrides-hidden bug).
    click("[data-theme-toggle]")
    time.sleep(1.5)
    value = evaluate(r"""
    (() => ({
      theme: document.documentElement.getAttribute('data-theme'),
      salesEmpty: getComputedStyle(document.getElementById('salesChartEmpty')).display,
      productEmpty: getComputedStyle(document.getElementById('productChartEmpty')).display,
      salesCanvas: [Math.round(document.getElementById('salesChart').clientWidth),
                    Math.round(document.getElementById('salesChart').clientHeight)],
      productCanvas: [Math.round(document.getElementById('productChart').clientWidth),
                      Math.round(document.getElementById('productChart').clientHeight)],
      errors: (window.__qaErrors || []).slice(),
    }))()
    """, "theme flip")
    print("AFTER THEME FLIP (dark):")
    print(json.dumps(value, indent=2))
    shot = cdp.call("Page.captureScreenshot", {"format": "png"})
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs",
                           "dash_realdata_dark.png"), "wb") as f:
        f.write(base64.b64decode(shot["data"]))
    print("screenshot: POS_V2/logs/dash_realdata_dark.png")


if __name__ == "__main__":
    main()
