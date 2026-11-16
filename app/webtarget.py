"""A website subject's observed browser: one Chrome (or Edge) launched with DevTools open, so
REA's web tools have a `cdp_endpoint` and a page `target_id` to work against.

A website subject is a URL, not a file. When it is opened, the studio starts a Chrome on a
profile of its own (never the user's), pointed at the site, with remote debugging on a private
127.0.0.1 port, and reads the DevTools endpoint and the page target from it. Claude and the
Tools page are then given `cdp_endpoint`, `target_id` and `allowed_origins`, and the web tools
(inspect_web_page, observe_web_session, capture_web_screenshot, analyze_web_bundle, …) work.
pin_web_arguments() holds those tools to this browser and the subject's own origin.

The browser gets no --remote-allow-origins: REA's CDP client (Node `ws`) sends no Origin
header, which Chrome accepts without the flag, while the flag would let any web page — the
observed site included — drive this browser's DevTools.

This is the one place the studio touches the network: observing a website means loading it in a
real browser, exactly as opening it would. It happens only for website subjects.
"""
import http.client
import ipaddress
import json
import socket
import subprocess
import threading
import time
import urllib.request
from urllib.parse import quote, urlsplit, urlunsplit

from . import config
from .winjob import Job, kill_tree

DEFAULT_PORTS = {"http": 80, "https": 443}
WEB_FIELDS = ("cdp_endpoint", "target_id", "allowed_origins")


def is_browser_tool(name, properties):
    """Whether a tool drives a web browser over CDP (inspect_web_*, observe_web_*,
    capture_web_screenshot, analyze_web_bundle, list_browser_targets, discover_webmcp_tools): it
    takes allowed_origins, or a cdp_endpoint and is not one of the Electron tools, which reach an
    Electron app's own loopback DevTools rather than a browser."""
    properties = properties if isinstance(properties, dict) else {}
    if "allowed_origins" in properties:
        return True
    return "cdp_endpoint" in properties and "electron" not in str(name or "")


def local_endpoints(properties):
    """The inputs through which a tool that is not a browser tool attaches to a process on this PC
    (a V8 inspector's inspector_endpoint, an Electron app's cdp_endpoint, any other *_endpoint)."""
    properties = properties if isinstance(properties, dict) else {}
    return [key for key in properties if key.endswith("_endpoint")]
_FETCH_ERRORS = (OSError, ValueError, http.client.HTTPException)
_BAD_HOST_CHARS = set('%<>"\'`{}|\\^[]')


class WebTargetError(RuntimeError):
    pass


def safe_url(text):
    """`text` as a normalised http(s) URL (scheme and host lower-cased, default port dropped), or
    None when it is not one the studio will open: another scheme, a user name or password in it,
    no host, a bad port, or whitespace, control characters or backslashes anywhere."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text or len(text) > 4096 or "\\" in text:
        return None
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS or "@" in parts.netloc or port == 0:
        return None
    host = parts.hostname
    if not host:
        return None
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return None
        host = f"[{host}]"
    elif any(ch in _BAD_HOST_CHARS for ch in host):
        return None
    netloc = host if port is None or port == DEFAULT_PORTS[scheme] else f"{host}:{port}"
    return urlunsplit((scheme, netloc, parts.path, parts.query, parts.fragment))


def allowed_origins(url):
    """[scheme://hostname[:port]] for a safe URL (a real origin: no user info, lower case, no
    default port), else []."""
    safe = safe_url(url)
    if not safe:
        return []
    parts = urlsplit(safe)
    return [f"{parts.scheme}://{parts.netloc}"]


def pin_web_arguments(properties, arguments, info, name=""):
    """A browser tool's arguments, held to the studio's observed browser (SEC-07). For each of the
    tool's own inputs: `cdp_endpoint` and `target_id` become the browser's, and `allowed_origins`
    is what the caller asked for intersected with the subject's origin — the subject's origin
    when that leaves nothing, so it can only narrow. A tool that is not a browser tool
    (is_browser_tool: the V8-inspector and Electron tools) passes unchanged, its own target_id
    included. Raises WebTargetError when the tool needs the browser and it is not running."""
    out = dict(arguments or {})
    properties = properties if isinstance(properties, dict) else {}
    if not is_browser_tool(name, properties):
        return out
    if not info or not info.get("endpoint"):
        raise WebTargetError("The website’s observed browser is not running. Open the subject first.")
    if "cdp_endpoint" in properties:
        out["cdp_endpoint"] = info["endpoint"]
    if "target_id" in properties and "cdp_endpoint" in properties:
        out["target_id"] = info.get("target_id") or ""
    if "allowed_origins" in properties:
        subject = [origin for url in info.get("allowed_origins") or [] for origin in allowed_origins(url)]
        asked = out.get("allowed_origins")
        wanted = ({origin for url in asked if isinstance(url, str) for origin in allowed_origins(url)}
                  if isinstance(asked, list) else set())
        out["allowed_origins"] = [origin for origin in subject if origin in wanted] or subject
    return out


def _free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def _get(url, timeout=2.0, method="GET"):
    """JSON from one of DevTools' HTTP endpoints. Raises one of _FETCH_ERRORS."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, method=method)
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read(2_000_000).decode("utf-8"))


class WebTarget:
    """One observed browser for one website subject. `launch` lets tests stand in for Chrome."""

    def __init__(self, slug, url, launch=None, log=None):
        self.slug = slug
        self.url = url
        self._launch = launch        # (browser, port, profile, url) -> Popen; default launches Chrome
        self._log = log or (lambda *_: None)
        self._proc = None
        self._job = None
        self.port = None
        self.endpoint = ""
        self.target_id = ""

    def start(self, timeout=25.0):
        port = _free_port()
        profile = config.web_profiles_dir() / self.slug
        profile.mkdir(parents=True, exist_ok=True)
        try:
            (profile / "DevToolsActivePort").unlink(missing_ok=True)   # a stale one would mislead the check
        except OSError:
            pass
        try:
            if self._launch:
                proc = self._launch(None, port, str(profile), self.url)
            else:
                browser = config.browser_path()
                if not browser:
                    raise WebTargetError("No Chrome or Edge was found to observe the website. Install one, "
                                         "or set it in Settings.")
                proc = subprocess.Popen(
                    [browser, f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
                     "--no-first-run", "--no-default-browser-check", self.url])
        except OSError as exc:
            raise WebTargetError(f"The browser did not start: {exc}") from None
        self._proc = proc
        try:
            self._job = Job()
            self._job.assign(proc.pid)
            self.port = port
            self.endpoint = f"http://127.0.0.1:{port}"
            self._await_page(proc, profile, timeout)
        except BaseException:
            self.close()   # never leave a browser holding the profile after a failed start
            raise
        return self.info()

    def _await_page(self, proc, profile, timeout):
        end = time.monotonic() + timeout
        last = ""
        while time.monotonic() < end:
            if self._proc is not proc:
                raise WebTargetError("The browser was closed while it was starting.")
            if proc.poll() is not None:
                raise WebTargetError("The browser closed before DevTools was ready. If a browser is still "
                                     "open on this subject's profile, close it and try again.")
            try:
                version = _get(self.endpoint + "/json/version", timeout=1.5)
            except _FETCH_ERRORS as exc:
                last = str(exc)
                time.sleep(0.3)
                continue
            problem = self._not_ours(version, profile)
            if problem:
                raise WebTargetError(problem)
            target = self._page_target()
            if target:
                self.target_id = target
                return
            time.sleep(0.3)
        raise WebTargetError(f"DevTools did not come up on {self.endpoint}." + (f" ({last})" if last else ""))

    def _not_ours(self, version, profile):
        """Why the program answering on our port is not the browser we started, or ""."""
        if not isinstance(version, dict) or not isinstance(version.get("Browser"), str):
            return "Something other than a browser answered on the DevTools port, so the studio stopped."
        socket_url = version.get("webSocketDebuggerUrl")
        if socket_url is not None and not str(socket_url).startswith(f"ws://127.0.0.1:{self.port}/"):
            return "The DevTools endpoint did not name the studio's own port, so the studio stopped."
        try:
            written = (profile / "DevToolsActivePort").read_text(encoding="utf-8").split()
        except (OSError, ValueError):
            written = []
        if written and written[0] != str(self.port):
            return "Another program holds the browser's DevTools port, so the studio stopped."
        return ""

    def _page_target(self):
        try:
            targets = _get(self.endpoint + "/json/list", timeout=2)
        except _FETCH_ERRORS:
            return ""
        if not isinstance(targets, list):
            return ""
        pages = [t for t in targets if isinstance(t, dict) and t.get("type") == "page" and t.get("id")]
        if not pages:
            try:   # current Chrome opens a tab only on PUT, with the URL encoded as the whole query
                made = _get(self.endpoint + "/json/new?" + quote(self.url, safe=""), timeout=5, method="PUT")
            except _FETCH_ERRORS:
                return ""
            return str(made.get("id") or "") if isinstance(made, dict) else ""
        host = urlsplit(self.url).hostname or ""
        for page in pages:   # prefer a tab already on the site
            try:
                page_host = urlsplit(str(page.get("url", ""))).hostname or ""
            except ValueError:
                page_host = ""
            if host and page_host == host:
                return str(page["id"])
        return str(pages[0]["id"])

    def alive(self):
        proc = self._proc
        return proc is not None and proc.poll() is None

    def info(self):
        return {"slug": self.slug, "url": self.url, "running": self.alive(),
                "state": "running" if self.alive() else "stopped", "error": "",
                "endpoint": self.endpoint, "target_id": self.target_id,
                "allowed_origins": allowed_origins(self.url)}

    def close(self):
        proc, job = self._proc, self._job
        self._proc = self._job = None
        if proc and proc.poll() is None:
            if job:
                job.terminate()
            kill_tree(proc.pid)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if job:
            job.close()


class WebTargets:
    """Runs the observed browser for the active website subject. One at a time, like the engine.
    Starting happens outside the lock, so info() and state_of() answer at once meanwhile."""

    def __init__(self, launch=None, log=None, busy=None):
        self._launch = launch
        self._log = log or (lambda *_: None)
        self._busy = busy       # (slug) -> bool: that subject's browser is in use and is not swapped out
        self._lock = threading.Lock()
        self._target = None
        self._starting = None   # an Event while the current target starts
        self._states = {}       # slug -> {"state", "error"}

    def _set(self, slug, state, error=""):
        self._states[slug] = {"state": state, "error": error}

    def open(self, slug, url, timeout=25.0):
        clean = safe_url(url)
        if not clean:
            raise WebTargetError("That website address cannot be opened. Use an http or https address "
                                 "with no user name or password in it.")
        while True:
            with self._lock:
                current = self._target
                waiting = None
                if current is not None and current.slug == slug:
                    if self._starting is not None:
                        waiting = self._starting
                    elif current.alive() and current.url == clean:
                        return current.info()
                if waiting is None:
                    if current is not None and current.slug != slug and self._in_use(current.slug):
                        raise WebTargetError("The browser of another subject is in use. Wait for it to finish, "
                                             "or stop it, then try again.")
                    old = current
                    target = WebTarget(slug, clean, launch=self._launch, log=self._log)
                    started = threading.Event()
                    self._target, self._starting = target, started
                    self._set(slug, "starting")
                    if old is not None and old.slug != slug:
                        self._set(old.slug, "stopped")
                    break
            waiting.wait(timeout + 10)
            with self._lock:
                current = self._target
                if current is not None and current.slug == slug and self._starting is None and current.alive():
                    return current.info()
                if current is not None and current.slug == slug and self._starting is not None:
                    continue
                raise WebTargetError(self._states.get(slug, {}).get("error") or "The browser did not start.")

        if old is not None:
            old.close()
        try:
            target.start(timeout)
        except BaseException as exc:
            message = str(exc) if isinstance(exc, WebTargetError) else f"The browser did not start: {exc}"
            with self._lock:
                if self._target is target:
                    self._target = None
                    self._starting = None
                    self._set(slug, "stopped", message)
            started.set()
            if isinstance(exc, WebTargetError):
                raise
            raise WebTargetError(message) from exc
        with self._lock:
            superseded = self._target is not target
            if not superseded:
                self._starting = None
                self._set(slug, "running")
        started.set()
        if superseded:
            target.close()
            raise WebTargetError("The browser was closed while it was starting.")
        return target.info()

    def _in_use(self, slug):
        if self._busy is None:
            return False
        try:
            return bool(self._busy(slug))
        except Exception:
            return False

    def info(self, slug):
        """The running browser's info for `slug`, or None."""
        with self._lock:
            target = self._target
            if target is not None and target.slug == slug and self._starting is None and target.alive():
                return target.info()
            return None

    def state_of(self, slug):
        """{"state": stopped|starting|running, "error", "endpoint", "target_id", "allowed_origins", "url"}."""
        with self._lock:
            target = self._target if self._target is not None and self._target.slug == slug else None
            record = dict(self._states.get(slug) or {"state": "stopped", "error": ""})
            if target is not None and record["state"] == "running" and not target.alive():
                self._set(slug, "stopped", "The observed browser was closed outside the studio.")
                record = dict(self._states[slug])
            running = target is not None and record["state"] == "running"
        record.update({
            "endpoint": target.endpoint if running else "",
            "target_id": target.target_id if running else "",
            "allowed_origins": allowed_origins(target.url) if target is not None else [],
            "url": target.url if target is not None else "",
        })
        return record

    def close(self, slug=None):
        with self._lock:
            target = self._target
            if target is None or (slug is not None and target.slug != slug):
                if slug is not None and self._states.get(slug, {}).get("state") == "stopped":
                    self._set(slug, "stopped")   # forget an old error
                return
            self._target = None
            self._starting = None
            self._set(target.slug, "stopped")
        target.close()

    def shutdown(self):
        self.close()
