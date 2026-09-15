"""CDP probe: CSS application after htmx SPA swaps.

User report: on /home, clicking Dashboard renders UNSTYLED content;
a refresh styles it; then clicking Home renders unstyled again.
Symptom: stylesheets inside swapped fragments never apply.

Diagnoses whether the <link> element is present in the DOM after swap,
whether the browser fetched it, and whether it made it into
document.styleSheets (applied), plus computed-style evidence.

Usage: venv\\Scripts\\python.exe POS_V2\\tools\\_cdp_swap_style_check.py [port]
"""
import json
import os
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _cdp_login_check import CDP, EDGE, PROFILE  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9350
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

    probe = r"""
    (() => {
      const sheetHrefs = [...document.styleSheets]
        .map((s) => s.href || '(inline)')
        .filter((h) => h.includes('dashboard.css') || h.includes('home.css'));
      const domLinks = [...document.querySelectorAll('link[rel="stylesheet"]')]
        .map((l) => l.href)
        .filter((h) => h.includes('dashboard.css') || h.includes('home.css'));
      const fetched = performance.getEntriesByType('resource')
        .filter((e) => e.name.includes('dashboard.css') || e.name.includes('home.css'))
        .map((e) => ({ name: e.name.split('/').pop(), dur: Math.round(e.duration) }));
      const kpis = document.querySelector('.dash-kpis');
      const kpi = document.querySelector('.dash-kpi');
      const wrap = document.querySelector('#salesChartWrap');
      const page = document.querySelector('template[data-view-meta]').dataset.page;
      return {
        page,
        domLinks,
        sheetHrefs,
        fetched,
        kpiGrid: kpis ? getComputedStyle(kpis).gridTemplateColumns : null,
        kpiRadius: kpi ? getComputedStyle(kpi).borderRadius : null,
        chartWrapH: wrap ? getComputedStyle(wrap).height : null,
        navCount: performance.getEntriesByType('navigation').length,
      };
    })()
    """

    # 1. Full load of /home (baseline: home.css applied)
    cdp.call("Page.navigate", {"url": BASE + "/home"})
    time.sleep(3.0)
    print("AFTER FULL LOAD /home:")
    print(json.dumps(evaluate(probe, "baseline home"), indent=2))

    # 2. SPA swap -> /dashboard
    click('a.side-link[href="/dashboard"]')
    time.sleep(1.5)
    print("AFTER SWAP -> /dashboard (t+1.5s):")
    print(json.dumps(evaluate(probe, "swap dash 1"), indent=2))
    time.sleep(2.0)
    print("AFTER SWAP -> /dashboard (t+3.5s):")
    print(json.dumps(evaluate(probe, "swap dash 2"), indent=2))

    # 3. SPA swap back -> /home
    click('a.side-link[href="/home"]')
    time.sleep(1.5)
    print("AFTER SWAP -> /home (t+1.5s):")
    print(json.dumps(evaluate(probe, "swap home"), indent=2))

    # 4. Swap back to /dashboard and capture the styled state.
    click('a.side-link[href="/dashboard"]')
    time.sleep(2.5)
    print("AFTER SWAP -> /dashboard (final):")
    print(json.dumps(evaluate(probe, "swap dash final"), indent=2))
    import base64
    shot = cdp.call("Page.captureScreenshot", {"format": "png"})
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs",
                           "dash_swapped_styled.png"), "wb") as f:
        f.write(base64.b64decode(shot["data"]))
    print("screenshot: POS_V2/logs/dash_swapped_styled.png")


if __name__ == "__main__":
    main()
