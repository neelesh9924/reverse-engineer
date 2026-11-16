"""A stand-in for `rea mcp`: an MCP server over stdio that answers the handshake, lists tools and
prompts, and returns canned results shaped like REA's (Evidence envelopes, `{"result": …}`
objects, and REA's error contract). It lets the tests and the demo drive the whole studio
without Node or the real REA. Newline-delimited JSON-RPC, as the real engine uses.

Like the real server, it handles requests concurrently: a slow tool call runs on its own thread
while other requests (and notifications/cancelled) are answered. Every tool result carries
`content[0].text` = the JSON of its structured content, as REA's do.

Run as: python fake_rea.py [anything ...]   (extra args, e.g. a path and "mcp", are ignored.)
FAKE_REA_BEHAVIOUR:
  slow           each tool call waits 2 s first
  crash          exit on the first tool call
  noisy-crash    write ~50 KB to stderr, then a last line, then exit (code 3) on the first tool call
  silent-cancel  a cancelled decompile_all stops without replying (as REA's SDK does)
"""
import json
import os
import sys
import threading
import time

BEHAVIOUR = os.environ.get("FAKE_REA_BEHAVIOUR", "")
# The smallest valid PNG: one transparent pixel.
TINY_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
UNTRUSTED = {"provenance": {"untrusted_content": True, "source": "analysis_target"}}

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
_CHANGES = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
_DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}
_WEB = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}


def _tool(name, properties=None, required=None, annotations=None, description=""):
    schema = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return {"name": name, "title": name.replace("_", " ").title(), "description": description or f"Fake {name}.",
            "inputSchema": schema, "annotations": annotations or _READ_ONLY}


_S = {"type": "string"}
_I = {"type": "integer"}
_WEB_PROPS = {"cdp_endpoint": _S, "target_id": _S, "allowed_origins": {"type": "array", "items": _S}}
TOOLS = [
    _tool("open_binary", {"path": _S}, ["path"], _CHANGES, "Open a target."),
    _tool("binary_overview", description="Summarise the open target."),
    _tool("list_strings", {"detail": {"type": "string", "enum": ["complete", "summary"]}},
          description="List strings in the target."),
    _tool("procedure_pseudo_code", {"procedure": _S}, ["procedure"], description="Decompile one procedure."),
    _tool("inspect_web_page", _WEB_PROPS, ["cdp_endpoint", "target_id"], _WEB, "Observe a page over CDP."),
    _tool("capture_web_screenshot", {k: _WEB_PROPS[k] for k in ("cdp_endpoint", "target_id")},
          ["cdp_endpoint", "target_id"], _WEB, "Screenshot a page."),
    _tool("list_javascript_runtime_targets", {"inspector_endpoint": _S}, ["inspector_endpoint"], _WEB,
          "List V8 Inspector targets."),
    _tool("observe_javascript_runtime", {"inspector_endpoint": _S, "target_id": _S},
          ["inspector_endpoint", "target_id"], _WEB, "Observe one V8 Inspector target."),
    _tool("inspect_electron_page", {"cdp_endpoint": _S, "target_id": _S}, ["cdp_endpoint", "target_id"], _WEB,
          "Inspect one Electron page."),
    _tool("read_bytes", {"address": _S, "length": _I}, ["address"], description="Read bytes."),
    _tool("get_call_graph", {"address": _S}, description="A call graph."),
    _tool("decompile_all", {"output_directory": _S}, ["output_directory"], _CHANGES, "Decompile everything (slow)."),
    _tool("inspect_kernel_module", {"path": _S}, ["path"], description="Inspect a kernel module."),
    _tool("install_adb_package", {"path": _S}, ["path"], _DESTRUCTIVE, "Install an APK on a device."),
    _tool("start_frida_session", annotations=_DESTRUCTIVE, description="Instrument a process."),
    _tool("frida_session_status", {"session_id": _S}, ["session_id"]),
    _tool("close_frida_session", {"session_id": _S}, ["session_id"], _CHANGES),
    _tool("list_unknowns", {"status": _S, "severity": _S, "offset": _I, "limit": _I}),
    _tool("record_unknown", {"question": _S, "severity": _S, "domain": _S}, ["question", "severity", "domain"], _CHANGES),
    _tool("update_unknown", {"unknown_id": _S, "expected_revision": _I, "status": _S},
          ["unknown_id", "expected_revision", "status"], _CHANGES),
    _tool("verify_unknown_resolution", {"unknown_id": _S}, ["unknown_id"]),
    _tool("binary_session", {"detail": _S}),
    _tool("inspect_analysis_settings"),
    _tool("update_analysis_settings", {"settings": {"type": "array", "items": {"type": "object"}}}, ["settings"], _CHANGES),
    _tool("export_evidence_bundle", {"path": _S}, ["path"], _CHANGES),
    _tool("import_evidence_bundle", {"path": _S}, ["path"], _CHANGES),
]
PROMPTS = [
    {"name": "investigate_feature", "title": "Investigate a feature",
     "description": "Trace a feature from the surface to the code.",
     "arguments": [{"name": "feature", "description": "The feature to trace.", "required": True}]},
    {"name": "trace_crash", "title": "Trace a crash", "description": "Find where and why a crash happens.",
     "arguments": [{"name": "crash_signal", "description": "The crash signal or message.", "required": True}]},
]

_out = threading.Lock()
_cancels = {}            # request id -> Event
_cancels_lock = threading.Lock()
_state = {"target": None, "frida": 0, "frida_open": set(), "unknowns": {},
          "settings": {"ghidra.decompile_timeout_seconds": 60}}
_state_lock = threading.Lock()


def send(message):
    with _out:
        sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def result_of(structured, meta=None):
    result = {"content": [{"type": "text", "text": json.dumps(structured)}], "structuredContent": structured}
    if meta:
        result["_meta"] = meta
    return result


def error_result(code, category, message, retryable=False, **extra):
    error = {"code": code, "category": category, "message": message, "retryable": retryable, **extra}
    return {"content": [{"type": "text", "text": json.dumps({"error": error})}], "isError": True}


def evidence(name, arguments, normalized=None, meta=None):
    # Echo a few environment variables so tests can see the active backends reach the engine.
    env_echo = {k: os.environ.get(k) for k in ("REA_ADB_PATH", "GHIDRA_INSTALL_DIR", "REA_ANALYSIS_PROVIDER")}
    return {
        "evidence_id": f"ev-{name}",
        "operation": name,
        "predicate_type": "fake.result",
        "provider": {"name": "fake-rea"},
        "parameters": arguments,
        "raw_result": {"echo": arguments, "env": env_echo, "meta": meta or {}},
        "normalized_result": normalized or {"summary": f"ran {name}", "strings": ["hello", "world"]},
        # REA's own vocabulary (src/domain/evidence.ts): what was read straight from the target is
        # observed, what a decompiler or graph walk worked out is derived, a reading is inferred.
        "confidence": CONFIDENCE.get(name, "observed"),
        "authority": "controlled-replay" if "web" in name else "shipped-artifact",
        "limitations": ["This is a stand-in engine for tests."],
    }


CONFIDENCE = {"procedure_pseudo_code": "derived", "decompile_all": "derived", "get_call_graph": "derived",
              "binary_overview": "derived", "verify_unknown_resolution": "inferred"}


def decompile_all(mid, args, meta):
    """Up to `seconds` (default 10) in 0.2 s steps; progress when asked; stops on a cancel."""
    token = meta.get("progressToken")
    steps = max(1, int(float(args.get("seconds", 10)) / 0.2))
    with _cancels_lock:
        cancel = _cancels.setdefault(mid, threading.Event())
    try:
        for step in range(1, steps + 1):
            if cancel.wait(0.2):
                if BEHAVIOUR == "silent-cancel":
                    return None
                return error_result("cancelled", "cancelled", "Cancelled.", True)
            if token is not None:
                send({"jsonrpc": "2.0", "method": "notifications/progress",
                      "params": {"progressToken": token, "progress": step,
                                 "message": f"decompile: {step} of {steps} steps"}})
        return result_of(evidence("decompile_all", args, {"summary": "decompiled", "functions": 3}, meta))
    finally:
        with _cancels_lock:
            _cancels.pop(mid, None)


def tool_result(mid, name, args, meta):
    if name == "decompile_all":
        return decompile_all(mid, args, meta)
    if name == "open_binary":
        with _state_lock:
            _state["target"] = {"path": args.get("path")}
        return result_of({"result": {"path": args.get("path"), "kind": "executable"}})
    if name == "list_strings":
        return result_of(evidence(name, args, meta=meta), UNTRUSTED)
    if name == "procedure_pseudo_code":
        return result_of(evidence(name, args, {"pseudo_code": "int main(void) {\n  return 0;\n}"}, meta), UNTRUSTED)
    if name == "capture_web_screenshot":
        return result_of(evidence(name, args, {"image": TINY_PNG, "mime": "image/png"}, meta))
    if name == "read_bytes":
        length = max(0, min(int(args.get("length") or 64), 4096))
        return result_of(evidence(name, args, {"address": args.get("address"), "bytes": [i % 256 for i in range(length)]}, meta))
    if name == "get_call_graph":
        return result_of(evidence(name, args, {"root": "main", "edges": [["main", "init"], ["main", "run"], ["run", "helper"]]}, meta))
    if name == "inspect_kernel_module":
        return error_result("provider_unavailable", "provider", "No kernel module provider.", False,
                            remediation={"action": "Install the provider."})
    if name == "start_frida_session":
        with _state_lock:
            _state["frida"] += 1
            session = f"frida-{_state['frida']}"
            _state["frida_open"].add(session)
        return result_of({"result": {"session_id": session}})
    if name == "frida_session_status":
        with _state_lock:
            running = args.get("session_id") in _state["frida_open"]
        return result_of({"result": {"session_id": args.get("session_id"), "state": "running" if running else "closed"}})
    if name == "close_frida_session":
        with _state_lock:
            _state["frida_open"].discard(args.get("session_id"))
        return result_of({"result": {"closed": True}})
    if name == "record_unknown":
        with _state_lock:
            unknown_id = f"unk_{len(_state['unknowns']) + 1}"
            _state["unknowns"][unknown_id] = {"unknown_id": unknown_id, "revision": 1, "status": "open",
                                              **{k: v for k, v in args.items() if k != "unknown_id"}}
        return result_of({"result": {"unknown_id": unknown_id, "revision": 1}})
    if name == "list_unknowns":
        with _state_lock:
            items = [dict(u) for u in _state["unknowns"].values()
                     if (not args.get("status") or u.get("status") == args["status"])
                     and (not args.get("severity") or u.get("severity") == args["severity"])]
        offset = max(0, int(args.get("offset") or 0))
        limit = max(1, int(args.get("limit") or 50))
        page = items[offset:offset + limit]
        more = offset + limit < len(items)
        return result_of({"result": {"items": page, "total": len(items), "next_offset": offset + limit if more else None}})
    if name == "update_unknown":
        with _state_lock:
            unknown = _state["unknowns"].get(args.get("unknown_id"))
            if unknown is None:
                return error_result("not_found", "input", "No such unknown.")
            if args.get("expected_revision") != unknown["revision"]:
                return error_result("revision_conflict", "conflict", "The unknown changed since you read it.", True,
                                    details={"current_revision": unknown["revision"]})
            unknown.update({k: v for k, v in args.items() if k not in ("unknown_id", "expected_revision")})
            unknown["revision"] += 1
            revision = unknown["revision"]
        return result_of({"result": {"unknown_id": args.get("unknown_id"), "revision": revision}})
    if name == "verify_unknown_resolution":
        return result_of({"result": {"verdict": "unresolved"}})
    if name == "binary_session":
        with _state_lock:
            target = _state["target"]
        return result_of({"result": {"target": target, "provider": {"name": "fake"},
                                     "tool_availability": {"available": 12, "unavailable": 3},
                                     "cleanup_pending": False}})
    if name == "inspect_analysis_settings":
        with _state_lock:
            value = _state["settings"]["ghidra.decompile_timeout_seconds"]
        return result_of({"result": {"settings": [{"name": "ghidra.decompile_timeout_seconds", "value": value,
                                                   "default": 60, "minimum": 1, "maximum": 3600}]}})
    if name == "update_analysis_settings":
        settings = args.get("settings") or []
        pairs = settings.items() if isinstance(settings, dict) else (
            (s.get("name"), s.get("value")) for s in settings if isinstance(s, dict))
        updated = []
        with _state_lock:
            for key, value in pairs:
                if key:
                    _state["settings"][key] = value
                    updated.append(key)
        return result_of({"result": {"updated": updated}})
    if name == "export_evidence_bundle":
        with open(args.get("path"), "w", encoding="utf-8") as handle:
            json.dump({"bundle": True}, handle)
        return result_of({"result": {"path": args.get("path")}})
    if name == "import_evidence_bundle":
        return result_of({"result": {"imported": 1}})
    if name == "fake_environment":   # unlisted: what the engine's environment holds (names only)
        return result_of({"result": {"names": sorted(os.environ)}})
    return result_of(evidence(name, args, meta=meta))


def run_tool(mid, params):
    if BEHAVIOUR == "slow":
        time.sleep(2)
    name = params.get("name", "")
    args = params.get("arguments") or {}
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    try:
        result = tool_result(mid, name, args, meta)
    except Exception as exc:
        result = error_result("internal", "internal", f"The fake failed: {exc}")
    if result is not None:
        send({"jsonrpc": "2.0", "id": mid, "result": result})


def die(code):
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def handle(message):
    method = message.get("method")
    mid = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2025-06-18", "capabilities": {"tools": {}, "prompts": {}, "completions": {}},
            "serverInfo": {"name": "fake-rea", "version": "0.0.0"}, "instructions": "FAKE REA INSTRUCTIONS"}})
    elif method == "notifications/cancelled":
        with _cancels_lock:
            event = _cancels.setdefault(params.get("requestId"), threading.Event())
        event.set()
    elif method and mid is None:
        return   # other notifications
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "prompts/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"prompts": PROMPTS}})
    elif method == "prompts/get":
        args = params.get("arguments") or {}
        texts = {"investigate_feature": f"Investigate the feature: {args.get('feature', '')}",
                 "trace_crash": f"Trace the crash: {args.get('crash_signal', '')}"}
        if params.get("name") not in texts:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "No such prompt."}})
        else:
            send({"jsonrpc": "2.0", "id": mid, "result": {"messages": [
                {"role": "user", "content": {"type": "text", "text": texts[params["name"]]}}]}})
    elif method == "completion/complete":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "completion": {"values": ["main", "sub_401000"], "total": 2, "hasMore": False}}})
    elif method == "tools/call":
        if BEHAVIOUR == "crash":
            die(1)
        if BEHAVIOUR == "noisy-crash":
            for i in range(500):
                sys.stderr.write(f"[startup noise {i:03d}] " + "x" * 90 + "\n")
            sys.stderr.write("FATAL: the real reason REA stopped\n")
            die(3)
        threading.Thread(target=run_tool, args=(mid, params), daemon=True).start()
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no such method {method}"}})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict):
            handle(message)
    die(0)


if __name__ == "__main__":
    main()
