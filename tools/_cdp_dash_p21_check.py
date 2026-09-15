"""CDP probe for the V2 dashboard Phase 2.1 (period logic + product drilldown).

Verifies against the LIVE server on :5002:
  1. Full load as admin: KPI cards, MTD/YTD period cards (values + deltas),
     ticker, Chart.js, canvases, range captions, category select options.
  2. Sales trend Daily -> Weekly -> Monthly: captions + chart data swap
     (weekly must have 8 labels); no request, no reload.
  3. Product drilldown: Month range -> category select lists real
     categories; picking one re-renders the chart with its labels; metric
     toggle Quantity -> Peso re-sorts bars descending and reformats.
  4. Theme flip: charts re-init, canvas sized.
  5. SPA swap home <-> dashboard — navCount stays 1, no console errors.
  6. Global range bar: preset chips + From/To inputs + Apply/Reset exist;
     the inputs default to month to date (2026-08-01..today) and the
     server-rendered default view is MTD (KPIs show month-to-date values
     with month-aligned prior deltas); Apply re-renders the KPI cards,
     hides the MTD/YTD strip + product scope segment and swaps the top
     orders table; the 30d preset renders 30, Weekly re-buckets (5), a
     custom Aug 1-20 range renders 20 and updates the caption/aria;
     Monthly over the custom range renders 1 bucket; Reset restores the
     server-rendered default view (inputs back to this-month, preset
     pressed); navCount stays 1.
  7. Narrow viewport (1024x678): no horizontal overflow, period strip
     1-column, charts single column.

Usage: venv\\Scripts\\python.exe POS_V2\\tools\\_cdp_dash_p21_check.py [w] [h] [port]
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

    def shot(name):
        out = cdp.call("Page.captureScreenshot", {"format": "png"})
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs",
                               name), "wb") as f:
            f.write(base64.b64decode(out["data"]))
        print("screenshot:", name)

    # --- 1. Full load ---------------------------------------------------------
    cdp.call("Page.navigate", {"url": BASE + "/dashboard"})
    wait(4.5)

    probe = r"""
    (() => {
      const kpis = [...document.querySelectorAll('.dash-kpi')].map((k) => ({
        label: k.querySelector('.dash-kpi-label').textContent,
        value: k.querySelector('.dash-kpi-value').textContent.trim(),
        delta: (k.querySelector('.dash-kpi-delta') || {}).textContent || '',
      }));
      const periods = [...document.querySelectorAll('.dash-period')].map((p) => ({
        label: p.querySelector('.dash-kpi-label').textContent,
        sub: p.querySelector('.dash-period-sub').textContent,
        value: p.querySelector('.dash-period-value').textContent.trim(),
        delta: (p.querySelector('.dash-kpi-delta') || {}).textContent || '',
      }));
      const select = document.getElementById('dashCategorySelect');
      return {
        href: location.href,
        page: document.querySelector('template[data-view-meta]').dataset.page,
        dashDate: document.getElementById('dashDate').textContent,
        ticker: [...document.querySelectorAll('.dash-ticker-chip')].map((c) => c.textContent.trim()),
        kpis,
        periods,
        chartJs: typeof window.Chart === 'function' ? window.Chart.version : 'MISSING',
        salesCanvas: [Math.round(document.getElementById('salesChart').clientWidth),
                      Math.round(document.getElementById('salesChart').clientHeight)],
        prodCanvas: [Math.round(document.getElementById('productChart').clientWidth),
                     Math.round(document.getElementById('productChart').clientHeight)],
        salesRange: document.getElementById('dashSalesRange').textContent,
        productRange: document.getElementById('dashProductRange').textContent,
        seg: {
          sales: document.querySelector('input[name="salesView"]:checked').id,
          product: document.querySelector('input[name="productView"]:checked').id,
          metric: document.querySelector('input[name="productMetric"]:checked').id,
        },
        selectOptions: select ? [...select.options].map((o) => o.value) : null,
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """
    value = evaluate(probe, "full-load probe")
    print("FULL LOAD:")
    print(json.dumps(value, indent=2))
    shot("dash_p21_light.png")

    # --- 2. Weekly trend --------------------------------------------------------
    click("#salesWeekly")
    wait(1.2)
    value = evaluate(r"""
    (() => {
      const chart = window.Chart.getChart(document.getElementById('salesChart'));
      const ds = chart ? chart.data.datasets : [];
      return {
        salesRange: document.getElementById('dashSalesRange').textContent,
        salesChecked: document.querySelector('input[name="salesView"]:checked').id,
        labels: chart ? chart.data.labels : [],
        labelCount: chart ? chart.data.labels.length : 0,
        netLength: ds[0] ? ds[0].data.length : 0,
        grossLength: ds[1] ? ds[1].data.length : 0,
        netLast: ds[0] ? ds[0].data[ds[0].data.length - 1] : null,
        grossLast: ds[1] ? ds[1].data[ds[1].data.length - 1] : null,
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """, "weekly probe")
    print("WEEKLY TREND:")
    print(json.dumps(value, indent=2))

    click("#salesMonthly")
    wait(1.2)
    value = evaluate(r"""
    (() => {
      const chart = window.Chart.getChart(document.getElementById('salesChart'));
      return {
        salesRange: document.getElementById('dashSalesRange').textContent,
        labelCount: chart ? chart.data.labels.length : 0,
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """, "monthly probe")
    print("MONTHLY TREND:")
    print(json.dumps(value, indent=2))

    # --- 3. Product drilldown: category + metric --------------------------------
    click("#productMonth")
    wait(1.2)
    value = evaluate(r"""
    (() => {
      const select = document.getElementById('dashCategorySelect');
      const chart = window.Chart.getChart(document.getElementById('productChart'));
      return {
        productRange: document.getElementById('dashProductRange').textContent,
        selectOptions: [...select.options].map((o) => o.value),
        selectValue: select.value,
        allLabels: chart ? chart.data.labels : [],
        allCount: chart ? chart.data.labels.length : 0,
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """, "month products probe")
    print("MONTH PRODUCTS (All):")
    print(json.dumps(value, indent=2))

    value = evaluate(r"""
    (() => {
      const select = document.getElementById('dashCategorySelect');
      if (select.options.length < 2) return { skipped: true };
      select.value = select.options[1].value;
      select.dispatchEvent(new Event('change'));
      return { chosen: select.value };
    })()
    """, "pick category")
    print("PICK CATEGORY:", value)
    wait(1.2)
    value = evaluate(r"""
    (() => {
      const select = document.getElementById('dashCategorySelect');
      const chart = window.Chart.getChart(document.getElementById('productChart'));
      return {
        selectValue: select.value,
        labels: chart ? chart.data.labels : [],
        labelCount: chart ? chart.data.labels.length : 0,
        aria: document.getElementById('productChart').getAttribute('aria-label'),
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """, "category filtered probe")
    print("CATEGORY FILTERED:")
    print(json.dumps(value, indent=2))

    click("#productPeso")
    wait(1.2)
    value = evaluate(r"""
    (() => {
      const chart = window.Chart.getChart(document.getElementById('productChart'));
      const values = chart ? chart.data.datasets[0].data : [];
      const sorted = values.every((v, i, a) => i === 0 || a[i - 1] >= v);
      const xTicks = chart && chart.options.scales.x && chart.options.scales.x.ticks;
      return {
        metric: document.querySelector('input[name="productMetric"]:checked').id,
        datasetLabel: chart ? chart.data.datasets[0].label : null,
        values,
        sortedDesc: sorted,
        firstTick: values.length ? values[0] : null,
        lastTick: values.length ? values[values.length - 1] : null,
        aria: document.getElementById('productChart').getAttribute('aria-label'),
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """, "peso metric probe")
    print("PESO METRIC:")
    print(json.dumps(value, indent=2))

    click("#productQty")
    wait(1.2)
    click("#dashCategorySelect")  # noop safety; select back to All via value below
    value = evaluate(r"""
    (() => {
      const select = document.getElementById('dashCategorySelect');
      select.value = 'All';
      select.dispatchEvent(new Event('change'));
      const chart = window.Chart.getChart(document.getElementById('productChart'));
      return {
        metric: document.querySelector('input[name="productMetric"]:checked').id,
        selectValue: select.value,
        labelCount: chart ? chart.data.labels.length : 0,
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """, "reset to qty/all")
    print("RESET QTY + ALL:")
    print(json.dumps(value, indent=2))

    # --- 4. Theme flip ----------------------------------------------------------
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
    shot("dash_p21_dark.png")

    # --- 5. SPA swap demo --------------------------------------------------------
    click('a.side-link[href="/home"]')
    wait(2.0)
    value = evaluate(r"""
    (() => ({
      page: document.querySelector('template[data-view-meta]').dataset.page,
      navCount: performance.getEntriesByType('navigation').length,
      errors: window.__qaErrors.slice(),
    }))()
    """, "swap to home")
    print("SPA SWAP -> HOME:")
    print(json.dumps(value, indent=2))

    click('a.side-link[href="/dashboard"]')
    wait(2.0)
    value = evaluate(r"""
    (() => {
      const select = document.getElementById('dashCategorySelect');
      return {
        page: document.querySelector('template[data-view-meta]').dataset.page,
        periodCount: document.querySelectorAll('.dash-period').length,
        selectOptions: select ? [...select.options].map((o) => o.value) : null,
        selectValue: select ? select.value : null,
        salesRange: document.getElementById('dashSalesRange').textContent,
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """, "swap back to dashboard")
    print("SPA SWAP -> DASHBOARD:")
    print(json.dumps(value, indent=2))

    # --- 6. Global range bar: presets + custom From/To dates -------------------
    value = evaluate(r"""
    (() => ({
      presets: [...document.querySelectorAll('.dash-preset[data-preset]')].map((b) => b.dataset.preset),
      startValue: document.getElementById('dashRangeStart').value,
      endValue: document.getElementById('dashRangeEnd').value,
      apply: !!document.getElementById('dashRangeApply'),
      reset: !!document.getElementById('dashRangeReset'),
      max: document.getElementById('dashRangeEnd').max,
      kpiOrdersBefore: document.getElementById('kpiOrdersValue').textContent,
      stripVisible: !document.getElementById('dashPeriodStrip').hidden,
      scopeSegVisible: !document.getElementById('dashProductScopeSeg').hidden,
      navCount: performance.getEntriesByType('navigation').length,
      errors: window.__qaErrors.slice(),
    }))()
    """, "global range bar UI probe")
    print("RANGE BAR UI:")
    print(json.dumps(value, indent=2))

    click("#dashRangeApply")
    wait(1.8)
    value = evaluate(r"""
    (() => {
      const chart = window.Chart.getChart(document.getElementById('salesChart'));
      return {
        caption: document.getElementById('dashSalesRange').textContent,
        labelCount: chart ? chart.data.labels.length : 0,
        firstLabel: chart ? chart.data.labels[0] : null,
        lastLabel: chart ? chart.data.labels[chart.data.labels.length - 1] : null,
        aria: document.getElementById('salesChart').getAttribute('aria-label'),
        kpiOrders: document.getElementById('kpiOrdersValue').textContent,
        kpiNet: document.getElementById('kpiNetValue').textContent,
        kpiDelta: document.getElementById('kpiNetDelta').textContent,
        stripHidden: document.getElementById('dashPeriodStrip').hidden,
        scopeSegHidden: document.getElementById('dashProductScopeSeg').hidden,
        productCaption: document.getElementById('dashProductRange').textContent,
        topOrdersRows: document.querySelectorAll('#dashTopOrdersBody tr').length,
        topOrdersTitle: document.getElementById('dashTopOrdersTitle').textContent,
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """, "apply default range probe")
    print("APPLY (default this-month inputs):")
    print(json.dumps(value, indent=2))
    shot("dash_p21_range.png")

    click('.dash-preset[data-preset="30d"]')
    wait(1.8)
    value = evaluate(r"""
    (() => {
      const chart = window.Chart.getChart(document.getElementById('salesChart'));
      return {
        caption: document.getElementById('dashSalesRange').textContent,
        startValue: document.getElementById('dashRangeStart').value,
        endValue: document.getElementById('dashRangeEnd').value,
        pressed: [...document.querySelectorAll('.dash-preset')].map((b) => [b.dataset.preset, b.getAttribute('aria-pressed')]),
        labelCount: chart ? chart.data.labels.length : 0,
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """, "30d preset probe")
    print("30D PRESET:")
    print(json.dumps(value, indent=2))

    click("#salesWeekly")
    wait(1.2)
    value = evaluate(r"""
    (() => {
      const chart = window.Chart.getChart(document.getElementById('salesChart'));
      return {
        caption: document.getElementById('dashSalesRange').textContent,
        salesChecked: document.querySelector('input[name="salesView"]:checked').id,
        labelCount: chart ? chart.data.labels.length : 0,
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """, "weekly over 30d probe")
    print("WEEKLY OVER 30D:")
    print(json.dumps(value, indent=2))

    click("#salesDaily")
    wait(1.2)
    value = evaluate(r"""
    (() => {
      const start = document.getElementById('dashRangeStart');
      const end = document.getElementById('dashRangeEnd');
      start.value = '2026-08-01';
      end.value = '2026-08-20';
      return { start: start.value, end: end.value };
    })()
    """, "set custom dates")
    click("#dashRangeApply")
    wait(1.8)
    value = evaluate(r"""
    (() => {
      const chart = window.Chart.getChart(document.getElementById('salesChart'));
      return {
        caption: document.getElementById('dashSalesRange').textContent,
        labelCount: chart ? chart.data.labels.length : 0,
        firstLabel: chart ? chart.data.labels[0] : null,
        lastLabel: chart ? chart.data.labels[chart.data.labels.length - 1] : null,
        netLast: chart ? chart.data.datasets[0].data[chart.data.datasets[0].data.length - 1] : null,
        aria: document.getElementById('salesChart').getAttribute('aria-label'),
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """, "custom range probe")
    print("CUSTOM AUG 1-20:")
    print(json.dumps(value, indent=2))

    click("#salesMonthly")
    wait(1.2)
    value = evaluate(r"""
    (() => {
      const chart = window.Chart.getChart(document.getElementById('salesChart'));
      return {
        caption: document.getElementById('dashSalesRange').textContent,
        labelCount: chart ? chart.data.labels.length : 0,
        firstLabel: chart ? chart.data.labels[0] : null,
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """, "monthly over custom probe")
    print("MONTHLY OVER CUSTOM:")
    print(json.dumps(value, indent=2))

    click("#dashRangeReset")
    wait(1.8)
    value = evaluate(r"""
    (() => {
      const chart = window.Chart.getChart(document.getElementById('salesChart'));
      return {
        caption: document.getElementById('dashSalesRange').textContent,
        productCaption: document.getElementById('dashProductRange').textContent,
        labelCount: chart ? chart.data.labels.length : 0,
        stripVisible: !document.getElementById('dashPeriodStrip').hidden,
        scopeSegVisible: !document.getElementById('dashProductScopeSeg').hidden,
        kpiOrders: document.getElementById('kpiOrdersValue').textContent,
        topOrdersTitle: document.getElementById('dashTopOrdersTitle').textContent,
        pressed: [...document.querySelectorAll('.dash-preset[data-preset]')].map((b) => b.getAttribute('aria-pressed')),
        startValue: document.getElementById('dashRangeStart').value,
        endValue: document.getElementById('dashRangeEnd').value,
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """, "reset probe")
    print("RESET (back to default):")
    print(json.dumps(value, indent=2))

    # --- 7. Narrow viewport ------------------------------------------------------
    cdp.call("Emulation.setDeviceMetricsOverride", {
        "width": 1024, "height": 678, "deviceScaleFactor": 1, "mobile": False,
    })
    wait(0.8)
    value = evaluate(r"""
    (() => {
      const kpiGrid = getComputedStyle(document.querySelector('.dash-kpis'));
      const periods = getComputedStyle(document.querySelector('.dash-periods'));
      const charts = getComputedStyle(document.querySelector('.dash-charts'));
      const doc = document.documentElement;
      const offenders = [...document.querySelectorAll('body *')]
        .filter((el) => {
          const r = el.getBoundingClientRect();
          const s = getComputedStyle(el);
          return r.width > 0 && r.right > doc.clientWidth + 1 &&
                 s.position !== 'fixed' && s.position !== 'absolute';
        })
        .slice(0, 6)
        .map((el) => (el.id ? '#' + el.id : el.className || el.tagName) +
                      ' right=' + Math.round(el.getBoundingClientRect().right));
      return {
        viewport: [window.innerWidth, window.innerHeight],
        overflowX: doc.scrollWidth > doc.clientWidth ? doc.scrollWidth - doc.clientWidth : 0,
        kpiColumns: kpiGrid.gridTemplateColumns.split(' ').length,
        periodColumns: periods.gridTemplateColumns.split(' ').length,
        chartsColumns: charts.gridTemplateColumns.split(' ').length,
        offenders,
        navCount: performance.getEntriesByType('navigation').length,
        errors: window.__qaErrors.slice(),
      };
    })()
    """, "narrow probe")
    print("NARROW (1024x678):")
    print(json.dumps(value, indent=2))
    shot("dash_p21_narrow.png")


if __name__ == "__main__":
    main()
