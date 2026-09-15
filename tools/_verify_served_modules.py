"""Verify the LIVE server serves the fixed page modules (cache-bust parity)."""
import urllib.request

BASE = "http://127.0.0.1:5001"
V = "1787906522"

def fetch(path):
    req = urllib.request.Request(f"{BASE}{path}")
    with urllib.request.urlopen(req, timeout=10) as res:
        return res.status, res.read().decode("utf-8", "replace")

status, od = fetch(f"/assets/js/pages/order_details.js?v={V}")
print(f"order_details.js: HTTP {status}, {len(od)} bytes")
print("  has destroy():", "function destroy()" in od)
print("  has visibility re-acquire:", "re-acquire the lock" in od)
print("  has pagehide release:", "addEventListener('pagehide', releasePresence)" in od)

status, st = fetch(f"/assets/js/pages/settlement.js?v={V}")
print(f"settlement.js: HTTP {status}, {len(st)} bytes")
print("  has api.post presence:", "api.post(`/order_presence/${ORDER_ID}`" in st)
print("  has 409-only conflict gate:", "err.status === 409" in st)
print("  has ApiError import:", "ApiError" in st)
