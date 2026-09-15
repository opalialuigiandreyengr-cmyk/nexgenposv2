"""CDP probe for the V2 dashboard (Phase 2 exit check).

Verifies against the LIVE server on :5002:
  1. Full load of /dashboard as admin: KPI cards render real values, ticker
     chips, Chart.js imported (window.Chart), both canvases laid out, empty
     states hidden, dashDate filled, zero console errors.
  2. Date filter: Monthly + Month toggles swap the range captions and keep
     the charts alive (no request, no reload).
  3. Theme flip: data-theme flips and both charts re-init (canvas sized).
  4. SPA swap demo: home <-> dashboard via the sidebar — only #view-root
     changes, navigation count stays 1 (no full reload).
  5. Narrow viewport (1024x678): no horizontal overflow, KPI grid 2-col,
     chart row single column.

Usage: venv\\Scripts\\python.exe POS_V2\\tools\\_cdp_dash_check.py [w] [h] [port]
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
PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 9341
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = "http://127.0.0.1:" + os.environ.get("POS_CDP_PORT", "5002")

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
    cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": ERROR_CAPTURE})
    cdp.call("Network.setCookie", {
        "name": "session", "value": cookie, "url": BASE + "/",
        "httpOnly": True, "sameSite": "Lax", "path": "/",
    })

    def evaluate(expression, label):
        result = cdp.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True})
        if "exceptionDetails" in result:
            print("EXCEPTION:", label, json.dumps(result["exceptionDetails"], indent=2)[:1200])
            return None
        return result["result"].get("value")

    def click(selector):
        evaluate(f"document.querySelector({json.dumps(selector)}).click();", "click " + selector)

    def wait(seconds):
        time.sleep(seconds)

    # --- 1. Full load ---------------------------------------------------------
    cdp.call("Page.navigate", {"url": BASE + "/dashboard"})
    wait(4.5)  # JSON parse + chart mount + animations settle

    probe = r"""
    (() => {
      const kpis = [...document.querySelectorAll('.dash-kpi')].map((k) => ({
        label: k.querySelector('.dash-kpi-label').textContent,
        value: k.querySelector('.dash-kpi-value').textContent.trim(),
        delta: (k.querySelector('.dash-kpi-delta') || {}).textContent || '',
      }));
      const sales = document.getElementById('salesChart');
      const prod = document.getElementById('productChart');
      const segChecked = (name) => {
        const c = document.querySelector(`input[name="${name}"]:checked`);
        return c ? c.id : null;
      };
      return {
        href: location.href,
        title: document.title,
        page: document.querySelector('template[data-view-meta]').dataset.page,
        dashDate: document.getElementById('dashDate').textContent,
        ticker: [...document.querySelectorAll('.dash-ticker-chip')].map((c) => c.textContent.trim()),
        kpis,
        chartJs: typeof window.Chart === 'function' ? window.Chart.version : 'MISSING',
        salesCanvas: sales ? [Math.round(sales.clientWidth), Math.round(sales.clientHeight)] : null,
        prodCanvas: prod ? [Math.round(prod.clientWidth), Math.round(prod.clientHeight)] : null,
        salesEmptyHidden: document.getElementById('salesChartEmpty').hidden,
        prodEmptyHidden: document.getElementById('productChartEmpty').hidden,
        salesRange: document.getElementById('dashSalesRange').textContent,
        productRange: document.getElementById('dashProductRange').textContent,
        seg: { sales: segChecked('salesView'), product: segChecked('productView') },
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """
    value = evaluate(probe, "full-load probe")
    print("FULL LOAD:")
    print(json.dumps(value, indent=2))

    shot = cdp.call("Page.captureScreenshot", {"format": "png"})
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs",
                           f"dash_cdp_{WIN_W}x{WIN_H}.png"), "wb") as f:
        f.write(base64.b64decode(shot["data"]))

    # --- 2. Date filter toggles ------------------------------------------------
    click("#salesMonthly")
    wait(1.2)
    click("#productMonth")
    wait(1.2)
    value = evaluate(r"""
    (() => ({
      salesRange: document.getElementById('dashSalesRange').textContent,
      productRange: document.getElementById('dashProductRange').textContent,
      salesChecked: document.querySelector('input[name="salesView"]:checked').id,
      productChecked: document.querySelector('input[name="productView"]:checked').id,
      salesCanvas: [Math.round(document.getElementById('salesChart').clientWidth),
                    Math.round(document.getElementById('salesChart').clientHeight)],
      prodCanvas: [Math.round(document.getElementById('productChart').clientWidth),
                   Math.round(document.getElementById('productChart').clientHeight)],
      navCount: performance.getEntriesByType('navigation').length,
      errors: window.__qaErrors.slice(),
    }))()
    """, "filter probe")
    print("AFTER FILTER TOGGLES (Monthly / Month):")
    print(json.dumps(value, indent=2))

    # --- 3. Theme flip ----------------------------------------------------------
    click("[data-theme-toggle]")
    wait(1.5)
    value = evaluate(r"""
    (() => ({
      theme: document.documentElement.getAttribute('data-theme'),
      salesCanvas: [Math.round(document.getElementById('salesChart').clientWidth),
                    Math.round(document.getElementById('salesChart').clientHeight)],
      prodCanvas: [Math.round(document.getElementById('productChart').clientWidth),
                   Math.round(document.getElementById('productChart').clientHeight)],
      navCount: performance.getEntriesByType('navigation').length,
      errors: window.__qaErrors.slice(),
    }))()
    """, "theme probe")
    print("AFTER THEME FLIP:")
    print(json.dumps(value, indent=2))
    shot = cdp.call("Page.captureScreenshot", {"format": "png"})
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs",
                           f"dash_cdp_dark_{WIN_W}x{WIN_H}.png"), "wb") as f:
        f.write(base64.b64decode(shot["data"]))

    # --- 4. SPA swap demo: home <-> dashboard ----------------------------------
    click('a.side-link[href="/home"]')
    wait(2.0)
    value = evaluate(r"""
    (() => ({
      page: document.querySelector('template[data-view-meta]').dataset.page,
      title: document.title,
      hasHomeStats: !!document.getElementById('homeStatSales'),
      hasTicker: !!document.querySelector('.home-ticker'),
      navCount: performance.getEntriesByType('navigation').length,
      errors: window.__qaErrors.slice(),
    }))()
    """, "swap to home")
    print("SPA SWAP -> HOME:")
    print(json.dumps(value, indent=2))

    click('a.side-link[href="/dashboard"]')
    wait(2.0)
    value = evaluate(r"""
    (() => ({
      page: document.querySelector('template[data-view-meta]').dataset.page,
      title: document.title,
      hasKpis: document.querySelectorAll('.dash-kpi').length,
      hasChart: !!document.getElementById('salesChart'),
      salesRange: document.getElementById('dashSalesRange').textContent,
      productRange: document.getElementById('dashProductRange').textContent,
      navCount: performance.getEntriesByType('navigation').length,
      errors: window.__qaErrors.slice(),
    }))()
    """, "swap to dashboard")
    print("SPA SWAP -> DASHBOARD (back to dashboard):")
    print(json.dumps(value, indent=2))

    # --- 5. Narrow viewport ------------------------------------------------------
    cdp.call("Emulation.setDeviceMetricsOverride", {
        "width": 1024, "height": 678, "deviceScaleFactor": 1, "mobile": False,
    })
    wait(0.5)
    value = evaluate(r"""
    (() => {
      const kpiGrid = getComputedStyle(document.querySelector('.dash-kpis'));
      const charts = getComputedStyle(document.querySelector('.dash-charts'));
      const doc = document.documentElement;
      return {
        viewport: [window.innerWidth, window.innerHeight],
        overflowX: doc.scrollWidth > doc.clientWidth ? doc.scrollWidth - doc.clientWidth : 0,
        kpiColumns: kpiGrid.gridTemplateColumns.split(' ').length,
        chartsColumns: charts.gridTemplateColumns.split(' ').length,
        salesCanvas: [Math.round(document.getElementById('salesChart').clientWidth),
                      Math.round(document.getElementById('salesChart').clientHeight)],
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """, "narrow probe")
    print("NARROW (1024x678):")
    print(json.dumps(value, indent=2))


if __name__ == "__main__":
    main()
