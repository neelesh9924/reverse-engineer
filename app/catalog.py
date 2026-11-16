"""REA's tool surface, read from the checkout's built catalogue (dist/mcp-catalog.json).

That file is REA's own generated manifest: every tool with its title, description, input
and output schema, and MCP hints (read-only, destructive, …), plus REA's prompts and the
server instructions. The studio reads it so the Tools page can show and run all of them,
and so the Ask Claude system prompt carries REA's own guidance verbatim.

The manifest's top-level "version" is its format (1); REA's own version is info.version.

Categories are derived here, in one place, from each tool's name — REA groups nothing for
us, and 229 tools need grouping to be browsable. The first matching rule wins, so order
matters; anything unmatched lands in "Other".
"""
import json
import threading

from . import config

# (category label, the substrings that place a tool in it). Checked in order.
_RULES = [
    ("Session & targets", ("open_binary", "close_binary", "binary_overview", "binary_session",
                           "extract_artifact", "inspect_artifact", "compare_artifacts", "list_architectures",
                           "extract_firmware", "firmware", "export_environment_bundle")),
    ("Android & ADB", ("adb", "android", "apk", "apktool", "flutter", "dart", "harmony")),
    ("Web, JS & Electron", ("javascript", "web", "browser", "electron", "bundle", "webmcp", "source")),
    ("Runtime & debug", ("frida", "gdb", "rizin", "cutter", "observe_native", "trace_native", "trace_dylib")),
    ("Captures & crashes", ("capture", "packet", "pcap", "signal", "recorded_crash", "apple_ips",
                            "bugreport", "network_capture")),
    ("Managed & languages", ("managed", "il2cpp", "go_binary", "rust_binary", "python_pyc", "erlang",
                             "wasm", "evm", "swift", "objc", "model_artifact", "gpu_code")),
    ("Files & formats", ("sqlite", "plist", "keyed_archive", "asset_catalog", "interface_builder",
                         "devicetree", "elf_notes", "pe_", "macho", "kernel_module", "byte_entropy",
                         "binary_layout", "signature")),
    ("Reconstruction & evidence", ("reconstruction", "evidence", "unknown", "obligation", "coverage",
                                   "correlate", "changed_behavior", "residual", "verify_")),
    ("Trace & features", ("trace_", "feature")),
    ("Functions & disassembly", ("procedure", "decompile", "function", "call_graph", "call_path", "xref",
                                 "assembly", "instruction", "pseudo", "annotate", "objdump", "demangle")),
    ("Navigation & notes", ("address", "goto", "current_", "next_", "prev_", "document", "segment",
                            "bookmark", "comment", "names", "strings", "bytes", "navigation", "list_",
                            "search_")),
    ("Settings", ("analysis_settings",)),
]

_lock = threading.Lock()
_cache = None   # (mtime, parsed catalogue)


def _category(name):
    for label, needles in _RULES:
        if any(n in name for n in needles):
            return label
    return "Other"


def order():
    """Category labels in the order the Tools page shows them."""
    return [label for label, _ in _RULES] + ["Other"]


def _hints(annotations):
    """(read_only, destructive, open_world) by the MCP defaults: a tool that does not say it is
    read-only may change state, and one that does not say otherwise may be destructive and reach
    beyond the target."""
    read_only = annotations.get("readOnlyHint") is True
    destructive = not read_only and annotations.get("destructiveHint", True) is not False
    open_world = annotations.get("openWorldHint", True) is not False
    return read_only, destructive, open_world


def _prompt(raw):
    args = []
    for arg in raw.get("arguments") or []:
        if isinstance(arg, dict) and isinstance(arg.get("name"), str) and arg["name"]:
            args.append({"name": arg["name"], "description": str(arg.get("description") or ""),
                         "required": arg.get("required") is True})
    return {"name": raw["name"], "title": raw.get("title") or raw["name"],
            "description": raw.get("description") or "", "arguments": args}


def load():
    """The parsed catalogue, cached by the file's mtime and size. None when REA is not built yet."""
    global _cache
    path = config.rea_catalog_file()
    if path is None:
        return None
    try:
        st = path.stat()
        mtime = (st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    with _lock:
        if _cache and _cache[0] == (str(path), mtime):
            return _cache[1]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    tools = []
    for tool in raw.get("tools") or []:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not tool["name"]:
            continue
        annotations = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
        read_only, destructive, open_world = _hints(annotations)
        tools.append({
            "name": tool["name"],
            "title": tool.get("title") or tool["name"],
            "description": tool.get("description") or "",
            "category": _category(tool["name"]),
            "inputSchema": tool.get("inputSchema") or {"type": "object", "properties": {}},
            "annotations": annotations,
            "read_only": read_only,
            "destructive": destructive,
            "open_world": open_world,
        })
    tools.sort(key=lambda t: (order().index(t["category"]), t["title"]))
    info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
    parsed = {
        "tools": tools,
        "by_name": {t["name"]: t for t in tools},
        "prompts": [_prompt(p) for p in (raw.get("prompts") or [])
                    if isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"]],
        "instructions": raw.get("instructions") or "",
        "version": str(info.get("version") or ""),
        "count": len(tools),
    }
    with _lock:
        _cache = ((str(path), mtime), parsed)
    return parsed


def instructions():
    parsed = load()
    return parsed["instructions"] if parsed else ""


def tool_names():
    parsed = load()
    return [t["name"] for t in parsed["tools"]] if parsed else []


def tool(name):
    """One tool as the Tools page shows it, or None."""
    parsed = load()
    return parsed["by_name"].get(name) if parsed and isinstance(name, str) else None


def has_tool(name):
    return tool(name) is not None


def is_destructive(name):
    """Whether REA marks this tool as one that may change or destroy state (MCP destructiveHint)."""
    found = tool(name)
    return bool(found and found["destructive"])


def prompt(name):
    """One of REA's prompts ({name, title, description, arguments}), or None."""
    parsed = load()
    if not parsed or not isinstance(name, str):
        return None
    return next((p for p in parsed["prompts"] if p["name"] == name), None)


def analysis_setting_specs():
    """{setting name: schema of its value}, read from update_analysis_settings' input schema.
    Empty when REA is not built or the tool's schema has an unexpected shape."""
    found = tool("update_analysis_settings")
    if not found:
        return {}
    items = (((found["inputSchema"].get("properties") or {}).get("changes") or {}).get("items") or {})
    specs = {}
    for option in items.get("anyOf") or items.get("oneOf") or []:
        props = option.get("properties") if isinstance(option, dict) else None
        if not isinstance(props, dict):
            continue
        name = (props.get("setting") or {}).get("const")
        if isinstance(name, str) and isinstance(props.get("value"), dict):
            specs[name] = props["value"]
    return specs


def catalogue():
    """What the Tools page shows: tools grouped by category, the prompts, counts."""
    parsed = load()
    if parsed is None:
        return {"ready": False, "categories": [], "prompts": [], "count": 0, "version": ""}
    groups = {}
    for item in parsed["tools"]:
        groups.setdefault(item["category"], []).append(item)
    categories = [{"name": label, "tools": groups[label]} for label in order() if label in groups]
    return {"ready": True, "categories": categories, "prompts": parsed["prompts"],
            "count": parsed["count"], "version": parsed["version"]}
