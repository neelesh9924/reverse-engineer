"""Local server for the studio's page, Claude's /mcp bridge, and the Tools API. Listens on
127.0.0.1 only.

Other pages open in the same browser are kept away from it:
- Host must be exactly 127.0.0.1:<port>.
- Every request except /health, /auth and /static needs the per-launch session cookie,
  which /auth sets from the token in the launcher's link.
- Every request that changes something needs Origin (or Referer) on this origin and a
  JSON body.
- Every reply carries a Content-Security-Policy that allows only this origin and forbids
  framing.
/mcp is for the running Claude request alone and takes that run's own bearer token. There the
studio checks every tool call against REA's catalogue and refuses what it cannot check: tools
that change state or start a program (unless Settings allow them), paths outside the subject's
target, its folder and its work folder (outputs: the work folder only), network-share and device
paths, and a local inspector or Electron endpoint the user did not use first. Browser tools are
pinned to the subject's own browser and origin, whatever arguments Claude sends.
"""
import hmac
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime
from http.cookies import SimpleCookie
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import backends, catalog, checks, claude, config, evidence, mcp, settings, subjects, webtarget
from .assistant import Assistant, parse_error
from .claude import ClaudeRunner
from .rea import Engines
from .webtarget import WebTargets

COOKIE = "studio_session"
MAX_BODY = 16 * 1024 * 1024
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
SECURITY_HEADERS = (
    ("Content-Security-Policy", CSP),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".ico": "image/x-icon",
          ".webp": "image/webp", ".png": "image/png", ".woff2": "font/woff2",
          ".json": "application/json; charset=utf-8"}
_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><title>{app}</title>
<style>body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; background: #0d0f13;
color: #b2b9c8; font: 15px/1.55 system-ui, "Segoe UI", sans-serif; }} main {{ max-width: 460px; text-align: center; }}
h1 {{ margin: 0 0 10px; color: #e9ecf2; font-size: 20px; }}</style>
<body><main><h1>{title}</h1><p>{line}</p></main></body></html>"""
DENIED_PAGE = _PAGE.format(app=config.APP_NAME, title="Open it from the shortcut",
                           line="This page only works in the window the studio opens. If you closed it, start the studio again.")

RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RUN_KEEP = 120            # seconds a finished Tools-page run stays listed in /api/state
SOURCES = ("tools", "panel")
WEB_FIELDS = ("cdp_endpoint", "target_id", "allowed_origins")
# Inputs that name a file or folder on this PC, which Claude's calls may only point at the
# subject's own places (claude_path_problem): these, and any name ending in _path, _paths or
# _directory, except the ones that are not paths on this PC (on a device, inside a JEB project,
# on a remote server, or relative to the application root REA was given).
PATH_KEYS = ("path", "output_directory", "application_root", "working_directory", "package_tarball",
             "executable", "local_path", "program")
NOT_LOCAL_PATHS = ("device_path", "unit_path", "parent_unit_path", "expected_server_path", "source_paths")
# Inputs REA writes to; Claude's must be inside the subject's work folder (as must an export_*'s path).
OUTPUT_KEYS = ("output_directory", "snapshot_path")
ENGINE_SETTINGS = ("rea_dir", "node_bin")   # settings that make the running engine a different one
# Inputs that name the analysis target itself, filled from a file subject by /api/tool/defaults.
INPUT_PATHS = ("path", "input_path", "target_path", "binary_path", "capture_path", "left_target_path",
               "application_path", "executable_path")
# Tools whose `path` is not the target: an import source, a script, or (export_*) an output.
_PATH_NOT_TARGET = ("import_", "export_", "load_frida_script")
SESSION_STARTS = {"start_frida_session": "frida", "start_gdb_session": "gdb", "start_rizin_debug_session": "rizin"}
SESSION_CLOSES = {"close_frida_session": "frida", "close_gdb_session": "gdb", "close_rizin_debug_session": "rizin"}
_BASE64 = re.compile(r"^[A-Za-z0-9+/=\s]+$")
REPORT_RESULT_MAX = 40000  # characters of one finding's result in the Markdown report
SOURCE_NAMES = {"claude": "Claude", "tools": "Tools page", "panel": "Subject page"}


def _same(a, b):
    return bool(a) and hmac.compare_digest(a.encode(), b.encode())


def parts_of(relative):
    """A path asked for inside a folder, as its parts; None for one that could leave it. Checked as
    text before the filesystem is touched (resolving //host/share would make Windows connect out)."""
    text = unquote(relative)
    if text.startswith("/") or any(c in text for c in "\\:\0"):
        return None
    parts = text.split("/") if text else []
    if parts and parts[-1] == "":
        parts = parts[:-1]
    if any(not part or part.startswith(".") for part in parts):
        return None
    return parts


def log(message):
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def _props(tool):
    schema = tool.get("inputSchema") if isinstance(tool, dict) else None
    props = schema.get("properties") if isinstance(schema, dict) else None
    return props if isinstance(props, dict) else {}


def _is_path_key(key):
    return key not in NOT_LOCAL_PATHS and (key in PATH_KEYS or key.endswith(("_path", "_paths", "_directory")))


def paths_in(name, value, depth=0, out=None):
    """(input name, path, is_output) for every local path a tool call names, nested inputs included."""
    out = [] if out is None else out
    if depth > 8:
        return out
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and _is_path_key(key):
                output = key in OUTPUT_KEYS or (key == "path" and name.startswith("export_"))
                for text in item if isinstance(item, list) else [item]:
                    if isinstance(text, str):
                        out.append((key, text, output))
            if isinstance(item, (dict, list)):
                paths_in(name, item, depth + 1, out)
    elif isinstance(value, list):
        for item in value:
            paths_in(name, item, depth + 1, out)
    return out


def _inside(path, root):
    return path == root or path.is_relative_to(root)


def claude_path_problem(name, arguments, record, slug):
    """Why one of Claude's REA calls names a path it may not, or "". Every path must be a full
    path on this PC (never a network share or a device, checked as text first). An output must be
    inside the subject's work folder; an input there, or the target itself, or the folder Claude
    may read for it (claude.readable_places), or under a folder target."""
    found = paths_in(name, arguments)
    if not found:
        return ""
    work = subjects.work_dir(slug).resolve()
    roots = [work]
    target = (record or {}).get("target") or {}
    where = "this subject's work folder"
    path = target.get("path") or ""
    if path and not config.local_path_problem(path):
        roots.append(Path(path).resolve())
        folder, _ = claude.readable_places(path, bool(target.get("folder")))
        if folder:
            roots.append(Path(folder).resolve())
            where = f"the target, the folder that holds it ({folder}) and this subject's work folder"
        else:
            where = "the target and this subject's work folder"
    for key, text, output in found:
        problem = config.local_path_problem(text)
        if problem:
            return f"{key}: {problem} The studio passes REA only full paths on this PC."
        try:
            place = Path(text).resolve()
        except (OSError, RuntimeError, ValueError):
            return f"{key}: that path cannot be used."
        if output and not _inside(place, work):
            return f"{key} must be inside this subject's work folder, {work}. Write REA's output there."
        if not output and not any(_inside(place, root) for root in roots):
            return (f"{key} is outside what the studio lets Claude use for this subject: {where} "
                    f"({work}). Ask the user to run the tool from the Tools page if it needs another file.")
    return ""


def _collect(value, key, depth=0, out=None):
    """Every dict under `value` (a few levels deep) that has `key`, outermost first."""
    out = [] if out is None else out
    if depth > 5:
        return out
    if isinstance(value, dict):
        if key in value:
            out.append(value)
        for item in value.values():
            _collect(item, key, depth + 1, out)
    elif isinstance(value, list):
        for item in value:
            _collect(item, key, depth + 1, out)
    return out


def open_sessions(items, current):
    """Debugger and instrumentation sessions still open in engine session `current`, from the
    findings (oldest first): a start_* result's session_id not yet closed by its close_* call."""
    found = {}
    for entry in items:
        if entry.get("error") or (current and entry.get("session") != current):
            continue
        tool = entry.get("tool")
        if tool in SESSION_STARTS:
            holder = next(iter(_collect(entry.get("evidence"), "session_id")), None)
            session_id = holder.get("session_id") if holder else None
            if isinstance(session_id, str) and session_id:
                kind = SESSION_STARTS[tool]
                found[(kind, session_id)] = {"kind": kind, "session_id": session_id, "tool": tool,
                                             "at": entry.get("at", "")}
        elif tool in SESSION_CLOSES:
            found.pop((SESSION_CLOSES[tool], (entry.get("arguments") or {}).get("session_id")), None)
        elif tool == "list_cutter_sessions":
            # Cutter's sessions are discovered, not started here: the latest listing is the truth.
            for key in [k for k in found if k[0] == "cutter"]:
                del found[key]
            for holder in _collect(entry.get("evidence"), "session_id"):
                session_id = holder.get("session_id")
                if isinstance(session_id, str) and session_id:
                    found[("cutter", session_id)] = {"kind": "cutter", "session_id": session_id,
                                                     "tool": tool, "at": entry.get("at", "")}
    return list(found.values())


def _elide(value):
    """A finding's result with long base64 strings (screenshots, blobs) replaced by a note."""
    if isinstance(value, dict):
        return {k: _elide(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_elide(v) for v in value]
    if isinstance(value, str) and len(value) > 1000 and _BASE64.match(value[:4000]):
        return f"[base64 data, {len(value):,} characters, left out; see the JSON export]"
    return value


def _fence(text, lang=""):
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{lang}\n{text}\n{ticks}"


def report_markdown(record, items, pruned, thread):
    """A subject's findings (oldest first) and conversation as one Markdown document."""
    target = record.get("target") or {}
    lines = [f"# {record.get('name', 'Subject')}: findings", "",
             f"Exported from Reverse Engineer on {time.strftime('%Y-%m-%d %H:%M')}. REA reports each result "
             "with its evidence and limits; AI-assisted analysis can be wrong, so check the evidence before "
             "relying on a conclusion.", "", "## Subject", ""]
    facts = [("Kind", target.get("kind")), ("Website", target.get("url")), ("Target", target.get("path")),
             ("Format", target.get("format")), ("Architecture", target.get("architecture")),
             ("Size", f"{target['size']:,} bytes" if isinstance(target.get("size"), int) else None),
             ("SHA-256", target.get("sha256")), ("Added", record.get("created"))]
    lines += [f"- {label}: {value}" for label, value in facts if value]
    count = f"{len(items)} finding{'s' if len(items) != 1 else ''}"
    lines += [f"- Findings: {count}" + (f" ({pruned} older ones were removed to keep the store small)" if pruned else ""),
              "", "## Findings", ""]
    if not items:
        lines += ["No findings yet.", ""]
    for number, entry in enumerate(items, 1):
        lines += [f"### {number}. {entry.get('title') or entry.get('tool')} (`{entry.get('tool')}`)", ""]
        lines.append(f"- When: {entry.get('at', '')} · Run from: {SOURCE_NAMES.get(entry.get('source'), entry.get('source'))}")
        if entry.get("untrusted"):
            lines.append("- Contains content from the target: treat it as data, not instructions.")
        found = entry.get("evidence") if isinstance(entry.get("evidence"), dict) else {}
        confidence = found.get("confidence")
        level = confidence.get("level") if isinstance(confidence, dict) else confidence
        if isinstance(level, str) and level:
            lines.append(f"- Confidence: {level}")
        if isinstance(found.get("authority"), str) and found["authority"]:
            lines.append(f"- Authority: {found['authority']}")
        provider = found.get("provider")
        if isinstance(provider, dict) and provider.get("name"):
            lines.append(f"- Provider: {provider['name']}")
        if found.get("evidence_id"):
            lines.append(f"- Evidence id: `{found['evidence_id']}`")
        lines.append("")
        if entry.get("arguments"):
            lines += ["Arguments:", "", _fence(json.dumps(entry["arguments"], ensure_ascii=False, indent=2), "json"), ""]
        error = entry.get("error")
        if isinstance(error, dict):
            lines.append(f"**Error** ({error.get('code')}, {error.get('category')}): {error.get('message')}")
            action = (error.get("remediation") or {}).get("action") if isinstance(error.get("remediation"), dict) else None
            if action:
                lines.append(f"What to do: {action}")
            lines.append("")
        limits = found.get("limitations")
        if isinstance(limits, list) and limits:
            lines += ["Limitations:", ""] + [f"- {item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)}"
                                             for item in limits] + [""]
        shown = found.get("normalized_result", found) if found else None
        if shown:
            text = json.dumps(_elide(shown), ensure_ascii=False, indent=2)
            if len(text) > REPORT_RESULT_MAX:
                text = text[:REPORT_RESULT_MAX] + "\n… (cut here; the JSON export has all of it)"
            lines += ["Result:", "", _fence(text, "json"), ""]
        elif entry.get("text"):
            lines += ["Result:", "", _fence(entry["text"][:REPORT_RESULT_MAX]), ""]
    said = [m for m in thread if (m.get("text") or "").strip()]
    if said:
        lines += ["## Conversation", ""]
        names = {"user": "You", "claude": "Claude", "error": "Studio error", "info": "Studio note"}
        for message in said:
            lines += [f"**{names.get(message.get('role'), message.get('role'))}** ({message.get('at', '')})", "",
                      message["text"].strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


class Studio:
    """What one running server shares between requests."""

    def __init__(self, token, port, runner=None, engines=None, web=None, engine_command=None, web_launch=None):
        self.token = token
        self.port = port
        self.instance = secrets.token_hex(8)
        self.origin = f"http://127.0.0.1:{port}"
        self.last_seen = time.monotonic()   # the page's last /api/state: the launcher's heartbeat
        self.runner = runner or ClaudeRunner()
        self.engines = engines or Engines(on_event=self._engine_event, log=log, command=engine_command,
                                          env_provider=backends.engine_env, busy=self._busy)
        self.web = web or WebTargets(launch=web_launch, log=log, busy=self._busy)
        self.assistant = Assistant(port, self.runner, self.engines, log, web=self.web,
                                   open_engine=self.open_engine)
        self.checks = {"claude": None, "node": None, "rea": None, "backends": None, "checking": False}
        self.runs = {}                     # run_id -> a Tools-page call: {slug, tool, status, started, …}
        self._runs_lock = threading.Lock()
        self.analysis = {}                 # slug -> {"session", "applied": [names], "error"} after each start
        self._analysis_lock = threading.Lock()
        self._checking = threading.Lock()
        self._checks_thread = None
        self._again = False
        self._live = ("", None)            # (engine session, {tool name: annotations}) from its tools/list
        self._live_lock = threading.Lock()

    # ---- engines ----

    def _engine_event(self, slug, kind, payload):
        payload = payload if isinstance(payload, dict) else {}
        if kind == "progress":
            run_id = payload.get("run_id")
            with self._runs_lock:
                run = self.runs.get(run_id) if run_id else None
                if run and run["slug"] == slug:
                    run["progress"] = {key: payload.get(key) for key in ("progress", "total", "message")}
            if not run and getattr(self, "assistant", None):
                self.assistant.progress(slug, payload)
        elif kind == "engine" and (payload.get("state") == "crashed" or payload.get("error")):
            log(f"engine for {slug} {payload.get('state')}: {payload.get('error') or 'no reason given'}")
        if getattr(self, "assistant", None):
            self.assistant.bump()

    def _busy(self, slug):
        """Whether `slug`'s engine must not be swapped away: Claude or a Tools-page call is using it."""
        return self.assistant.working_on(slug) or self.run_busy(slug)

    def open_engine(self, slug):
        """Open (or reuse) `slug`'s engine. After each fresh start, the user's saved analysis
        settings are applied again, since REA forgets them when it exits."""
        engine = self.engines.open(slug)
        session = getattr(engine, "session", None) or str(id(engine))
        with self._analysis_lock:
            fresh = (self.analysis.get(slug) or {}).get("session") != session
            if fresh:
                self.analysis[slug] = {"session": session, "applied": [], "error": ""}
        if fresh:
            self.apply_analysis_settings(slug, engine)
        return engine

    def apply_analysis_settings(self, slug, engine):
        values = getattr(config, "ANALYSIS_SETTINGS", None)
        if not isinstance(values, dict) or not values or not catalog.has_tool("update_analysis_settings"):
            return
        changes = [{"setting": name, "action": "set", "value": value} for name, value in sorted(values.items())]
        try:
            result = engine.call("update_analysis_settings", {"changes": changes}, timeout=60)
            error = parse_error([b.get("text", "") for b in result.get("content") or [] if isinstance(b, dict)])["message"] \
                if isinstance(result, dict) and result.get("isError") else ""
        except Exception as exc:
            error = str(exc)
        with self._analysis_lock:
            record = self.analysis.setdefault(slug, {"session": getattr(engine, "session", ""), "applied": [], "error": ""})
            record["applied"] = [] if error else [change["setting"] for change in changes]
            record["error"] = error
        if error:
            log(f"analysis settings for {slug} were not applied: {error}")

    # ---- Tools-page runs ----

    def run_busy(self, slug=None):
        """Whether a Tools-page call is running on `slug` (on any subject when slug is None)."""
        with self._runs_lock:
            return any((slug is None or run["slug"] == slug) and run["status"] == "running"
                       for run in self.runs.values())

    def runs_state(self):
        now = time.monotonic()
        with self._runs_lock:
            for run_id in [k for k, run in self.runs.items()
                           if run["status"] != "running" and now - run["ended"] > RUN_KEEP]:
                del self.runs[run_id]
            return {run_id: {"slug": run["slug"], "tool": run["tool"], "status": run["status"],
                             "seconds": int((run["ended"] or now) - run["started"]), "progress": run["progress"]}
                    for run_id, run in self.runs.items()}

    # ---- checks ----

    def check(self):
        self._again = True
        if not self._checking.acquire(blocking=False):
            return
        self.checks["checking"] = True

        def work():
            try:
                while self._again:
                    self._again = False
                    # Each check stands alone: one that fails replaces its old result, never the others'.
                    checks.run_all(self.runner, put=self.checks.__setitem__)
            finally:
                self.checks["checking"] = False
                self._checking.release()
                if self._again:
                    self.check()

        self._checks_thread = threading.Thread(target=work, name="checks", daemon=True)
        self._checks_thread.start()

    # ---- what the page polls ----

    def brief(self, slug, record):
        target = record.get("target") or {}
        website = subjects.is_website(record)
        web = None
        if website:
            known = self.web.state_of(slug) or {}
            info = self.web.info(slug) or {}
            web = {"state": known.get("state") or ("running" if info else "stopped"),
                   "endpoint": info.get("endpoint") or known.get("endpoint") or "",
                   "target_id": info.get("target_id") or known.get("target_id") or "",
                   "allowed_origins": info.get("allowed_origins") or known.get("allowed_origins") or [],
                   "url": target.get("url", ""), "error": known.get("error") or ""}
        engine = self.engines.state_of(slug) or {}
        return {"slug": slug, "name": record.get("name", slug), "kind": target.get("kind", "executable"),
                "path": target.get("path", ""), "url": target.get("url", ""),
                "exists": True if website else bool(subjects.target_exists(slug)),
                "hashing": False if website else bool(subjects.hashing(slug)),
                "engine": {"state": engine.get("state", "stopped"), "error": engine.get("error", "")},
                "web": web, "thread_rev": self.assistant.thread_rev(slug),
                "evidence_rev": evidence.revision(slug), "evidence_count": evidence.count(slug)}

    def state(self):
        self.last_seen = time.monotonic()
        found = []
        for slug in subjects.slugs():
            record = subjects.meta(slug)
            if record:
                found.append(self.brief(slug, record))
        found.sort(key=lambda s: (s["name"].lower(), s["slug"]))
        assistant = self.assistant.state()
        parsed = catalog.load()
        rea = self.checks.get("rea") or {}

        def ready(name):
            return bool((self.checks.get(name) or {}).get("ok"))

        return {"app": config.APP_NAME, "rev": assistant["rev"], "subjects": found, "job": assistant["job"],
                "runs": self.runs_state(), "checks": self.checks,
                "catalog": {"ready": parsed is not None, "count": parsed["count"] if parsed else 0,
                            "version": rea.get("version") or (parsed["version"] if parsed else "")},
                "setup": {"rea_ready": ready("rea"), "claude_ready": ready("claude"), "node_ready": ready("node"),
                          "notice_seen": bool(getattr(config, "NOTICE_SEEN", False))},
                "permissions": {"claude_destructive": bool(getattr(config, "CLAUDE_DESTRUCTIVE", False))}}

    # ---- Claude's calls through /mcp ----

    def learn_tools(self, session, tools):
        """Keep what the running engine itself listed (tools/list), so Claude's calls are checked
        against the process that serves them as well as against the catalogue file."""
        if not session or not isinstance(tools, list):
            return
        listed = {t["name"]: t.get("annotations") if isinstance(t.get("annotations"), dict) else {}
                  for t in tools if isinstance(t, dict) and isinstance(t.get("name"), str)}
        with self._live_lock:
            self._live = (session, listed)

    def _live_tools(self, session):
        with self._live_lock:
            known, listed = self._live
        return listed if session and known == session else None

    def gated(self, name, session=""):
        """Whether Claude needs the user's permission for this tool: the catalogue or the running
        engine marks it destructive, or it starts or attaches to a program."""
        if claude.needs_permission(name):
            return True
        annotations = (self._live_tools(session) or {}).get(name)
        return (isinstance(annotations, dict) and annotations.get("destructiveHint") is True
                and annotations.get("readOnlyHint") is not True)

    def claude_tools(self, tools, session=""):
        """A tools/list reply's tools as Claude may see them: only tools the catalogue knows, and,
        unless Settings allow them, none that change state."""
        if catalog.load() is None or not isinstance(tools, list):
            return []
        allowed = bool(getattr(config, "CLAUDE_DESTRUCTIVE", False))
        return [t for t in tools if isinstance(t, dict) and catalog.has_tool(t.get("name"))
                and (allowed or not self.gated(t.get("name"), session))]

    def _user_endpoints(self, slug):
        """The inspector and Electron endpoints the user ran a tool with for this subject (Tools
        page or panels): the only ones Claude may attach to."""
        try:
            items = evidence.listing(slug, limit=getattr(evidence, "MAX_KEPT", 500))["items"]
        except (evidence.EvidenceError, subjects.SubjectError):
            return set()
        return {value for entry in items if entry.get("source") != "claude"
                for key, value in (entry.get("arguments") or {}).items()
                if key.endswith("_endpoint") and isinstance(value, str)}

    def claude_policy(self, job, session=""):
        """policy(name, arguments) for mcp.handle, for one Claude run. It fails closed: a call it
        cannot check against REA's catalogue (and the running engine's own list) is refused. Tools
        that change state or start a program are refused unless Settings allow them; browser tools
        are pinned to the subject's own browser and origin; a local inspector or Electron endpoint
        must be one the user used first; every path must be one of the subject's places
        (claude_path_problem). A refusal is shown in the run's activity and kept as a finding."""

        def refuse(name, arguments, text, why):
            tool = catalog.tool(name)
            job.add(f"Refused: {tool['title'] if tool else name}", why)
            self.assistant.record(job.slug, name, arguments, mcp.refused_result(text), source="claude")
            raise mcp.Refused(text)

        def policy(name, arguments):
            if catalog.load() is None:
                refuse(name, arguments, "REA's tool catalogue cannot be read (REA may be rebuilding), so the "
                       "studio cannot check this call and runs none. Tell the user to finish REA's build "
                       "(`npm run build:cached`) and ask again.", "REA's catalogue cannot be read")
            tool = catalog.tool(name)
            live = self._live_tools(session)
            if tool is None or (live is not None and name not in live):
                refuse(name, arguments, f"{name} is not in REA's tool catalogue for the running engine, so the "
                       "studio cannot check it and does not run it. Use one of the listed tools.",
                       "not in REA's catalogue")
            if self.gated(name, session) and not getattr(config, "CLAUDE_DESTRUCTIVE", False):
                refuse(name, arguments,
                       f"{tool['title']} can change state or start a program, and the studio does not let "
                       "Claude run such tools. The user can run it from the Tools page, or turn on “Let Claude "
                       "run state-changing tools” in Settings.", "it can change state")
            props = _props(tool)
            record = subjects.meta(job.slug)
            if webtarget.is_browser_tool(name, props):
                if not subjects.is_website(record):
                    refuse(name, arguments, "Web tools run only on a website subject, against the browser the "
                           "studio opens for it. This subject is a file.", "web tools need a website subject")
                try:
                    arguments = webtarget.pin_web_arguments(props, arguments, self.web.info(job.slug), name)
                except Exception as exc:
                    refuse(name, arguments, str(exc), "the site's browser is not running")
            else:
                endpoints = webtarget.local_endpoints(props)
                asked = [arguments.get(key) for key in endpoints if arguments.get(key) is not None]
                if asked and not set(asked) <= self._user_endpoints(job.slug):
                    refuse(name, arguments, f"{tool['title']} attaches to a program on this PC. Claude may use "
                           "only an endpoint the user has already used for this subject: ask the user to run "
                           f"{tool['title']} (or its list tool) with that endpoint on the Tools page first.",
                           "an endpoint the user has not used")
            try:
                problem = claude_path_problem(name, arguments, record, job.slug)
            except subjects.SubjectError as exc:
                problem = str(exc)
            if problem:
                refuse(name, arguments, problem, "a path outside the subject's places")
            return arguments

        return policy

    def close(self):
        self._again = False
        self.assistant.shutdown()
        self.engines.shutdown()
        self.web.shutdown()
        thread = self._checks_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10)   # the checks read REA's files; let them finish first


class Handler(BaseHTTPRequestHandler):
    server_version = "Studio"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    @property
    def studio(self):
        return self.server.studio

    # ---- replies ----

    def _send(self, status, body=b"", content_type="application/json; charset=utf-8", headers=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _html(self, page, status, headers=()):
        self._send(status, page.encode("utf-8"), "text/html; charset=utf-8", headers)

    def _download(self, body, content_type, filename):
        ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
        disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
        self._send(200, body, content_type, (("Content-Disposition", disposition),))

    def _file(self, base, relative):
        parts = parts_of(relative)
        if not parts:
            return self._json({"error": "Not found."}, 404)
        root = base.resolve()
        target = root.joinpath(*parts).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return self._json({"error": "Not found."}, 404)
        ctype = _TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            return None
        if length < 0 or length > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return None

    def _drain(self):
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        if 0 < length <= MAX_BODY:
            self.rfile.read(length)
        elif length:
            self.close_connection = True

    # ---- who may ask ----

    def _host_ok(self):
        return self.headers.get("Host", "") == f"127.0.0.1:{self.studio.port}"

    def _signed_in(self):
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        return COOKIE in jar and _same(jar[COOKIE].value, self.studio.token)

    def _own_origin(self):
        origin = self.headers.get("Origin")
        if origin is not None:
            return origin == self.studio.origin
        referer = urlparse(self.headers.get("Referer", ""))
        return f"{referer.scheme}://{referer.netloc}" == self.studio.origin

    # ---- GET ----

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "Wrong host name."}, 421)
        url = urlparse(self.path)
        query = parse_qs(url.query)

        def arg(name, default=""):
            return query.get(name, [default])[0]

        if url.path == "/health":
            return self._json({"ok": True, "app": config.APP_ID, "instance": self.studio.instance, "pid": os.getpid()})
        if url.path == "/auth":
            if not _same(arg("token"), self.studio.token):
                return self._html(DENIED_PAGE, 403)
            cookie = f"{COOKIE}={self.studio.token}; HttpOnly; SameSite=Strict; Path=/"
            return self._send(303, headers=(("Location", "/"), ("Set-Cookie", cookie)))
        if url.path == "/mcp":
            return self._send(405, headers=(("Allow", "POST"),))
        if url.path.startswith("/static/"):
            return self._file(config.STATIC_DIR, url.path[len("/static/"):])
        if not self._signed_in():
            if url.path.startswith("/api/"):
                return self._json({"error": "No valid session for this launch."}, 401)
            return self._html(DENIED_PAGE, 401)
        studio = self.studio
        slug = arg("slug")
        if url.path == "/":
            page = (config.STATIC_DIR / "index.html").read_text(encoding="utf-8")
            theme = config.THEME if config.THEME in settings.THEMES else "auto"  # noqa: F821 (set by config.apply)
            return self._html(page.replace('data-choice="auto"', f'data-choice="{theme}"', 1), 200)
        if url.path == "/api/state":
            return self._json(studio.state())
        if url.path == "/api/thread":
            try:
                return self._json({"slug": slug, "messages": subjects.thread(slug)})
            except subjects.SubjectError as exc:
                return self._json({"error": str(exc)})
        if url.path == "/api/subject":
            record = subjects.meta(slug)
            return self._json({"error": "That subject is no longer there."} if record is None
                              else {"slug": slug, "subject": record})
        if url.path == "/api/evidence":
            return self._json(self._evidence(slug, arg("q"), arg("tool"), arg("offset", "0"), arg("limit", "200")))
        if url.path == "/api/evidence/report":
            return self._report(slug, arg("format", "md"))
        if url.path == "/api/catalog":
            return self._json(catalog.catalogue())
        if url.path == "/api/tool/defaults":
            return self._json(self._defaults(slug, arg("name")))
        if url.path == "/api/sessions":
            return self._json(self._sessions(slug))
        if url.path == "/api/settings":
            return self._json({"settings": settings.public(), "checks": studio.checks})
        return self._json({"error": "Not found."}, 404)

    def do_DELETE(self):
        if not self._host_ok():
            return self._json({"error": "Wrong host name."}, 421)
        self._drain()
        return self._send(405, headers=(("Allow", "POST" if urlparse(self.path).path == "/mcp" else "GET, POST"),))

    @staticmethod
    def _number(text, default, low, high):
        try:
            value = int(text)
        except (TypeError, ValueError):
            return default
        return max(low, min(high, value))

    def _evidence(self, slug, q, tool, offset, limit):
        if not subjects.exists(slug):
            return {"slug": slug, "total": 0, "items": [], "pruned": 0}
        try:
            listing = evidence.listing(slug, q=q[:200], tool=tool[:200], offset=self._number(offset, 0, 0, 10 ** 6),
                                       limit=self._number(limit, 200, 1, 500))
        except (evidence.EvidenceError, subjects.SubjectError) as exc:
            return {"error": str(exc)}
        return {"slug": slug, "total": listing["total"], "items": listing["items"], "pruned": listing.get("pruned", 0)}

    def _report(self, slug, fmt):
        record = subjects.meta(slug)
        if record is None:
            return self._json({"error": "That subject is no longer there."}, 404)
        try:
            listing = evidence.listing(slug, limit=getattr(evidence, "MAX_KEPT", 500))
        except (evidence.EvidenceError, subjects.SubjectError) as exc:
            return self._json({"error": str(exc)}, 503)
        items = list(reversed(listing["items"]))   # oldest first, as they happened
        try:
            thread = subjects.thread(slug)
        except subjects.SubjectError:
            thread = []
        if fmt == "json":
            payload = {"app": config.APP_NAME, "exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "subject": {"slug": slug, "name": record.get("name"), "target": record.get("target"),
                                   "created": record.get("created")},
                       "pruned": listing.get("pruned", 0), "findings": items, "conversation": thread}
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            return self._download(body, "application/json; charset=utf-8", f"{slug}-findings.json")
        if fmt != "md":
            return self._json({"error": "The report is md or json."}, 400)
        body = report_markdown(record, items, listing.get("pruned", 0), thread).encode("utf-8")
        return self._download(body, "text/markdown; charset=utf-8", f"{slug}-findings.md")

    def _defaults(self, slug, name):
        """Arguments to prefill for one tool on this subject, and notes saying where they came from."""
        record = subjects.meta(slug)
        if record is None:
            return {"error": "That subject is no longer there."}
        tool = catalog.tool(name)
        if tool is None:
            return {"error": "That is not a REA tool."}
        props = _props(tool)
        target = record.get("target") or {}
        arguments, notes = {}, []
        if subjects.is_website(record):
            wanted = [key for key in WEB_FIELDS if key in props] if webtarget.is_browser_tool(name, props) else []
            if wanted:
                info = self.studio.web.info(slug)
                if info:
                    for key in wanted:
                        arguments[key] = info.get(key) if key != "cdp_endpoint" else info.get("endpoint")
                    notes.append(", ".join(wanted) + " come from the browser the studio runs for this site; "
                                 "the studio keeps them pinned to it.")
                else:
                    notes.append("Open the subject to start the site's browser; its DevTools details fill in here.")
        else:
            path = target.get("path") or ""
            filled = []
            if path:
                for key in INPUT_PATHS:
                    if key in props and not (key == "path" and name.startswith(_PATH_NOT_TARGET)):
                        arguments[key] = path
                        filled.append(key)
                if "apk_path" in props and path.lower().endswith(".apk"):
                    arguments["apk_path"] = path
                    filled.append("apk_path")
                for key in ("format", "architecture", "entry_point"):
                    if filled and key in props and target.get(key):
                        arguments[key] = target[key]
                        filled.append(key)
            if filled:
                verb = "comes" if len(filled) == 1 else "come"
                notes.append(f"{', '.join(filled)} {verb} from this subject's target. Change "
                             f"{'it' if len(filled) == 1 else 'them'} if the tool should read a different file.")
        outputs = []
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if "output_directory" in props:
            outputs.append(("output_directory", f"{name}-{stamp}"))
        if "path" in props and name.startswith("export_"):
            outputs.append(("path", f"{name}-{stamp}.json"))
        if "snapshot_path" in props and name == "close_binary":
            outputs.append(("snapshot_path", f"analysis-{stamp}.snapshot"))
        if outputs:
            work = subjects.work_dir(slug)
            for key, leaf in outputs:
                arguments[key] = str(work / leaf)
            notes.append(", ".join(key for key, _ in outputs) + " go in this subject's work folder, with a new "
                         "timestamped name.")
        return {"arguments": arguments, "notes": notes}

    def _sessions(self, slug):
        engine = self.studio.engines.engine_for(slug) if subjects.exists(slug) else None
        if engine is None:
            return {"sessions": []}   # a stopped engine took its debugger sessions with it
        try:
            listing = evidence.listing(slug, limit=getattr(evidence, "MAX_KEPT", 500))
        except (evidence.EvidenceError, subjects.SubjectError) as exc:
            return {"sessions": [], "error": str(exc)}
        return {"sessions": open_sessions(list(reversed(listing["items"])), getattr(engine, "session", ""))}

    # ---- POST ----

    def do_POST(self):
        if not self._host_ok():
            return self._json({"error": "Wrong host name."}, 421)
        path = urlparse(self.path).path
        if path == "/mcp":
            return self._mcp()
        if not self._signed_in():
            self._drain()
            return self._json({"error": "No valid session for this launch."}, 401)
        if not self._own_origin() or "application/json" not in self.headers.get("Content-Type", ""):
            self._drain()
            return self._json({"error": "Request from another page was blocked."}, 403)
        body = self._body()
        if not isinstance(body, dict):
            self.close_connection = True
            return self._json({"error": "Bad request."}, 400)
        studio = self.studio
        assistant = studio.assistant
        slug = body.get("slug") if isinstance(body.get("slug"), str) else ""
        if path == "/api/subjects/new":
            return self._json(self._new_subject(body))
        if path == "/api/ask":
            error = assistant.ask(slug, body.get("text"), prompt=body.get("prompt"))
        elif path == "/api/stop":
            return self._json(self._stop())
        elif path == "/api/clear":
            error = assistant.clear(slug)
        elif path == "/api/evidence/clear":
            return self._json(self._clear_evidence(slug))
        elif path == "/api/tool/run":
            return self._json(self._run_tool(slug, body))
        elif path == "/api/tool/cancel":
            return self._json(self._cancel(slug, body.get("run_id")))
        elif path == "/api/prompt/complete":
            return self._json(self._complete(slug, body))
        elif path == "/api/subject/open":
            return self._json(self._open_subject(slug))
        elif path == "/api/subject/close":
            return self._json(self._close_subject(slug, body.get("what") or "all"))
        elif path == "/api/rename":
            return self._json(self._change(slug, lambda: subjects.rename(slug, body.get("name"))))
        elif path == "/api/remove":
            return self._json(self._remove(slug))
        elif path == "/api/check":
            studio.check()
            return self._json({"ok": True})
        elif path == "/api/settings":
            return self._json(self._settings(body.get("values")))
        else:
            return self._json({"error": "Not found."}, 404)
        return self._json({"error": error} if error else {"ok": True})

    def _new_subject(self, body):
        kind = body.get("kind") if isinstance(body.get("kind"), str) else ""
        raw = body.get("url") if kind == "website" else body.get("path")
        if not kind:
            raw = body.get("path") or body.get("url")
        target = subjects.clean_path(raw) if isinstance(raw, str) else ""
        if not kind:
            kind = subjects.guess_kind(target)
        text = lambda key: body.get(key) if isinstance(body.get(key), str) else ""  # noqa: E731
        try:
            slug = subjects.create(body.get("name"), target, kind, text("format"), text("architecture"),
                                   entry_point=text("entry_point"))
        except subjects.SubjectError as exc:
            return {"error": str(exc), **({"field": exc.field} if exc.field else {})}
        self.studio.assistant.bump()
        return {"ok": True, "slug": slug}

    def _elsewhere(self, slug):
        """Why `slug` cannot use the engine now, or ""."""
        assistant = self.studio.assistant
        if assistant.busy() and not assistant.working_on(slug):
            return "Claude is working on another subject. Wait for it, or press Stop."
        return ""

    def _run_tool(self, slug, body):
        """Run one REA tool directly (the Tools page or a subject panel), in this subject's engine."""
        studio = self.studio
        if not subjects.exists(slug):
            return {"error": "That subject is no longer there."}
        name = body.get("name")
        tool = catalog.tool(name) if isinstance(name, str) else None
        if tool is None:
            return {"error": "That is not a REA tool."}
        arguments = body.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return {"error": "The arguments must be a JSON object."}
        if tool["destructive"] and body.get("confirmed") is not True:
            return {"error": f"{tool['title']} can change state (REA marks it destructive). Confirm to run it.",
                    "needs_confirmation": True}
        run_id = body.get("run_id")
        if run_id is not None and not (isinstance(run_id, str) and RUN_ID.match(run_id)):
            return {"error": "A run id is letters, numbers, - or _."}
        run_id = run_id or secrets.token_hex(8)
        source = body.get("source") if body.get("source") in SOURCES else "tools"
        problem = self._elsewhere(slug)
        if problem:
            return {"error": problem}
        try:
            engine = studio.open_engine(slug) if body.get("start") is not False else studio.engines.engine_for(slug)
        except Exception as exc:
            return {"error": str(exc)}
        if engine is None:
            return {"error": "This subject's engine is not running. Open the subject to start it.", "not_running": True}
        record = subjects.meta(slug)
        props = _props(tool)
        if subjects.is_website(record) and webtarget.is_browser_tool(name, props):
            try:
                info = (studio.web.open(slug, record["target"]["url"]) if body.get("start") is not False
                        else studio.web.info(slug))
            except Exception as exc:
                return {"error": f"The website’s browser could not start: {exc}"}
            if not info:
                return {"error": "The site's browser is not running. Open the subject to start it.", "not_running": True}
            try:
                arguments = webtarget.pin_web_arguments(props, arguments, info, name)
            except Exception as exc:
                return {"error": str(exc)}
        with studio._runs_lock:
            if run_id in studio.runs and studio.runs[run_id]["status"] == "running":
                return {"error": "That run is already going."}
            run = {"slug": slug, "tool": name, "status": "running", "started": time.monotonic(), "ended": 0,
                   "progress": None, "cancelled": False}
            studio.runs[run_id] = run
        studio.assistant.bump()

        def progress(payload):
            if isinstance(payload, dict):
                run["progress"] = {key: payload.get(key) for key in ("progress", "total", "message")}
                studio.assistant.bump()

        try:
            result = engine.call(name, arguments, progress=progress, run_id=run_id)
        except Exception as exc:
            run["status"], run["ended"] = ("cancelled" if run["cancelled"] else "error"), time.monotonic()
            studio.assistant.bump()
            return {"error": "Cancelled." if run["cancelled"] else str(exc)}
        result = result if isinstance(result, dict) else {}
        if result.get("isError"):
            code = parse_error([b.get("text", "") for b in result.get("content") or [] if isinstance(b, dict)])["code"]
            run["status"] = "cancelled" if run["cancelled"] or code == "cancelled" else "error"
        else:
            run["status"] = "done"
        run["ended"] = time.monotonic()
        entry = None
        if body.get("record") is not False:
            entry = studio.assistant.record(slug, name, arguments, result, source=source,
                                            session=getattr(engine, "session", ""))
        studio.assistant.bump()
        return {"ok": True, "result": result, "entry": entry}

    def _cancel(self, slug, run_id):
        studio = self.studio
        if not subjects.exists(slug):
            return {"error": "That subject is no longer there."}
        if run_id is not None and not (isinstance(run_id, str) and RUN_ID.match(run_id)):
            return {"error": "A run id is letters, numbers, - or _."}
        with studio._runs_lock:
            for key, run in studio.runs.items():
                if run["slug"] == slug and run["status"] == "running" and (run_id is None or key == run_id):
                    run["cancelled"] = True
        try:
            studio.engines.cancel(slug, run_id)
        except Exception as exc:
            log(f"cancel for {slug}: {exc}")
        studio.assistant.bump()
        return {"ok": True}

    def _stop(self):
        studio = self.studio
        job = studio.assistant.job
        error = studio.assistant.stop()
        if error:
            return {"error": error}
        # Claude's own REA calls go too, queued or in flight; the user's Tools-page runs keep going.
        if job:
            try:
                studio.engines.cancel_forwarded(job.slug)
            except Exception as exc:
                log(f"cancel for {job.slug}: {exc}")
        return {"ok": True}

    def _clear_evidence(self, slug):
        if not subjects.exists(slug):
            return {"error": "That subject is no longer there."}
        try:
            evidence.clear(slug)
        except (evidence.EvidenceError, OSError) as exc:
            return {"error": f"The findings could not be cleared: {exc}"}
        self.studio.assistant.bump()
        return {"ok": True}

    def _complete(self, slug, body):
        """Suggestions for one argument of a REA workflow, from the running engine (none when it is not)."""
        name, argument, value = body.get("name"), body.get("argument"), body.get("value")
        if not (subjects.exists(slug) and isinstance(name, str) and catalog.prompt(name)
                and isinstance(argument, str) and isinstance(value, str) and len(value) <= 1000):
            return {"values": []}
        engine = self.studio.engines.engine_for(slug)
        if engine is None:
            return {"values": []}
        params = {"ref": {"type": "ref/prompt", "name": name}, "argument": {"name": argument, "value": value}}
        context = body.get("context")
        given = context.get("arguments") if isinstance(context, dict) else None
        if isinstance(given, dict):
            params["context"] = {"arguments": {k: v for k, v in given.items()
                                               if isinstance(k, str) and isinstance(v, str) and v}}
        try:
            reply = engine.request({"jsonrpc": "2.0", "id": f"studio-complete-{secrets.token_hex(4)}",
                                    "method": "completion/complete", "params": params}, timeout=15)
        except Exception:
            return {"values": []}
        found = (((reply or {}).get("result") or {}).get("completion") or {}).get("values") if isinstance(reply, dict) else None
        values = [str(v) for v in found or [] if isinstance(v, (str, int, float)) and not isinstance(v, bool)]
        return {"values": values[:100]}

    def _open_subject(self, slug):
        studio = self.studio
        if not subjects.exists(slug):
            return {"error": "That subject is no longer there."}
        problem = self._elsewhere(slug)
        if problem:
            return {"error": problem}
        try:
            studio.open_engine(slug)
        except Exception as exc:
            return {"error": str(exc)}
        record = subjects.meta(slug)
        if subjects.is_website(record):
            try:
                studio.web.open(slug, record["target"]["url"])
            except Exception as exc:
                return {"error": f"The website’s browser could not start: {exc}"}
        studio.assistant.bump()
        return {"ok": True}

    def _close_subject(self, slug, what):
        studio = self.studio
        if what not in ("engine", "browser", "all"):
            return {"error": "Close the engine, the browser, or all."}
        if what in ("engine", "all"):
            if studio.assistant.working_on(slug):
                return {"error": "Claude is working on this subject. Press Stop first."}
            if studio.run_busy(slug):
                return {"error": "A tool is running on this subject. Cancel it first."}
            studio.engines.close(slug)
        if what in ("browser", "all"):
            studio.web.close(slug)
        studio.assistant.bump()
        return {"ok": True}

    def _change(self, slug, work):
        if not subjects.exists(slug):
            return {"error": "That subject is no longer there."}
        try:
            work()
        except subjects.SubjectError as exc:
            return {"error": str(exc)}
        self.studio.assistant.bump()
        return {"ok": True}

    def _remove(self, slug):
        studio = self.studio
        if studio.assistant.working_on(slug):
            return {"error": "Claude is working on this subject. Wait for it, or press Stop."}
        if studio.run_busy(slug):
            return {"error": "A tool is running on this subject. Cancel it first."}
        studio.engines.close(slug)
        studio.web.close(slug)
        try:
            subjects.remove(slug)   # its findings, work folder and website profile go with it
        except subjects.SubjectError as exc:
            return {"error": str(exc)}
        studio.assistant.touch(slug)
        return {"ok": True}

    def _settings(self, values):
        if not isinstance(values, dict):
            return {"error": "Bad request."}
        studio = self.studio
        before = config.current()
        switching = [key for key in ENGINE_SETTINGS
                     if key in values and subjects.clean_path(values[key]) != before[key]]
        if switching and (studio.assistant.busy() or studio.run_busy()):
            return {"error": "Claude or a tool is using REA now. Wait for it, or stop it, before changing "
                             "REA's folder or Node."}
        changed, problems = settings.save(values)
        if problems:
            return {"problems": problems}
        if set(ENGINE_SETTINGS) & set(changed):
            # The running engine came from the old checkout or Node; the next call starts the new one.
            studio.engines.close()
        if {"claude_bin", "node_bin", "rea_dir"} & set(changed):
            studio.check()
        if "analysis_settings" in changed:
            slug = (studio.engines.status() or {}).get("slug")
            engine = studio.engines.engine_for(slug) if slug else None
            if engine is not None:
                threading.Thread(target=studio.apply_analysis_settings, args=(slug, engine),
                                 name="analysis-settings", daemon=True).start()
        studio.assistant.bump()
        return {"ok": True, "settings": settings.public(), "changed": changed}

    def _mcp(self):
        scheme, _, token = self.headers.get("Authorization", "").partition(" ")
        studio = self.studio
        job = studio.assistant.job_for_token(token) if scheme == "Bearer" else None
        if job is None:
            self._drain()
            return self._json({"error": "No running request for this token."}, 401)
        message = self._body()
        if message is None:
            self.close_connection = True
            return self._json({"error": "Bad request."}, 400)
        method = message.get("method") if isinstance(message, dict) else None
        engine = studio.engines.engine_for(job.slug)
        if engine is None and isinstance(message, dict) and "id" in message and not job.cancel.is_set():
            # The run's engine went (closed, crashed): start it again rather than fail every call.
            try:
                engine = studio.open_engine(job.slug)
            except Exception as exc:
                log(f"engine for Claude's run on {job.slug}: {exc}")
        session = getattr(engine, "session", "") if engine else ""

        def record(name, arguments, result):
            if not job.cancel.is_set():   # a call that ends after Stop belongs to no run
                studio.assistant.record(job.slug, name, arguments, result, source="claude", session=session)

        reply = mcp.handle(message, engine, record=record,
                           timeout=config.ENGINE_TIMEOUT,  # noqa: F821 (set by config.apply)
                           policy=studio.claude_policy(job, session))
        if method == "tools/list" and isinstance(reply, dict) and isinstance(reply.get("result"), dict):
            listed = reply["result"].get("tools")
            studio.learn_tools(session, listed)
            reply["result"]["tools"] = studio.claude_tools(listed, session)
        if reply is None:
            return self._send(202)
        return self._json(reply)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            super().handle_error(request, client_address)


def make_server(token, port=None, runner=None, engines=None, web=None, engine_command=None, web_launch=None):
    """The server on 127.0.0.1, on `port` (0 or None: whatever port is free). The checks start at once.
    engine_command / web_launch stand in for REA and Chrome (tests, the demo) while keeping the
    studio's own Engines and WebTargets, with their busy and progress wiring."""
    httpd = Server(("127.0.0.1", config.PORT if port is None else port), Handler)  # noqa: F821 (set by config.apply)
    httpd.studio = Studio(token, httpd.server_address[1], runner, engines, web, engine_command, web_launch)
    httpd.studio.check()
    return httpd
