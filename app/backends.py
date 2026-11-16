"""The analysis backends REA can use, in one place: how each is detected, what it unlocks, and
the environment variable that hands it to REA.

REA is bring-your-own: it finds a backend on PATH or through an environment variable. The studio
lets the user manage each one from the dashboard — mark it active or inactive, and set a path
for an absent one — and injects the active ones' environment when it starts the REA engine, so a
backend you point at here is the backend REA uses.

A backend found only on PATH is active by its presence; marking it inactive stops the studio
*offering* it, but cannot hide a tool already on PATH from REA (noted in the UI).
"""
import os
import shutil
from pathlib import Path

from . import config

# env "kind": "dir" (a folder), "file" (a program/jar/config), or "" (no env hook — presence only).
# (id, label, unlocks, env_var, kind)
_DEFS = [
    ("java", "Java (JDK)", "Ghidra and the Android decompiler need it", "JAVA_HOME", "dir"),
    ("ghidra", "Ghidra", "native decompilation and disassembly", "GHIDRA_INSTALL_DIR", "dir"),
    ("jadx", "JADX", "Android (APK/DEX) decompilation", "REA_JADX_MCP_JAR", "file"),
    ("adb", "ADB", "pulling and inspecting apps on a device", "REA_ADB_PATH", "file"),
    ("apktool", "apktool", "Android resource decoding", "REA_APKTOOL_COMMAND", "file"),
    ("frida", "Frida", "runtime instrumentation of a running process", "", ""),
    ("chrome", "Chrome", "web and Electron observation, and website subjects", "REA_BROWSER_EXECUTABLE", "file"),
    ("tshark", "tshark / Wireshark", "reading packet captures", "REA_TSHARK_COMMAND", "file"),
    ("rizin", "Rizin", "an extra disassembly and debug backend", "REA_RIZIN_COMMAND", "file"),
    ("objdump", "binutils (objdump)", "quick object-file inspection", "REA_OBJDUMP_COMMAND", "file"),
    ("gdb", "GDB", "native debugging sessions", "REA_GDB_COMMAND", "file"),
    ("binwalk", "binwalk", "firmware extraction", "REA_BINWALK_COMMAND", "file"),
    ("kuna", "Kuna", "an alternative native analyser", "REA_KUNA_COMMAND", "file"),
    ("ida", "IDA", "an alternative native decompiler (commercial)", "REA_IDA_MCP_CONFIG", "file"),
    ("hopper", "Hopper", "an alternative native decompiler (commercial)", "HOPPER_LAUNCHER_PATH", "file"),
]
BY_ID = {d[0]: d for d in _DEFS}


def _program_dirs(*patterns):
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.environ.get("ProgramW6432", "")]
    found = []
    for root in filter(None, roots):
        for pattern in patterns:
            found += [str(p) for p in Path(root).glob(pattern) if p.exists()]
    return found


def _which(*names):
    for name in names:
        hit = shutil.which(name)
        if hit:
            return hit
    return ""


def _detect(key):
    if key == "ghidra":
        env = os.environ.get("GHIDRA_INSTALL_DIR", "")
        if env and Path(env).exists():
            return env
        dirs = _program_dirs("ghidra*", "Ghidra*")
        return dirs[0] if dirs else _which("ghidraRun")
    if key == "java":
        home = os.environ.get("JAVA_HOME", "")
        if home and Path(home).exists():
            return home
        return _which("java")
    if key == "frida":
        pkg = config.rea_dir() / "node_modules" / "frida"
        return str(pkg) if pkg.is_dir() else _which("frida")
    if key == "chrome":
        return config.browser_path() or ""
    if key == "tshark":
        return _which("tshark") or (_program_dirs(r"Wireshark\tshark.exe") or [""])[0]
    if key == "jadx":
        return _which("jadx", "jadx.bat")
    if key == "adb":
        return _which("adb")
    if key == "apktool":
        return _which("apktool", "apktool.bat")
    if key == "rizin":
        return _which("rizin")
    if key == "objdump":
        return _which("objdump")
    if key == "gdb":
        return _which("gdb")
    if key == "binwalk":
        return _which("binwalk", "unblob")
    if key == "kuna":
        return _which("kuna")
    if key in ("ida", "hopper"):
        return _which(f"{key}64", key)
    return ""


def settings_map():
    """The user's per-backend choices: {id: {active: bool, path: str}}."""
    raw = config.BACKENDS if isinstance(config.BACKENDS, dict) else {}  # noqa: F821 (set by config.apply)
    out = {}
    for key, entry in raw.items():
        if key in BY_ID and isinstance(entry, dict):
            out[key] = {"active": entry.get("active", True) is not False,
                        "path": str(entry.get("path", "") or "")}
    return out


def inventory():
    """Each backend with its detection, the user's choice, and the path REA will be given."""
    chosen = settings_map()
    out = []
    for key, label, unlocks, env_var, kind in _DEFS:
        found = ""
        try:
            found = _detect(key) or ""
        except Exception:
            found = ""
        choice = chosen.get(key, {})
        active = choice.get("active", True) is not False
        set_path = choice.get("path", "")
        effective = set_path or found
        out.append({
            "id": key, "label": label, "unlocks": unlocks, "env": env_var, "kind": kind,
            "found": bool(found), "found_path": found, "configurable": bool(env_var),
            "active": active, "path": set_path, "effective": effective,
            "in_use": bool(active and effective),
        })
    return out


def engine_env():
    """The environment to give the REA engine: each active backend's variable set to its path.

    A backend set inactive is not offered; the native provider is pinned when exactly one native
    decompiler is active, so an inactive one is not picked up from PATH."""
    env = {}
    items = {b["id"]: b for b in inventory()}
    for backend in items.values():
        if backend["configurable"] and backend["active"] and backend["path"]:
            env[backend["env"]] = backend["path"]
    # Steer the native analysis provider by what is active, where REA offers that choice.
    natives = [k for k in ("ghidra", "ida", "kuna", "hopper") if k in items]
    active_natives = [k for k in natives if items[k]["in_use"]]
    if len(active_natives) == 1:
        env["REA_ANALYSIS_PROVIDER"] = active_natives[0]
    return env
