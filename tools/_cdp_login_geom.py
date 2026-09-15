"""CDP geometry check: field widths, eye toggle position, button width."""
import base64
import json
import os
import socket
import struct
import subprocess
import time
import urllib.request

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
EDGE = next(p for p in EDGE_CANDIDATES if os.path.exists(p))
PORT = 9334
PROFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "_cdp_profile2")
URL = "http://127.0.0.1:5002/login"


class CDP:
    def __init__(self, port):
        self.port = port
        self.sock = None

    def connect(self):
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
        raise RuntimeError("timeout waiting {method}")


def main():
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
    cdp.call("Page.navigate", {"url": URL})
    time.sleep(3.5)

    expr = r"""
    (() => {
      const box = (sel) => {
        const el = document.querySelector(sel);
        if (!el) return null;
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return { w: Math.round(r.width), h: Math.round(r.height),
                 left: Math.round(r.left), right: Math.round(r.right),
                 top: Math.round(r.top) };
      };
      return {
        usernameInput: box('#username'),
        passwordInput: box('#password'),
        eyeToggle: box('[data-password-toggle]'),
        submitBtn: box('.login-submit'),
        fieldUsername: box('.login-field:not(.login-field--secret)'),
        fieldPassword: box('.login-field--secret'),
        card: box('.login-card'),
        note: box('.login-note'),
        sidebar: box('.login-sidebar'),
        main: box('.login-main'),
      };
    })()
    """
    result = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    print(json.dumps(result["result"].get("value"), indent=2))


if __name__ == "__main__":
    main()
