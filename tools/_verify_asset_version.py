"""Verify the TTL-refreshed asset version flows into rendered shell HTML."""
import sys

import os
POS_V2_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if POS_V2_DIR not in sys.path:
    sys.path.insert(0, POS_V2_DIR)

from flask import render_template_string

from POS_V2.app import _current_asset_version, build_shell_app

app = build_shell_app()

v1 = _current_asset_version()
v2 = _current_asset_version()
print("boot version:", app.config["ASSET_VERSION"])
print("stable across calls:", v1 == v2, f"({v1})")

with app.test_request_context():
    html = render_template_string("{{ asset('js/pages/order_details.js') }}")
    print("rendered module URL:", html)
    ok = f"?v={v1}" in html
    print("version stamped:", ok)
    if not ok:
        sys.exit(1)

# Simulate an asset edit: force-expire the TTL cache after touching a temp
# file so the next call MUST pick up a newer mtime. The probe must live in
# POS_V2/static (the scanned tree), NOT app.static_folder (legacy static).
import os
import time

from POS_V2.app import STATIC_DIR

probe = os.path.join(STATIC_DIR, "_version_probe.tmp")
with open(probe, "w") as fh:
    fh.write("probe")
os.utime(probe, (time.time() + 5, time.time() + 5))  # strictly newer mtime
_asset_version_cache = getattr(sys.modules["POS_V2.app"], "_asset_version_cache")
_asset_version_cache["at"] = 0.0  # expire
v3 = _current_asset_version()
os.remove(probe)
print("version moved after edit:", v3 > v1, f"({v1} -> {v3})")
if v3 <= v1:
    sys.exit(1)
print("OK - dynamic asset version verified")
