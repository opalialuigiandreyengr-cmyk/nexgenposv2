# Screenshot /login at a tall desktop viewport with headless Edge.
param(
    [string]$Url = "http://127.0.0.1:5050/login",
    [string]$Out = "C:\Users\Marketing Head\Desktop\POS\POS_V2\logs\login_1440_fixed.png",
    [int]$Width = 1440,
    [int]$Height = 900
)
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edge)) { $edge = "C:\Program Files\Microsoft\Edge\Application\msedge.exe" }
& $edge --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 `
  --virtual-time-budget=3000 `
  --window-size="$Width,$Height" --screenshot=$Out $Url 2>$null
Write-Output "screenshot: $Out"
