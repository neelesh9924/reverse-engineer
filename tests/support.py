"""Shared test setup: a temporary data folder, a stand-in REA checkout (so the catalogue
loads and the engine "is built"), and a small HTTP client that passes the studio's host,
cookie and origin checks. Nothing here runs Node, the real REA or the real Claude.

CATALOG lists every tool tests/fake_rea.py answers, with schemas and MCP annotations taken
from REA's own dist/mcp-catalog.json (trimmed of long descriptions). Where the fake contract
says so, a tool is flagged destructive even though REA itself does not flag it
(start_frida_session), so the confirmation paths have a session tool to exercise.
"""
import contextlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

REA_VERSION = "6.8.0"
UUID_PATTERN = ("^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|"
                "00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$")
SAFE_PATH = {"type": "string", "minLength": 1, "pattern": "^[^\\x00]*$"}


def _hints(read_only=False, destructive=False, idempotent=True, open_world=False):
    return {"readOnlyHint": read_only, "destructiveHint": destructive, "idempotentHint": idempotent,
            "openWorldHint": open_world}


def _tool(name, title, description, properties=None, required=(), **hints):
    schema = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return {"name": name, "title": title, "description": description, "inputSchema": schema,
            "annotations": _hints(**hints)}


_WEB = {"cdp_endpoint": {"type": "string", "minLength": 1},
        "allowed_origins": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "target_id": {"type": "string", "minLength": 1},
        "command_timeout_ms": {"type": "integer", "minimum": 1, "maximum": 2147483647}}
_SEVERITY = {"type": "string", "enum": ["low", "medium", "high", "critical"]}
_STATUS = {"type": "string", "enum": ["open", "investigating", "blocked", "contradicted", "resolved"]}

CATALOG = {
    "version": 1,
    "info": {"name": "rea-agents", "version": REA_VERSION},
    "capabilities": {"completions": {}, "prompts": {"listChanged": True}, "tools": {"listChanged": True}},
    "instructions": "FAKE REA INSTRUCTIONS for the tests.",
    "tools": [
        _tool("open_binary", "Open Binary", "Open a local executable, bundle, archive or database.", {
            "path": {"type": "string", "description": "Absolute local filesystem path for the analysis target."},
            "format": {"type": "string"}, "architecture": {"type": "string"}, "entry_point": {"type": "string"},
            "provider_id": {"type": "string"}, "snapshot_path": {"type": "string"}},
            ("path",), open_world=True),
        _tool("binary_overview", "Binary Overview", "Return the bound document and procedure/string counts."),
        _tool("binary_session", "Binary Session", "Report the active target, provider and tool availability.", {
            "detail": {"type": "string", "enum": ["summary", "complete"]}}, open_world=True),
        _tool("list_strings", "List Strings", "List every provider-reported string.", {
            "document": {"type": "string"}, "address": {"type": "string"},
            "detail": {"type": "string", "enum": ["complete", "summary"]}}),
        _tool("procedure_pseudo_code", "Procedure Pseudo Code", "Decompile one analyzed procedure.", {
            "procedure": {"type": "string"}, "document": {"type": "string"}}, ("procedure",)),
        _tool("read_bytes", "Read Bytes", "Read analyzed bytes from one virtual address.", {
            "address": {"type": "string"}, "length": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
            "document": {"type": "string"}}, ("address",)),
        _tool("get_call_graph", "Get Call Graph", "Traverse callers or callees from one procedure.", {
            "address": {"type": "string"}, "direction": {"type": "string", "enum": ["forward", "backward"]},
            "detail": {"type": "string", "enum": ["complete", "summary"]}}, ("address",)),
        _tool("decompile_all", "Decompile All", "Write pseudocode for every function into a directory.", {
            "output_directory": {"type": "string", "description": "Absolute output directory. The parent must exist."},
            "layout": {"type": "string"}, "resume": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1}}, ("output_directory",), idempotent=False),
        _tool("inspect_kernel_module", "Inspect Kernel Module", "Inspect one local Linux kernel module.", {
            "path": SAFE_PATH, "max_file_bytes": {"type": "integer", "minimum": 64, "maximum": 536870912},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 65536}}, ("path",)),
        _tool("inspect_web_page", "Inspect Web Page", "Passively inspect one page target through CDP.", {
            **_WEB, "observation_ms": {"type": "integer", "minimum": 0},
            "include_console_text": {"type": "boolean"}}, ("cdp_endpoint", "target_id"), open_world=True),
        _tool("capture_web_screenshot", "Capture Web Screenshot", "Capture the visible viewport as a PNG.",
              dict(_WEB), ("cdp_endpoint", "target_id"), open_world=True),
        _tool("list_javascript_runtime_targets", "List Javascript Runtime Targets",
              "List Node or Electron V8 Inspector targets from one loopback endpoint.", {
                  "inspector_endpoint": {"type": "string", "minLength": 1}}, ("inspector_endpoint",), open_world=True),
        _tool("observe_javascript_runtime", "Observe Javascript Runtime",
              "Attach to one Node or Electron V8 Inspector target and observe it.", {
                  "inspector_endpoint": {"type": "string", "minLength": 1}, "target_id": {"type": "string", "minLength": 1},
                  "observation_ms": {"type": "integer", "minimum": 0}}, ("inspector_endpoint", "target_id"),
              open_world=True),
        _tool("inspect_electron_page", "Inspect Electron Page", "Inspect one loopback Electron page target.", {
            "cdp_endpoint": {"type": "string", "minLength": 1}, "target_id": {"type": "string", "minLength": 1}},
            ("cdp_endpoint", "target_id"), open_world=True),
        _tool("inspect_adb_device", "Inspect Adb Device", "Inspect one device's build identity.", {
            "serial": {"type": "string", "minLength": 1, "maxLength": 256}}, open_world=True),
        _tool("install_adb_package", "Install Adb Package", "Install one local APK on the device.", {
            "serial": {"type": "string", "minLength": 1, "maxLength": 256, "pattern": "^[^\\x00\\s\"']+$"},
            "apk_path": SAFE_PATH, "replace": {"type": "boolean"}}, ("serial", "apk_path"),
            destructive=True, idempotent=False, open_world=True),
        _tool("analyze_javascript_application", "Analyze Javascript Application",
              "Reconstruct one local ASAR or JavaScript application.", {
                  "input_path": SAFE_PATH, "source_paths": {"type": "array", "items": SAFE_PATH}},
              ("input_path",), open_world=True),
        _tool("start_frida_session", "Start Frida Session", "Attach Frida to one process or spawn one target.", {
            "mode": {"type": "string", "enum": ["attach", "spawn"]}, "device_id": {"type": "string", "minLength": 1},
            "pid": {"type": "integer", "exclusiveMinimum": 0}, "program": SAFE_PATH,
            "argv": {"type": "array", "items": {"type": "string"}}},
            destructive=True, idempotent=False, open_world=True),
        _tool("frida_session_status", "Frida Session Status", "Read observed Frida session state.", {
            "session_id": {"type": "string"}, "detail": {"type": "string", "enum": ["complete", "summary"]}},
            ("session_id",)),
        _tool("close_frida_session", "Close Frida Session", "Unload scripts and detach REA's Frida session.", {
            "session_id": {"type": "string"}}, ("session_id",), destructive=True, idempotent=False, open_world=True),
        _tool("inspect_sqlite_database", "Inspect Sqlite Database", "Inspect a local SQLite database snapshot.", {
            "path": SAFE_PATH}, ("path",), open_world=True),
        _tool("list_unknowns", "List Unknowns", "List residual-unknown heads.", {
            "status": _STATUS, "severity": _SEVERITY, "domain": {"type": "string", "minLength": 1},
            "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "exclusiveMinimum": 0}},
            read_only=True),
        _tool("record_unknown", "Record Unknown", "Create one residual unknown.", {
            "question": {"type": "string", "minLength": 1}, "severity": _SEVERITY,
            "domain": {"type": "string", "minLength": 1},
            "required_authority": {"anyOf": [{"type": "string", "enum": [
                "shipped-artifact", "controlled-replay", "historical-reference", "external-service",
                "analyst-inference"]}, {"type": "null"}]},
            "required_confidence": {"type": "string", "enum": ["observed", "derived", "inferred"]}},
            ("question", "severity", "domain"), idempotent=False),
        _tool("update_unknown", "Update Unknown", "Append one revision of a residual unknown.", {
            "unknown_id": {"type": "string"}, "expected_revision": {"type": "integer"}, "status": {"type": "string"},
            "severity": {"type": "string"}, "supporting_evidence_ids": {"type": "array", "items": {"type": "string"}},
            "resolution": {"type": ["object", "null"]}}, ("unknown_id", "expected_revision", "status"),
            idempotent=False),
        _tool("verify_unknown_resolution", "Verify Unknown Resolution", "Revalidate one residual unknown.", {
            "unknown_id": {"type": "string"}}, ("unknown_id",), read_only=True),
        _tool("verify_reconstruction", "Verify Reconstruction", "Verify a specification against an Evidence bundle.", {
            "specification": {"type": "object"}}),
        _tool("inspect_analysis_settings", "Inspect Analysis Settings", "Inspect REA's editable runtime limits.", {
            "settings": {"type": "array", "maxItems": 12, "items": {"type": "string", "enum": [
                "ghidra.decompile_timeout_seconds", "jadx.operation_timeout_ms"]}}}, read_only=True),
        _tool("update_analysis_settings", "Update Analysis Settings", "Set or reset runtime defaults.", {
            "changes": {"type": "array", "minItems": 1, "maxItems": 12, "items": {"anyOf": [
                {"type": "object", "properties": {
                    "setting": {"type": "string", "const": "ghidra.decompile_timeout_seconds"},
                    "action": {"type": "string", "const": "set"},
                    "value": {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 2147483}, {"type": "null"}]}},
                 "required": ["setting", "action", "value"], "additionalProperties": False},
                {"type": "object", "properties": {
                    "setting": {"type": "string", "const": "jadx.operation_timeout_ms"},
                    "action": {"type": "string", "const": "set"},
                    "value": {"type": "integer", "minimum": 1, "maximum": 2147483647}},
                 "required": ["setting", "action", "value"], "additionalProperties": False}]}}},
            ("changes",)),
        _tool("export_evidence_bundle", "Export Evidence Bundle", "Write the session's Evidence bundle to a path.", {
            "path": {"type": "string", "description": "Absolute local filesystem path for the exported bundle."},
            "overwrite": {"type": "boolean"}}, ("path",), destructive=True),
        _tool("import_evidence_bundle", "Import Evidence Bundle", "Merge an Evidence bundle from a path.", {
            "path": {"type": "string", "description": "Absolute local filesystem path of the bundle."}}, ("path",)),
    ],
    "prompts": [
        {"name": "investigate_feature", "title": "Investigate a feature",
         "description": "Trace a feature through relevant artifact, symbol, and function evidence.",
         "arguments": [
             {"name": "feature", "description": "Feature, behavior, string, or symbol to investigate", "required": True},
             {"name": "target_path", "description": "Absolute target path; omit when the target is open", "required": False},
             {"name": "procedure", "description": "Known procedure name or address to prioritize", "required": False}]},
        {"name": "trace_crash", "title": "Trace a crash",
         "description": "Correlate a crash symptom with static call and reference evidence.",
         "arguments": [
             {"name": "crash_signal", "description": "Crash message, exception, signal or address", "required": True},
             {"name": "target_path", "description": "Absolute target path; omit when the target is open", "required": False}]},
    ],
}
DESTRUCTIVE = sorted(t["name"] for t in CATALOG["tools"] if t["annotations"]["destructiveHint"])


def fake_rea_checkout(tmp):
    """Make a folder that looks enough like a built rea-agents checkout."""
    root = tmp / "rea"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "dist" / "mcp").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "rea.mjs").write_text("// stand-in\n", encoding="utf-8")
    (root / "dist" / "mcp" / "main.js").write_text("// stand-in\n", encoding="utf-8")
    (root / "dist" / "mcp-catalog.json").write_text(json.dumps(CATALOG), encoding="utf-8")
    (root / "package.json").write_text(json.dumps({"name": "rea-agents", "version": REA_VERSION}), encoding="utf-8")
    return root


def configure(tmp, **values):
    """Point config at a temp data folder and the stand-in checkout, plus any other settings.
    Returns the module."""
    from app import config

    data = tmp / "data"
    data.mkdir(parents=True, exist_ok=True)
    config.DATA_DIR = data
    rea = fake_rea_checkout(tmp)
    (data / "settings.json").write_text(json.dumps({"rea_dir": str(rea), **values}), encoding="utf-8")
    config.load()
    return config


def fake_rea_cmd():
    return [sys.executable, "-I", str(TESTS / "fake_rea.py")]


def fake_claude_prefix():
    return [sys.executable, "-I", str(TESTS / "fake_claude.py")]


def fake_web_launch(browser, port, profile, url):
    """Stand in for Chrome: a tiny CDP server on the port WebTarget chose."""
    import subprocess
    return subprocess.Popen([sys.executable, "-I", str(TESTS / "fake_cdp.py"), str(port), url])


def start_server(tmp, **values):
    """A running studio server wired to the fake engine, fake Claude and fake browser, with the
    studio's own Engines and WebTargets (so busy, progress and cancel are wired as in the app).
    Returns (httpd, Client)."""
    configure(tmp, **values)
    from app import server
    from app.claude import ClaudeRunner

    runner = ClaudeRunner(prefix=fake_claude_prefix())
    httpd = server.make_server("tok-" + "a" * 20, port=0, runner=runner,
                               engine_command=fake_rea_cmd(), web_launch=fake_web_launch)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, Client(httpd.server_address[1], httpd.studio.token)


def stop_server(httpd):
    httpd.studio.close()
    httpd.shutdown()
    httpd.server_close()


@contextlib.contextmanager
def env(**values):
    """Set environment variables (for the fake Claude / fake REA the studio starts) for a block."""
    before = {key: os.environ.get(key) for key in values}
    os.environ.update({key: str(value) for key, value in values.items()})
    try:
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def wait_for(check, timeout=20, step=0.1, message="the condition did not happen in time"):
    """Poll `check()` until it returns something truthy; return that, or raise AssertionError."""
    end = time.time() + timeout
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(step)
    raise AssertionError(message)


class Client:
    """Minimal HTTP client that satisfies the studio's host/cookie/origin checks."""

    def __init__(self, port, token):
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.token = token
        self.cookie = f"studio_session={token}"
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(self, path):
        req = urllib.request.Request(self.base + path, headers={"Cookie": self.cookie})
        try:
            with self._opener.open(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read().decode("utf-8") or "{}")

    def fetch(self, path):
        """(status, headers, raw body) for a signed-in GET."""
        req = urllib.request.Request(self.base + path, headers={"Cookie": self.cookie})
        try:
            with self._opener.open(req, timeout=30) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, dict(e.headers), e.read()

    def post(self, path, body, timeout=60):
        req = urllib.request.Request(self.base + path, method="POST",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Cookie": self.cookie, "Content-Type": "application/json",
                                              "Origin": self.base})
        try:
            with self._opener.open(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read().decode("utf-8") or "{}")

    def raw(self, path, headers=None, method="GET", body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=(json.dumps(body).encode("utf-8") if body is not None else None),
                                     headers=headers or {})
        try:
            with self._opener.open(req, timeout=20) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()

    def state(self):
        return self.get("/api/state")[1]

    def brief(self, slug):
        return next((s for s in self.state()["subjects"] if s["slug"] == slug), None)
