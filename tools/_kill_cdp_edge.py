"""Kill stale headless Edge CDP probe instances (safe: only _cdp_profile ones)."""
import subprocess

out = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" "
     "| Where-Object { $_.CommandLine -like '*_cdp_profile*' } "
     "| Select-Object -ExpandProperty ProcessId"],
    capture_output=True, text=True,
).stdout
ids = []
for line in out.splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        int(line)
    except ValueError:
        continue
    ids.append(line)
for pid in ids:
    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
print(f"killed {len(ids)} stale edge probe(s)")
