"""A minimal Chrome DevTools Protocol client, standard library only.

Used by dev/shots.py (README screenshots) and tests/test_ui.py (the headless smoke test). It
starts Chrome or Edge headless on a throwaway profile with remote debugging on a free
127.0.0.1 port, connects to the first page target over a WebSocket (just enough of RFC 6455),
and sends DevTools commands. Events the browser sends (console messages, exceptions, …) are
kept in Session.events, so a caller can check them afterwards.

    with Browser(chrome_path) as browser:
        page = browser.page()
        page.call("Runtime.enable")
        page.navigate("http://127.0.0.1:8000/")
        page.wait("document.querySelector('h1')")
        print(page.evaluate("document.title"))
"""
import base64
import json
import os
import select
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request

WIDTH, HEIGHT = 1440, 920
MAX_EVENTS = 5000


class CdpError(RuntimeError):
    """DevTools answered a command with an error, or the page threw while evaluating."""


class WebSocket:
    """One client WebSocket to a DevTools page: masked text frames out, whole messages in."""

    def __init__(self, url, timeout=30):
        if not url.startswith("ws://"):
            raise ValueError(f"not a ws:// URL: {url}")
        rest = url[len("ws://"):]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.timeout = timeout
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                           "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("the DevTools handshake was cut off")
            buf += chunk
        head, _, rest_bytes = buf.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise OSError("DevTools refused the WebSocket: " + head[:120].decode(errors="replace"))
        self.pending = bytearray(rest_bytes)

    def _exact(self, n):
        out = bytearray()
        if self.pending:
            take = self.pending[:n]
            out += take
            del self.pending[:len(take)]
        while len(out) < n:
            chunk = self.sock.recv(min(1 << 20, n - len(out)))
            if not chunk:
                raise OSError("DevTools closed the connection")
            out += chunk
        return bytes(out)

    def send(self, text, opcode=0x1):
        data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
        header = bytearray([0x80 | opcode])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def ready(self, wait):
        """Whether a frame has started arriving within `wait` seconds."""
        if self.pending:
            return True
        readable, _, _ = select.select([self.sock], [], [], max(0.0, wait))
        return bool(readable)

    def recv(self):
        """The next whole text message (pings are answered; a close frame raises OSError)."""
        message = bytearray()
        while True:
            b1, b2 = self._exact(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._exact(8))[0]
            mask = self._exact(4) if b2 & 0x80 else None
            payload = self._exact(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            opcode = b1 & 0x0F
            if opcode == 0x9:        # ping
                self.send(payload, 0xA)
                continue
            if opcode == 0xA:        # pong
                continue
            if opcode == 0x8:        # close
                raise OSError("DevTools closed the connection")
            message += payload
            if b1 & 0x80:
                return message.decode("utf-8")

    def close(self):
        try:
            self.send(b"", 0x8)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class Session:
    """DevTools commands on one page. Every event that arrives is kept in `events`."""

    def __init__(self, ws_url):
        self.ws = WebSocket(ws_url)
        self.next_id = 0
        self.events = []

    def _keep(self, message):
        if "method" in message and "id" not in message:
            self.events.append(message)
            if len(self.events) > MAX_EVENTS:
                del self.events[:len(self.events) - MAX_EVENTS]

    def call(self, method, params=None, timeout=30):
        self.next_id += 1
        mid = self.next_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self.ws.ready(end - time.monotonic()):
                break
            message = json.loads(self.ws.recv())
            if message.get("id") == mid:
                if "error" in message:
                    raise CdpError(f"{method}: {message['error']}")
                return message.get("result", {})
            self._keep(message)
        raise TimeoutError(f"DevTools did not answer {method} in {timeout} s")

    def pump(self, seconds=0.0):
        """Take in the events that arrive within `seconds` (at least those already waiting)."""
        end = time.monotonic() + seconds
        while time.monotonic() < end + 2 and self.ws.ready(max(0.0, end - time.monotonic())):
            self._keep(json.loads(self.ws.recv()))

    def evaluate(self, expression, timeout=30):
        """The value of a JavaScript expression in the page (promises awaited)."""
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                "awaitPromise": True}, timeout=timeout)
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            text = ((details.get("exception") or {}).get("description") or details.get("text") or "an error")
            raise CdpError(f"the page threw: {text}")
        return (result.get("result") or {}).get("value")

    def wait(self, condition, timeout=15, after=0.0):
        """Wait until the JavaScript `condition` is truthy. True when it was, False on timeout."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if self.evaluate(f"Boolean({condition})"):
                    if after:
                        self.pump(after)
                    return True
            except (CdpError, OSError, TimeoutError):
                pass
            self.pump(0.2)
        return False

    def navigate(self, url):
        return self.call("Page.navigate", {"url": url})

    def key(self, key, code=None, key_code=0, modifiers=0, text=""):
        """Press and release one key, as a real keyboard would (modifiers: 1 Alt, 2 Ctrl, 4 Meta, 8 Shift)."""
        base = {"key": key, "code": code or key, "windowsVirtualKeyCode": key_code,
                "nativeVirtualKeyCode": key_code, "modifiers": modifiers}
        self.call("Input.dispatchKeyEvent", {"type": "rawKeyDown" if not text else "keyDown", **base,
                                             **({"text": text} if text else {})})
        self.call("Input.dispatchKeyEvent", {"type": "keyUp", **base})

    def screenshot(self):
        """The viewport as PNG bytes."""
        return base64.b64decode(self.call("Page.captureScreenshot", {"format": "png"})["data"])

    def close(self):
        self.ws.close()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def page_url(port, timeout=20):
    """The WebSocket URL of the first page target on a DevTools port."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with opener.open(f"http://127.0.0.1:{port}/json", timeout=2) as r:
                for target in json.loads(r.read().decode()):
                    if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                        return target["webSocketDebuggerUrl"]
        except (OSError, ValueError):
            pass
        time.sleep(0.3)
    raise TimeoutError("headless Chrome did not open DevTools")


def _kill(proc):
    try:
        from app.winjob import kill_tree
        kill_tree(proc.pid)
    except Exception:
        pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


class Browser:
    """Headless Chrome or Edge on a throwaway profile, removed on close."""

    def __init__(self, executable, width=WIDTH, height=HEIGHT):
        self.executable = executable
        self.width, self.height = width, height
        self.profile = tempfile.mkdtemp(prefix="reverse-engineer-cdp-")
        self.port = free_port()
        self.proc = None
        self.sessions = []

    def start(self):
        self.proc = subprocess.Popen(
            [self.executable, "--headless=new", f"--remote-debugging-port={self.port}",
             f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check",
             "--disable-extensions", "--disable-background-networking", "--disable-sync",
             "--disable-component-update", "--metrics-recording-only", "--no-proxy-server",
             f"--window-size={self.width},{self.height}", "about:blank"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return self

    def page(self):
        """A Session on the browser's first page, sized to the window."""
        session = Session(page_url(self.port))
        self.sessions.append(session)
        session.call("Emulation.setDeviceMetricsOverride",
                     {"width": self.width, "height": self.height, "deviceScaleFactor": 1, "mobile": False})
        return session

    def close(self):
        for session in self.sessions:
            session.close()
        self.sessions = []
        if self.proc is not None:
            _kill(self.proc)
            self.proc = None
        for _ in range(10):
            shutil.rmtree(self.profile, ignore_errors=True)
            if not os.path.exists(self.profile):
                break
            time.sleep(0.3)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()
