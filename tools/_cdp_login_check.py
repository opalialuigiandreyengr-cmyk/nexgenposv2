"""Minimal CDP client (stdlib only) to inspect the live login page.

Launches its own headless Edge, navigates to the login page, waits for the
reveal animation to finish, then reports per-element computed styles and
bounding boxes for every direct child of .login-card plus the sidebar height.
"""
import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
EDGE = next(p for p in EDGE_CANDIDATES if os.path.exists(p))
PORT = 9333
PROFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs",
                      f"_cdp_profile_{os.getpid()}")
URL = "http://127.0.0.1:5002/login"

# Optional CLI: _cdp_login_check.py <width> <height> <port>
WIN_W = int(sys.argv[1]) if len(sys.argv) > 1 else 1440
WIN_H = int(sys.argv[2]) if len(sys.argv) > 2 else 900
if len(sys.argv) > 3:
    PORT = int(sys.argv[3])


class CDP:
    def __init__(self, port):
        self.port = port
        self.sock = None

    def connect(self):
        # Fetch the ws URL for the page target.
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json") as r:
                    targets = json.loads(r.read())
                page = next(t for t in targets if t.get("type") == "page")
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("no CDP page target")
        ws_url = page["webSocketDebuggerUrl"]
        host, _, path = ws_url.replace("ws://", "").partition("/")
        host, _, port = host.partition(":")
        self.sock = socket.create_connection((host, int(port)), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.sock.recv(4096)
        self._msg_id = 0

    def _send_frame(self, payload: bytes):
        mask = os.urandom(4)
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def _recv_frame(self):
        hdr = self.sock.recv(2)
        if len(hdr) < 2:
            raise RuntimeError("ws closed")
        _, ln = hdr
        opcode = hdr[0] & 0x0F
        if ln == 126:
            ln = struct.unpack(">H", self.sock.recv(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self.sock.recv(8))[0]
        data = b""
        while len(data) < ln:
            chunk = self.sock.recv(ln - len(data))
            if not chunk:
                break
            data += chunk
        return opcode, data

    def call(self, method, params=None, timeout=15):
        self._msg_id += 1
        mid = self._msg_id
        self._send_frame(json.dumps({"id": mid, "method": method, "params": params or {}}).encode())
        deadline = time.time() + timeout
        while time.time() < deadline:
            opcode, data = self._recv_frame()
            if opcode != 1:
                continue
            msg = json.loads(data)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})
        raise RuntimeError(f"timeout waiting {method}")


def main():
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
    cdp.call("Page.navigate", {"url": URL})
    time.sleep(3.5)  # real time: reveal animation (<=1s) fully settled

    expr = r"""
    (() => {
      const probe = {
        href: location.href,
        title: document.title,
        bodyLen: (document.body && document.body.innerHTML.length) || -1,
        hasCard: !!document.querySelector('.login-card'),
      };
      return probe;
    })()
    """
    result = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    print("PROBE:", json.dumps(result["result"].get("value"), indent=2))
    time.sleep(1.0)

    expr = r"""
    (() => {
      const card = document.querySelector('.login-card');
      const kids = [...card.children].map((el, i) => {
        const s = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return {
          i: i + 1,
          tag: el.tagName.toLowerCase(),
          cls: (el.className || '').toString().slice(0, 40),
          display: s.display,
          visibility: s.visibility,
          opacity: s.opacity,
          height: Math.round(r.height),
          width: Math.round(r.width),
          top: Math.round(r.top),
        };
      });
      const sidebar = document.querySelector('.login-sidebar').getBoundingClientRect();
      const main = document.querySelector('.login-main').getBoundingClientRect();
      const body = document.body.getBoundingClientRect();
      return {
        inner: [window.innerWidth, window.innerHeight],
        body: [Math.round(body.height), Math.round(body.bottom)],
        sidebar: [Math.round(sidebar.height), Math.round(sidebar.bottom)],
        main: [Math.round(main.height), Math.round(main.bottom)],
        sidebarBox: (() => { const r = document.querySelector('.login-sidebar').getBoundingClientRect(); return [Math.round(r.width), Math.round(r.left), Math.round(r.top)]; })(),
        mainBox: (() => { const r = document.querySelector('.login-main').getBoundingClientRect(); return [Math.round(r.width), Math.round(r.left), Math.round(r.top)]; })(),
        card: kids,
        formVisible: !!document.querySelector('#login-form') &&
          getComputedStyle(document.querySelector('#login-form')).display !== 'none',
        btnColor: getComputedStyle(document.querySelector('.login-submit')).color,
        btnText: document.querySelector('.login-submit').textContent.trim(),
      };
    })()
    """
    result = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    if "exceptionDetails" in result:
        print("EXCEPTION:", json.dumps(result["exceptionDetails"], indent=2)[:2000])
    value = result["result"].get("value")
    print(json.dumps(value, indent=2))

    shot = cdp.call("Page.captureScreenshot", {"format": "png"})
    png = base64.b64decode(shot["data"])
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "login_cdp_1440.png")
    with open(out, "wb") as f:
        f.write(png)
    print("screenshot:", out)


if __name__ == "__main__":
    main()
