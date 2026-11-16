"""The REA engine: one `rea mcp` subprocess, driven as an MCP client over stdio.

REA (github.com/morluto/rea, the `rea-agents` package) is a reverse-engineering MCP
server. The studio runs one engine at a time, for the open subject — `node scripts/rea.mjs
mcp` in the cloned checkout, with the machine's Node 24 (through fnm) — and speaks MCP
JSON-RPC to it over the process's stdin/stdout (newline-delimited, one compact JSON
object per line, the MCP stdio transport).

Tool calls are serialised: REA holds one opened target that "persists across turns", so a
call completes before the next starts. The studio is the engine's only client; it both
answers the Ask Claude panel's `/mcp` (forwarding Claude's JSON-RPC) and runs tools the
Tools page asks for directly. Either way the same engine, the same session, and the studio
sees every call — which is what the live panels draw from.

Every request REA sees carries an id the studio chose, never the client's own: Claude numbers
its requests from 0 and so does nothing else here, so a forwarded id is swapped for a studio
id on the way in and back on the way out. Cancelling (Stop, Cancel, a timeout, or Claude's own
notifications/cancelled) tells REA with notifications/cancelled and frees the waiter at once:
REA's SDK sends no reply for a request it aborted.

Nothing here reaches the network. A stop or a crash kills the whole process tree.
"""
import collections
import json
import os
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass, field

from . import config
from .winjob import CREATE_NO_WINDOW, Job, kill_tree

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "reverse-engineer-studio", "version": "1.0.0"}
HANDSHAKE_TIMEOUT = 60
# REA writes a lot to stderr (provider banners, pino logs). The reason it died comes last, so
# keep a rolling tail of about this many characters, each line cut to STDERR_LINE.
STDERR_KEEP = 20000
STDERR_LINE = 2000
# After a cancel, how long REA has to answer with its own typed `cancelled` result (which may
# carry partial observations) before the studio answers for it.
CANCEL_GRACE = 0.5
LOCK_POLL = 0.1

# Never handed to REA, nor through it to what it runs (debuggers, Frida, the analysed program).
# REA's own REA_* variables, and whatever the studio adds (the active backends), always pass.
SCRUB_PREFIXES = ("ANTHROPIC_", "CLAUDE", "MCP_", "AWS_", "OPENAI_")
SCRUB_WORDS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "APIKEY", "ACCESS_KEY",
               "PRIVATE_KEY", "CREDENTIAL")


class EngineError(RuntimeError):
    """The engine could not start, or a call could not be completed."""


class EngineBusy(EngineError):
    """Another subject's engine is busy, so it was not swapped out."""


def scrubbed_env(base=None, extra=None):
    """The environment REA starts with: this process's, minus sign-ins and secrets, plus `extra`."""
    env = {}
    for name, value in (os.environ if base is None else base).items():
        upper = name.upper()
        secret = upper.startswith(SCRUB_PREFIXES) or any(word in upper for word in SCRUB_WORDS)
        if upper.startswith("REA_") or not secret:
            env[name] = value
    env.update(extra or {})
    return env


def rpc_error(message_id, code, message):
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def _good_id(value):
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def classify(message):
    """What a client sent: ("request" | "notification" | "response", None), or ("invalid", the
    JSON-RPC error to answer with). A batch is refused; MCP over HTTP sends one message at a time."""
    if isinstance(message, list):
        return "invalid", rpc_error(None, -32600, "Batches are not supported. Send one message at a time.")
    if not isinstance(message, dict):
        return "invalid", rpc_error(None, -32600, "Invalid request.")
    has_id = "id" in message
    if has_id and not _good_id(message["id"]):
        return "invalid", rpc_error(None, -32600, "Invalid request id.")
    message_id = message["id"] if has_id else None
    if message.get("jsonrpc") != "2.0":
        return "invalid", rpc_error(message_id, -32600, "Invalid request: jsonrpc must be \"2.0\".")
    if "method" not in message:
        if has_id and ("result" in message or "error" in message):
            return "response", None
        return "invalid", rpc_error(message_id, -32600, "Invalid request: no method.")
    if not isinstance(message["method"], str) or not message["method"]:
        return "invalid", rpc_error(message_id, -32600, "Invalid request: method must be a string.")
    if "params" in message and not isinstance(message["params"], dict):
        return "invalid", rpc_error(message_id, -32602, "Invalid params: params must be an object.")
    return ("request" if has_id else "notification"), None


def _left(end):
    """Seconds left until the monotonic deadline `end` (at least a moment, so a reply already
    on its way is still read)."""
    return max(0.5, end - time.monotonic())


def cancelled_result(reason=""):
    """A tools/call result in REA's error contract, for a call the studio cancelled."""
    error = {"code": "cancelled", "category": "cancelled", "message": reason or "Cancelled.", "retryable": True}
    return {"content": [{"type": "text", "text": json.dumps({"error": error})}], "isError": True}


@dataclass(eq=False)
class _Call:
    """One request to REA, from the moment it is asked for (it may first wait for the call lock)."""
    id: int
    method: str
    name: str = ""
    run_id: str | None = None
    client_id: object = None          # the forwarding client's own id; None for the studio's requests
    token: object = None              # the progress token REA will report under
    progress: object = None           # callable(dict) for direct calls
    started: float = field(default_factory=time.monotonic)
    event: threading.Event = field(default_factory=threading.Event)
    reply: dict | None = None
    sent: bool = False                # about to be written, or written
    written: bool = False
    cancelled: bool = False
    reason: str = ""
    last: dict | None = None          # the latest progress payload


class ReaEngine:
    """One REA subprocess and the MCP conversation with it. Thread-safe."""

    def __init__(self, on_event=None, log=None, command=None):
        self._on_event = on_event or (lambda *_: None)  # (kind, payload): "engine", "progress", "notification"
        self._log = log or (lambda *_: None)
        self._command = command   # tests run a stand-in engine through the same code
        self.session = ""         # a new id at each start, so findings can say which engine run made them
        self._proc = None
        self._job = None
        self._next_id = 1
        self._inflight = {}       # studio id -> _Call, queued or sent
        self._tokens = {}         # progress token -> _Call
        self._send_lock = threading.Lock()   # one writer to stdin
        self._call_lock = threading.Lock()   # one REA tool call in flight (REA is single-target)
        self._state_lock = threading.Lock()  # _inflight, _tokens, the process handles
        self._id_lock = threading.Lock()
        self._stderr_lock = threading.Lock()
        self._initialize_result = None
        self._stderr = collections.deque()
        self._stderr_size = 0
        self._stderr_thread = None
        self._alive = False
        self._closing = False
        self._exit_error = ""

    # ---- lifecycle ----

    def start(self, env_extra=None):
        """Spawn `rea mcp` and run the MCP initialize handshake. Raises EngineError if it can't."""
        if self._command:
            cmd = list(self._command)
        else:
            entry = config.rea_entry()
            if entry is None:
                raise EngineError(f"REA is not where it should be. Expected {config.rea_dir()}\\scripts\\rea.mjs. "
                                  "Set the checkout in Settings, or clone github.com/morluto/rea there.")
            if not config.rea_built():
                raise EngineError("REA is cloned but not built. In the checkout run `npm ci` then "
                                  "`npm run build:cached` (Node 24), then try again.")
            node, _ = config.node_command()
            if not node:
                raise EngineError(f"No suitable Node was found. REA needs Node {config.NODE_REQUIREMENT}. Install "
                                  f"it (fnm install {config.NODE_MAJOR}), or give its path in Settings.")
            cmd = list(node) + [str(entry), "mcp"]
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(config.rea_dir()), env=scrubbed_env(extra=env_extra), text=True, encoding="utf-8",
                errors="replace", bufsize=1, creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError as exc:
            raise EngineError(f"REA did not start: {exc}") from None
        job = Job()
        job.assign(proc.pid)
        with self._state_lock:
            self._proc, self._job = proc, job
            self._alive = True
            self._closing = False
            self._exit_error = ""
        self.session = secrets.token_hex(6)
        self._stderr_thread = threading.Thread(target=self._read_stderr, args=(proc,), name="rea-stderr", daemon=True)
        self._stderr_thread.start()
        threading.Thread(target=self._read_stdout, args=(proc,), name="rea-stdout", daemon=True).start()
        try:
            self._initialize_result = self._handshake()
        except EngineError:
            self.close()
            raise
        self._emit("engine", {"state": "running", "session": self.session})
        return self._initialize_result

    def _handshake(self):
        reply = self.request({
            "jsonrpc": "2.0", "method": "initialize",
            "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
        }, timeout=HANDSHAKE_TIMEOUT)
        if not isinstance(reply.get("result"), dict):
            raise EngineError("REA did not answer the MCP handshake. " + config.doctor_hint())
        self.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return reply["result"]

    def alive(self):
        proc = self._proc
        return self._alive and proc is not None and proc.poll() is None

    def last_error(self):
        """Why the engine stopped on its own ("" while it runs, or after a deliberate close)."""
        return self._exit_error

    def close(self):
        with self._state_lock:
            self._closing = True
            self._alive = False
            proc, job = self._proc, self._job
            self._proc = self._job = None
            waiting = list(self._inflight.values())
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
        if proc:
            with self._send_lock:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
        for call in waiting:   # nothing more will answer
            call.event.set()

    # ---- the MCP conversation ----

    def _take_id(self):
        with self._id_lock:
            value = self._next_id
            self._next_id += 1
            return value

    def _emit(self, kind, payload):
        try:
            self._on_event(kind, payload)
        except Exception:
            pass  # a listener's fault never stops the engine's readers

    def _write(self, message):
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._send_lock:
            proc = self._proc
            if not proc or proc.poll() is not None:
                raise EngineError(self._gone_message("REA is not running."))
            try:
                proc.stdin.write(line)
                proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise EngineError(f"REA stopped accepting input: {exc}") from None

    def notify(self, message):
        """Send a JSON-RPC notification (no id, no reply)."""
        self._write(message)

    def _register(self, call):
        with self._state_lock:
            self._inflight[call.id] = call
            if call.token is not None:
                self._tokens[call.token] = call
        return call

    def _release(self, call):
        with self._state_lock:
            self._inflight.pop(call.id, None)
            if call.token is not None and self._tokens.get(call.token) is call:
                del self._tokens[call.token]

    def _gone_message(self, fallback="REA stopped before it answered."):
        if self._closing:
            return "The engine was closed before REA answered." if fallback.startswith("REA stopped") else fallback
        return self._exit_error or (fallback + self._stderr_tail())

    def _cancelled_reply(self, call):
        if call.method == "tools/call":
            return {"jsonrpc": "2.0", "id": call.id, "result": cancelled_result(call.reason)}
        return rpc_error(call.id, -32800, call.reason or "Cancelled.")

    def _exchange(self, call, message, timeout):
        """Write `message` under the call's studio id and wait up to `timeout` s for REA's reply
        (or the cancel)."""
        message = dict(message, id=call.id)
        with self._state_lock:
            if call.cancelled:
                return self._cancelled_reply(call)
            call.sent = True
        self._write(message)
        with self._state_lock:
            call.written = True
            late_cancel = call.cancelled
        if late_cancel:   # cancelled between the decision to send and the write
            self._send_cancel(call)
        if not call.event.wait(timeout):
            if call.method != "initialize":   # MCP never cancels initialize
                with self._state_lock:
                    call.cancelled = True
                    call.reason = "Timed out in the studio."
                self._send_cancel(call)
            raise EngineError(f"REA did not answer in {int(round(time.monotonic() - call.started))} s, so the "
                              "studio cancelled the call.")
        if call.reply is not None:
            return call.reply
        if call.cancelled:
            return self._cancelled_reply(call)
        raise EngineError(self._gone_message())

    def _send_cancel(self, call):
        try:
            self.notify({"jsonrpc": "2.0", "method": "notifications/cancelled",
                         "params": {"requestId": call.id, "reason": call.reason or "Cancelled."}})
        except EngineError:
            pass

    def _acquire(self, call, end):
        """Wait for the call lock until the monotonic deadline `end`. False when the call was
        cancelled while it waited."""
        while not self._call_lock.acquire(timeout=LOCK_POLL):
            if call.cancelled:
                return False
            if not self.alive():
                raise EngineError(self._gone_message("REA is not running."))
            if time.monotonic() >= end:
                raise EngineError("REA is still finishing another call. Try again when it is done.")
        if call.cancelled:
            self._call_lock.release()
            return False
        return True

    def request(self, message, timeout=None):
        """Send one of the studio's own requests (initialize, prompts/get, completion/complete, …)
        and return REA's whole JSON-RPC reply, `error` included. Any id in `message` is replaced
        by a studio id on the wire and put back in the reply."""
        timeout = timeout or config.ENGINE_TIMEOUT  # noqa: F821 (set by config.apply)
        method = message.get("method")
        if not isinstance(method, str) or not method:
            raise EngineError("A request needs a method.")
        call = self._register(_Call(id=self._take_id(), method=method))
        try:
            reply = self._exchange(call, {k: v for k, v in message.items() if k != "id"}, timeout)
        finally:
            self._release(call)
        return dict(reply, id=message.get("id", call.id))

    def forward(self, message, timeout=None, on_call=None):
        """Pass one client JSON-RPC message (Claude's, via /mcp) to REA. Returns the reply for a
        request, under the client's own id; None for a notification or a client's response. A
        malformed message gets its JSON-RPC error back. Tool calls queue behind any other tool
        call; `on_call(name, arguments)` runs when a forwarded tool call actually starts."""
        kind, error = classify(message)
        if kind == "invalid":
            return error
        if kind == "response":
            return None   # the studio asked the client nothing; there is nothing to match it to
        method = message["method"]
        if kind == "notification":
            if method == "notifications/cancelled":
                self._cancel_forwarded(message.get("params") or {})
            return None   # anything else (initialized, roots changed, …): REA is set up already
        client_id = message["id"]
        if method == "initialize":
            # One engine, initialised once: hand back its own answer under the client's id.
            return {"jsonrpc": "2.0", "id": client_id, "result": self._initialize_result}
        timeout = timeout or config.ENGINE_TIMEOUT  # noqa: F821
        params = dict(message.get("params") or {})
        call = _Call(id=self._take_id(), method=method, client_id=client_id)
        if method == "tools/call":
            call.name = params.get("name") if isinstance(params.get("name"), str) else ""
            meta = dict(params.get("_meta") or {}) if isinstance(params.get("_meta"), dict) else {}
            if _good_id(meta.get("progressToken")):
                call.token = meta["progressToken"]   # the client's own token, untouched
            else:
                call.token = meta["progressToken"] = f"studio-{call.id}"
            params["_meta"] = meta
        outgoing = {k: v for k, v in message.items() if k != "id"}
        if "params" in message or method == "tools/call":
            outgoing["params"] = params
        self._register(call)
        try:
            if method != "tools/call":
                reply = self._exchange(call, outgoing, timeout)
            else:
                # One deadline for the whole call, queueing included, so Claude's own MCP timeout
                # (engine timeout + headroom, claude.tool_timeout_ms) never runs out first.
                end = call.started + timeout
                if not self._acquire(call, end):
                    reply = self._cancelled_reply(call)
                else:
                    try:
                        if on_call is not None:
                            try:
                                on_call(call.name, params.get("arguments") or {})
                            except Exception:
                                pass
                        reply = self._exchange(call, outgoing, _left(end))
                    finally:
                        self._call_lock.release()
        finally:
            self._release(call)
        return dict(reply, id=client_id)

    def call(self, name, arguments=None, timeout=None, progress=None, run_id=None):
        """Run one REA tool directly (the Tools page, the panels). Returns the MCP result object.
        `progress(payload)` gets each of REA's progress reports; `run_id` lets cancel() find it."""
        timeout = timeout or config.ENGINE_TIMEOUT  # noqa: F821
        call = _Call(id=self._take_id(), method="tools/call", name=name, run_id=run_id, progress=progress)
        call.token = f"studio-{call.id}"
        self._register(call)
        end = call.started + timeout
        try:
            if not self._acquire(call, end):
                return cancelled_result(call.reason)
            try:
                reply = self._exchange(call, {
                    "jsonrpc": "2.0", "method": "tools/call",
                    "params": {"name": name, "arguments": arguments or {}, "_meta": {"progressToken": call.token}},
                }, _left(end))
            finally:
                self._call_lock.release()
        finally:
            self._release(call)
        if "error" in reply:
            raise EngineError((reply["error"] or {}).get("message") or "The tool call failed.")
        return reply.get("result") or {}

    # ---- cancelling ----

    def cancel(self, run_id=None):
        """Cancel one direct run (by its run_id), or every call in flight or queued."""
        with self._state_lock:
            calls = [c for c in self._inflight.values()
                     if c.method != "initialize" and (run_id is None or c.run_id == run_id)]
        self._cancel_calls(calls, "Cancelled in the studio.")

    def cancel_forwarded(self, reason="Stopped in the studio."):
        """Cancel every call forwarded for a client (Claude), queued or sent; the studio's own
        calls (Tools page, panels) are left alone."""
        with self._state_lock:
            calls = [c for c in self._inflight.values() if c.client_id is not None and c.method != "initialize"]
        self._cancel_calls(calls, reason)

    def _cancel_forwarded(self, params):
        request_id = params.get("requestId")
        if not _good_id(request_id):
            return
        with self._state_lock:
            calls = [c for c in self._inflight.values()
                     if c.client_id is not None and type(c.client_id) is type(request_id) and c.client_id == request_id]
        reason = params.get("reason") if isinstance(params.get("reason"), str) else ""
        self._cancel_calls(calls, reason or "Cancelled by Claude.")

    def _cancel_calls(self, calls, reason):
        tell, grace = [], []
        with self._state_lock:
            for call in calls:
                if call.cancelled or call.reply is not None:
                    continue
                call.cancelled = True
                call.reason = reason
                if call.written:
                    tell.append(call)
                if call.sent:
                    grace.append(call)
                else:
                    call.event.set()   # still queued: it never reaches REA
        for call in tell:
            self._send_cancel(call)
        for call in grace:
            timer = threading.Timer(CANCEL_GRACE, call.event.set)
            timer.daemon = True
            timer.start()

    def busy(self):
        """Whether any call is in flight or queued."""
        with self._state_lock:
            return any(c.method != "initialize" for c in self._inflight.values())

    def running(self):
        """The calls in flight or queued: [{run_id, tool, source, seconds, progress}]."""
        with self._state_lock:
            calls = [c for c in self._inflight.values() if c.method == "tools/call"]
        now = time.monotonic()
        return [{"run_id": c.run_id, "tool": c.name, "source": "claude" if c.client_id is not None else "studio",
                 "seconds": int(now - c.started), "progress": c.last} for c in calls]

    # ---- readers ----

    def _read_stdout(self, proc):
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue  # a stray non-JSON line: ignore
            if isinstance(message, dict):
                self._dispatch(message)
        proc.stdout.close()
        # End of output: the process is gone (or going). Let stderr finish so the tail is the real end.
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=2)
        try:
            code = proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            code = None
        with self._state_lock:
            self._alive = False
            deliberate = self._closing
            if not deliberate:
                said = self._stderr_tail()
                self._exit_error = (f"REA stopped unexpectedly (exit code {code})." if code is not None
                                    else "REA stopped unexpectedly.") + said
            waiting = list(self._inflight.values())
        for call in waiting:
            call.event.set()
        if deliberate:
            self._emit("engine", {"state": "stopped"})
        else:
            self._emit("engine", {"state": "crashed", "error": self._exit_error, "exit_code": code})

    def _dispatch(self, message):
        message_id = message.get("id")
        method = message.get("method")
        if method is None and _good_id(message_id) and ("result" in message or "error" in message):
            with self._state_lock:
                call = self._inflight.get(message_id)
                if call is not None and call.sent and call.reply is None:
                    call.reply = message
                    call.event.set()
            return  # a late answer to a call already given up on is dropped
        if not isinstance(method, str):
            return
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "notifications/progress":
            self._progress(params)
        elif method.startswith("notifications/"):
            self._emit("notification", {"method": method, "params": params})
        elif message_id is not None:
            # A server->client request. Answer ping; we expose no other client capability.
            reply = ({"jsonrpc": "2.0", "id": message_id, "result": {}} if method == "ping"
                     else rpc_error(message_id, -32601, "No such capability."))
            try:
                self._write(reply)
            except EngineError:
                pass

    def _progress(self, params):
        token = params.get("progressToken")
        with self._state_lock:
            call = self._tokens.get(token) if _good_id(token) else None
        payload = {"progress": params.get("progress"), "total": params.get("total"),
                   "message": params.get("message") if isinstance(params.get("message"), str) else ""}
        if call is not None:
            payload["tool"] = call.name
            payload["source"] = "claude" if call.client_id is not None else "studio"
            if call.run_id is not None:
                payload["run_id"] = call.run_id
            call.last = dict(payload)
            if call.progress is not None:
                try:
                    call.progress(dict(payload))
                except Exception:
                    pass
        self._emit("progress", payload)

    def _read_stderr(self, proc):
        for line in proc.stderr:
            line = line.rstrip("\r\n")[:STDERR_LINE]
            with self._stderr_lock:
                self._stderr.append(line)
                self._stderr_size += len(line) + 1
                while self._stderr_size > STDERR_KEEP and len(self._stderr) > 1:
                    self._stderr_size -= len(self._stderr.popleft()) + 1
        proc.stderr.close()

    def stderr_text(self):
        """The end of what REA wrote to stderr (about the last STDERR_KEEP characters)."""
        with self._stderr_lock:
            return "\n".join(self._stderr)

    def _stderr_tail(self, lines=3):
        with self._stderr_lock:
            kept = [line.strip() for line in self._stderr if line.strip()]
        tail = kept[-lines:]
        return (" REA said: " + " / ".join(tail)) if tail else ""


class Engines:
    """Runs the REA engine for the active subject. One at a time: REA holds a single target, so
    opening another subject stops the old engine and starts the new one — unless the old one is
    busy (a call in flight, still starting, or `busy(slug)` says so), which is refused instead.

    The lock is only ever held for bookkeeping: starting and stopping processes happen outside
    it, so status() and state_of() answer at once while an engine starts."""

    def __init__(self, on_event=None, log=None, command=None, env_provider=None, busy=None):
        self._on_event = on_event or (lambda *_: None)   # (slug, kind, payload)
        self._log = log or (lambda *_: None)
        self._command = command            # tests inject a stand-in engine command
        self._env_provider = env_provider  # () -> extra env for the engine (the active backends)
        self._busy = busy                  # (slug) -> bool; called under the lock, so it must not wait
        self._lock = threading.Lock()
        self._slug = None
        self._engine = None
        self._starting = None              # an Event while the current engine starts
        self._states = {}                  # slug -> {"state", "error"}

    # ---- reading (never waits on a start) ----

    def _view(self, slug):
        record = dict(self._states.get(slug) or {"state": "stopped", "error": ""})
        engine = self._engine if slug == self._slug else None
        if engine is not None:
            if record["state"] == "running" and not engine.alive():
                record = {"state": "crashed", "error": engine.last_error() or "REA stopped unexpectedly."}
            record["session"] = engine.session
        return record

    def status(self, slug=None):
        """{"slug", "state", "error", "running"} for `slug`, or for the current engine's subject."""
        with self._lock:
            slug = self._slug if slug is None else slug
            view = self._view(slug) if slug else {"state": "stopped", "error": ""}
        return {"slug": slug, **view, "running": view["state"] == "running"}

    def state_of(self, slug):
        """{"state": stopped|starting|running|crashed, "error"} for one subject's brief."""
        with self._lock:
            return self._view(slug)

    def engine_for(self, slug):
        """The running engine for `slug`, or None. Never starts one."""
        with self._lock:
            engine = self._engine
            if self._slug == slug and engine is not None and self._starting is None and engine.alive():
                return engine
            return None

    def running(self, slug):
        """The tool calls in flight for `slug` (see ReaEngine.running)."""
        engine = self.engine_for(slug)
        return engine.running() if engine else []

    # ---- changing ----

    def _set(self, slug, state, error=""):
        self._states[slug] = {"state": state, "error": error}

    def _swap_refusal(self, slug):
        """Why the current engine (another subject's) may not be swapped out now, or ""."""
        if self._starting is not None:
            return "The engine for another subject is still starting. Try again in a moment."
        busy = self._engine is not None and self._engine.alive() and self._engine.busy()
        if not busy and self._busy is not None:
            try:
                busy = bool(self._busy(self._slug))
            except Exception:
                busy = False
        if busy:
            return ("The engine is busy with another subject. Wait for it to finish, cancel it, or close "
                    "that subject's engine, then try again.")
        return ""

    def open(self, slug, env_extra=None):
        """Make `slug`'s engine the running one and return it, starting it if needed. Raises
        EngineError when it can't start, and EngineBusy rather than swap out a busy subject."""
        while True:
            with self._lock:
                engine = self._engine
                if self._slug == slug and engine is not None:
                    if self._starting is not None:
                        waiting = self._starting
                    elif engine.alive():
                        return engine
                    else:
                        waiting = None
                else:
                    waiting = None
                if waiting is None:
                    if self._slug not in (None, slug) and engine is not None:
                        refusal = self._swap_refusal(slug)
                        if refusal:
                            raise EngineBusy(refusal)
                    old, old_slug = engine, self._slug
                    started = threading.Event()
                    new = self._make(slug)
                    self._slug, self._engine, self._starting = slug, new, started
                    self._set(slug, "starting")
                    if old_slug not in (None, slug):
                        self._set(old_slug, "stopped")
                    break
            # Someone else is starting this subject's engine: wait for theirs.
            waiting.wait(HANDSHAKE_TIMEOUT + 30)
            with self._lock:
                engine = self._engine
                if self._slug == slug and engine is not None and self._starting is None and engine.alive():
                    return engine
                if self._slug == slug and self._starting is not None:
                    continue
                raise EngineError(self._states.get(slug, {}).get("error") or "The engine did not start.")

        if old is not None:
            old.close()
            if old_slug != slug:
                self._emit(old_slug, "engine", {"state": "stopped"})
        self._emit(slug, "engine", {"state": "starting"})
        if env_extra is None and self._env_provider:
            try:
                env_extra = self._env_provider()
            except Exception:
                env_extra = None
        try:
            new.start(env_extra)
        except BaseException as exc:
            message = str(exc) if isinstance(exc, EngineError) else f"The engine did not start: {exc}"
            with self._lock:
                mine = self._engine is new
                if mine:
                    self._engine = None
                    self._starting = None
                    self._set(slug, "stopped", message)
            started.set()
            new.close()
            if mine:
                self._emit(slug, "engine", {"state": "stopped", "error": message})
            if isinstance(exc, EngineError):
                raise
            raise EngineError(message) from exc
        with self._lock:
            superseded = self._engine is not new
            if not superseded:
                self._starting = None
                self._set(slug, "running")
        started.set()
        if superseded:
            new.close()
            raise EngineError("The engine was closed while it was starting.")
        self._emit(slug, "engine", {"state": "running", "session": new.session})
        return new

    def _make(self, slug):
        engine = None

        def relay(kind, payload):
            self._relay(slug, engine, kind, payload)

        engine = ReaEngine(on_event=relay, log=self._log, command=self._command)
        return engine

    def _relay(self, slug, engine, kind, payload):
        with self._lock:
            current = self._engine is engine and engine is not None
            if kind == "engine":
                # Start and close report their own transitions; only a crash comes from the engine.
                if not current or self._starting is not None or payload.get("state") != "crashed":
                    return
                self._set(slug, "crashed", payload.get("error") or "REA stopped unexpectedly.")
        if current:
            self._emit(slug, kind, payload)

    def _emit(self, slug, kind, payload):
        try:
            self._on_event(slug, kind, payload)
        except Exception:
            pass

    def close(self, slug=None):
        """Stop `slug`'s engine (or whichever runs). Never refused: Close is the way out of a hang."""
        with self._lock:
            if slug is not None and slug != self._slug:
                if self._states.get(slug, {}).get("state") in ("crashed", "stopped"):
                    self._set(slug, "stopped")   # forget an old error
                return
            engine, closed = self._engine, self._slug
            self._engine = None
            self._slug = None
            self._starting = None   # an open() still starting it sees it superseded
            if closed is not None:
                self._set(closed, "stopped")
        if engine is not None:
            engine.close()
        if closed is not None:
            self._emit(closed, "engine", {"state": "stopped"})

    def cancel(self, slug, run_id=None):
        """Cancel one direct run of `slug`'s engine, or every call it has in flight."""
        with self._lock:
            engine = self._engine if self._slug == slug else None
        if engine is not None:
            engine.cancel(run_id)

    def cancel_forwarded(self, slug):
        """Cancel the calls Claude forwarded to `slug`'s engine; direct runs keep going."""
        with self._lock:
            engine = self._engine if self._slug == slug else None
        if engine is not None:
            engine.cancel_forwarded()

    def shutdown(self):
        self.close()
