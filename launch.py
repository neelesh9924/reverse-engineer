"""Start the studio: start the server, open an app window, stop once the window is gone.

Run by "Launch Reverse Engineer.vbs" with .venv\\Scripts\\pythonw.exe (no console). With
--setup it runs in a visible console instead: the first run, which makes the venv, then
starts the studio as usual. The studio's page is plain HTML, CSS and JavaScript: there is
nothing to build here. (REA, the engine it drives, is built once in its own checkout.)

One studio runs at a time: data\\studio.lock is held for as long as it runs, and a second
launch opens a window on the running one instead. The window's own process is not a reliable
sign that the window is open (Chrome may hand the window to a browser already running on the
same profile and exit), so the studio stops when the window process is gone *and* the page
has not asked for /api/state for QUIET_SECONDS *and* no window with the studio's title is open.

Windows 10/11 only: the launcher, the process-tree handling and the browser discovery are
Windows-specific.
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PY = VENV / "Scripts" / "python.exe"
VENV_PYW = VENV / "Scripts" / "pythonw.exe"
DATA = ROOT / "data"
LOG = DATA / "logs" / "studio.log"
SESSION = DATA / "session.json"
LOCK = DATA / "studio.lock"
PROFILE = DATA / "browser-profile"
APP = "reverse-engineer"
NAME = "Reverse Engineer"
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
QUIET_SECONDS = 15        # no poll from the page for this long (and no window): stop
STARTUP_GRACE = 30        # the page gets this long after the window opens to poll for the first time
JOIN_WAIT = 30            # a second launch waits this long for the running studio to answer


def supported(platform=None):
    return (platform or sys.platform) == "win32"


def setup():
    print(f"{NAME} - setup\n")
    try:
        if sys.version_info < (3, 11):
            raise RuntimeError(f"Python 3.11 or newer is needed; this is {sys.version.split()[0]}.")
        if not VENV_PY.is_file():
            print(f"Creating the virtual environment in {VENV} ...")
            subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        print(f"\nSetup done. Starting {NAME}; this window closes in a few seconds.")
        subprocess.Popen([str(VENV_PYW), str(ROOT / "launch.py")], cwd=str(ROOT),
                         creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, close_fds=True)
        time.sleep(4)
        return 0
    except Exception as exc:
        print(f"\nSetup failed: {exc}")
        input("Press Enter to close this window.")
        return 1


def log(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def quiet_logs():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None or "pythonw" in Path(sys.executable).name.lower():
        sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)


def http_json(url, timeout=2.0):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def port_free(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", port))
        return True
    except (OSError, OverflowError, ValueError, TypeError):
        return False
    finally:
        sock.close()


# ---- one instance ----

class InstanceLock:
    """data\\studio.lock, held (one byte locked) for as long as this studio runs. The OS lets go of
    it when the process ends, however it ends, so a crash never leaves it stuck."""

    def __init__(self, path=LOCK):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self):
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self.handle.close()
            self.handle = None


def running_session(read=None, probe=http_json):
    """The running studio's session ({port, token, instance}) when it answers, else None."""
    try:
        session = json.loads(read() if read else SESSION.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(session, dict) or not session.get("port") or not session.get("token"):
        return None
    info = probe(f"http://127.0.0.1:{session['port']}/health")
    if info and info.get("app") == APP and info.get("instance") == session.get("instance"):
        return session
    return None


def wait_for_running(timeout=JOIN_WAIT, find=running_session, sleep=time.sleep, now=time.monotonic):
    end = now() + timeout
    while True:
        session = find()
        if session or now() >= end:
            return session
        sleep(0.25)


# ---- the window ----

def _user32():
    import ctypes

    return ctypes.WinDLL("user32", use_last_error=True)


def find_window(title, lib=None):
    """A visible top-level Chrome/Edge window titled `title` or "<something> `title`", or None."""
    if os.name != "nt" and lib is None:
        return None
    import ctypes
    from ctypes import wintypes

    user32 = lib or _user32()
    each = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    def look(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if cls.value.startswith("Chrome_WidgetWin") and (buf.value == title or buf.value.endswith(" " + title)):
                found.append(hwnd)
        return True

    user32.EnumWindows(each(look), 0)
    return found[0] if found else None


def to_front(title, timeout=20.0, lib=None):
    """Bring the window titled `title` (or "<subject> · `title`") to the front once it appears."""
    if os.name != "nt" and lib is None:
        return False
    import ctypes

    user32 = lib or _user32()
    current_thread = (lambda: 0) if lib else ctypes.WinDLL("kernel32").GetCurrentThreadId
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        hwnd = find_window(title, user32)
        if hwnd:
            front = user32.GetForegroundWindow()
            if front == hwnd:
                return True
            theirs = user32.GetWindowThreadProcessId(front, None) if front else 0
            mine = current_thread()
            joined = bool(theirs) and theirs != mine and user32.AttachThreadInput(mine, theirs, True)
            try:
                user32.ShowWindow(hwnd, 9)
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                if joined:
                    user32.AttachThreadInput(mine, theirs, False)
            for _ in range(20):
                if user32.GetForegroundWindow() == hwnd:
                    return True
                time.sleep(0.05)
            return False
        time.sleep(0.25)
    return False


def open_window(browser, url):
    if os.name == "nt":
        import ctypes

        ctypes.WinDLL("user32").AllowSetForegroundWindow(-1)
    return subprocess.Popen([browser, f"--app={url}", f"--user-data-dir={PROFILE}", "--window-size=1440,920",
                             "--no-first-run", "--no-default-browser-check"], cwd=str(ROOT))


def page_gone(window_alive, last_seen, opened, now, titled_window, quiet=QUIET_SECONDS, grace=STARTUP_GRACE):
    """Whether the studio should stop: the window process has exited, the page has stopped polling,
    and no window with the studio's title is open. `last_seen` and `opened` are monotonic times
    (last_seen None or older than `opened`: the page has not polled yet)."""
    if window_alive:
        return False
    if last_seen is not None and last_seen >= opened:
        if now - last_seen < quiet:
            return False
    elif now - opened < grace:
        return False
    return not titled_window()


def watch(window, server_thread, studio, opened, titled_window, now=time.monotonic, sleep=time.sleep):
    """Wait until the server stops ("server") or the window and its page are gone ("window")."""
    while True:
        if not server_thread.is_alive():
            return "server"
        seen = getattr(studio, "last_seen", None)
        if page_gone(window.poll() is None, seen if isinstance(seen, (int, float)) else None, opened, now(),
                     titled_window):
            return "window"
        sleep(0.5)


def fail(reasons, browser=""):
    for reason in reasons:
        log(f"cannot start: {reason}")
    if browser:
        import html

        DATA.mkdir(parents=True, exist_ok=True)
        page = DATA / "launch-error.html"
        items = "".join(f"<p>{html.escape(r)}</p>" for r in reasons)
        page.write_text(f"<!doctype html><meta charset='utf-8'><title>{NAME} cannot start</title>"
                        f"<body style='font:15px system-ui;max-width:640px;margin:48px auto;padding:0 24px'>"
                        f"<h1>{NAME} cannot start</h1>{items}<p>See {html.escape(str(LOG))}.</p>", encoding="utf-8")
        subprocess.Popen([browser, f"--app={page.as_uri()}", f"--user-data-dir={PROFILE}",
                          "--no-first-run", "--no-default-browser-check"])
    elif os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, "\n\n".join(reasons), f"{NAME} cannot start", 0x10)
    return 1


def join_running(browser):
    """Another launch holds the lock: open a window on that studio once it answers."""
    session = wait_for_running()
    if not session:
        return fail([f"Another {NAME} is starting or did not close properly, and it does not answer. "
                     "Wait a moment and try again; if that does not help, end pythonw.exe in Task Manager."], browser)
    log("already running; opening another window")
    open_window(browser, f"http://127.0.0.1:{session['port']}/auth?token={session['token']}")
    to_front(NAME)
    return 0


def main():
    if not supported():
        print(f"{NAME} runs on Windows 10/11 only.")
        return 1
    if "--setup" in sys.argv:
        return setup()
    if Path(sys.prefix).resolve() != VENV.resolve():
        return setup()
    quiet_logs()
    sys.path.insert(0, str(ROOT))
    from app import config, server
    from app.winjob import Job, kill_tree

    for problem in config.load():
        log(problem)
    browser = config.browser_path() or ""
    if not browser:
        return fail(["Neither Chrome nor Edge was found. Install one, or give its path as \"browser\" "
                     "in data\\settings.json."])

    lock = InstanceLock()
    if not lock.acquire():
        return join_running(browser)
    try:
        return serve(config, server, browser, Job, kill_tree)
    finally:
        lock.release()


def serve(config, server, browser, Job, kill_tree):
    # The lock is ours, so any session.json is from a studio that has ended.
    try:
        SESSION.unlink()
    except OSError:
        pass
    if config.PORT and not port_free(config.PORT):
        return fail([f"Port {config.PORT} on 127.0.0.1 is in use. Change \"port\" in data\\settings.json, "
                     "or leave it out to use any free port."], browser)

    token = secrets.token_urlsafe(32)
    try:
        httpd = server.make_server(token)
    except (OSError, OverflowError, ValueError) as exc:
        return fail([f"The studio could not listen on 127.0.0.1: {exc}"], browser)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, name="server", daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}"
    info = None
    for _ in range(100):
        info = http_json(base + "/health", timeout=1)
        if (info and info.get("app") == APP) or not thread.is_alive():
            break
        time.sleep(0.2)
    if not info:
        httpd.shutdown()
        httpd.server_close()
        return fail([f"The server did not start on {base}."], browser)
    DATA.mkdir(parents=True, exist_ok=True)
    SESSION.write_text(json.dumps({"port": port, "token": token, "instance": info["instance"],
                                   "pid": os.getpid()}), encoding="utf-8")
    log(f"server up on {base} (pid {os.getpid()}); opening {Path(browser).name}")

    window = open_window(browser, f"{base}/auth?token={token}")
    opened = time.monotonic()
    job = Job()   # a studio that dies takes its window with it, so no stray window outlives it
    job.assign(window.pid)
    threading.Thread(target=lambda: log("window in front" if to_front(NAME) else "window not brought to the front"),
                     name="front", daemon=True).start()
    try:
        why = watch(window, thread, httpd.studio, opened, lambda: find_window(NAME) is not None)
        if why == "server":
            log("server stopped; closing the window")
            kill_tree(window.pid)
        else:
            log("window closed and the page stopped asking; shutting down")
    finally:
        httpd.studio.close()
        httpd.shutdown()
        thread.join(timeout=10)
        httpd.server_close()
        try:
            SESSION.unlink()
        except OSError:
            pass
        job.close()
        log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
