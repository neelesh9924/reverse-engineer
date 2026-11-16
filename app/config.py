"""Paths, and the settings the rest of the app reads as config.NAME.

Every setting has a default that suits a typical Windows PC. data/settings.json (not in git)
holds what the Settings page saved, and may name any other setting by hand; load() reads it
when the studio starts and again after every save.

Reverse Engineer runs the `rea-agents` reverse-engineering engine (github.com/morluto/rea)
on this PC and drives it with headless Claude Code. Nothing in this module opens a network
connection, and a path that names a network share is refused as text, before the filesystem
is asked about it (local_path_problem).
"""
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

APP_NAME = "Reverse Engineer"
APP_ID = "reverse-engineer"

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"        # the studio's own page: plain HTML, CSS and JavaScript
# The cloned rea-agents checkout this studio drives. Default: a sibling "rea" folder.
# Empty in settings keeps this default; any absolute path overrides it.
_DEFAULT_REA = ROOT.parent / "rea"
# Everything of the user's lives here and is ignored by git. Tests point it at a temporary folder.
DATA_DIR = Path(os.environ.get("STUDIO_DATA_DIR") or ROOT / "data")

# REA's package.json "engines": ^22.23.4 || ^24.22.1 || >=26.12.1. The one place this is written.
NODE_MINIMUMS = ((22, 23, 4), (24, 22, 1), (26, 12, 1))
NODE_REQUIREMENT = "22.23.4+, 24.22.1+ or 26.12.1+"
NODE_MAJOR = 24     # the major fnm is asked for, so the machine's default Node is left alone

DEFAULTS = {
    "theme": "auto",         # the studio's own look: auto (as Windows has it), light or dark
    "rea_dir": "",           # the rea-agents checkout; empty = the sibling ../rea folder
    "node_bin": "",          # the Node binary REA needs (NODE_REQUIREMENT); empty = found (fnm 24, then PATH)
    "claude_bin": "",        # the Claude Code program; empty = found by itself
    "model": "",             # passed to claude --model; empty = the account's default
    "max_turns": 120,        # model steps of one request: open, inspect, decompile, trace, verify
    "run_timeout": 1800,     # seconds before one Claude request is stopped
    "engine_timeout": 900,   # seconds one REA tool call may take (some providers are slow)
    "port": 0,               # 0 = any free port on 127.0.0.1
    "browser": "",           # chrome.exe or msedge.exe for the app window; empty = found by itself
    "backends": {},          # per-backend {id: {active, path}}, managed on the Settings page
    "claude_destructive": False,   # let Claude run REA tools that change state (off: it asks no such tool)
    "notice_seen": False,    # the responsible-use notice on the Settings page was acknowledged
    "analysis_settings": {},  # REA analysis settings {name: value}, re-applied after each engine start
}
_NAMES = {key: key.upper() for key in DEFAULTS}

# Inclusive ranges for the numeric settings. "port" also takes 0 (any free port).
RANGES = {
    "port": (1024, 65535),
    "max_turns": (1, 1000),
    "run_timeout": (30, 86400),
    "engine_timeout": (30, 86400),
}
# Settings that name a file or folder on this PC.
PATH_SETTINGS = ("rea_dir", "node_bin", "claude_bin", "browser")


class ConfigError(RuntimeError):
    pass


def apply(values):
    """Make these settings the ones in force."""
    for key, name in _NAMES.items():
        globals()[name] = values.get(key, DEFAULTS[key])


apply(DEFAULTS)


def settings_file():
    return DATA_DIR / "settings.json"


# ---- paths as text ----

_DRIVE_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³]|conin\$|conout\$)(\..*)?$", re.IGNORECASE)


def local_path_problem(text):
    """What is wrong with `text` as a full path on this PC, in plain words ("" when nothing is).

    Checked as text only, before any filesystem call: a UNC path (\\\\host\\share, //host/share),
    the \\\\?\\ and \\\\.\\ device forms, and reserved device names would make Windows open a
    network connection or a device rather than read a local file."""
    if not isinstance(text, str) or not text:
        return "Give the full path on this PC."
    if "\0" in text:
        return "That path has a character a path cannot have."
    if re.match(r"^[\\/]{2}", text):
        return "Use a path on this PC, not a network share."
    if os.name == "nt":
        if not _DRIVE_ABSOLUTE.match(text):
            return "Give the full path on this PC, starting with a drive letter, like C:\\…"
    elif not text.startswith("/"):
        return "Give the full path on this PC, starting with /."
    for part in re.split(r"[\\/]+", text[2:] if os.name == "nt" else text):
        if _RESERVED.match(part.strip().rstrip(". ")):
            return f"“{part}” is a device name in Windows, not a file or folder."
    return ""


def _fits(key, default, value):
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if isinstance(default, int):
        if not isinstance(value, int):
            return False
        low, high = RANGES.get(key, (0, None))
        if key == "port" and value == 0:
            return True
        return value >= low and (high is None or value <= high)
    if isinstance(default, dict):
        return isinstance(value, dict)
    if not isinstance(value, str):
        return False
    return not (key in PATH_SETTINGS and value and local_path_problem(value))


def range_text(key):
    low, high = RANGES[key]
    return f"0 or {low}–{high}" if key == "port" else f"{low}–{high}"


def read_settings():
    """(the settings file as it is, what is wrong with it)."""
    try:
        raw = json.loads(settings_file().read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}, []
    except (OSError, ValueError) as exc:
        return {}, [f"data/settings.json cannot be read ({exc}); the defaults are used."]
    if not isinstance(raw, dict):
        return {}, ["data/settings.json is not a JSON object; the defaults are used."]
    return raw, []


def _clean_backends(value, problems):
    out = {}
    for key, entry in value.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        path = entry.get("path", "")
        if not isinstance(path, str) or (path and local_path_problem(path)):
            problems.append(f'data/settings.json: the path for backend "{key}" cannot be used; it is ignored.')
            entry = {**entry, "path": ""}
        out[key] = entry
    return out


def load():
    """Read data/settings.json and put it in force. Returns what was wrong with it, in plain words."""
    global _node_probe
    raw, problems = read_settings()
    values = dict(DEFAULTS)
    for key, value in raw.items():
        if key not in DEFAULTS:
            problems.append(f'data/settings.json: "{key}" is not a setting; it is ignored.')
        elif not _fits(key, DEFAULTS[key], value):
            if key in RANGES and isinstance(value, int) and not isinstance(value, bool):
                problems.append(f'data/settings.json: "{key}" must be {range_text(key)}; the default is kept.')
            elif key in PATH_SETTINGS and isinstance(value, str):
                problems.append(f'data/settings.json: "{key}": {local_path_problem(value)} The default is kept.')
            else:
                problems.append(f'data/settings.json: "{key}" has a value that cannot be used; the default is kept.')
        elif key == "backends":
            values[key] = _clean_backends(value, problems)
        else:
            values[key] = value
    apply(values)
    with _probe_lock:
        _node_probe = None
    return problems


def current():
    return {key: globals()[name] for key, name in _NAMES.items()}


# ---- where things are ----

def subjects_dir():
    return DATA_DIR / "subjects"


def logs_dir():
    return DATA_DIR / "logs"


def rea_dir():
    """The rea-agents checkout this studio drives (as settings name it, else the sibling ../rea)."""
    return Path(REA_DIR) if REA_DIR else _DEFAULT_REA  # noqa: F821 (set by apply)


def rea_entry():
    """REA's CLI entry script, or None when the checkout is not where it should be."""
    entry = rea_dir() / "scripts" / "rea.mjs"
    return entry if entry.is_file() else None


def rea_catalog_file():
    """The built MCP tool catalogue inside the checkout, or None before `npm run build`."""
    built = rea_dir() / "dist" / "mcp-catalog.json"
    return built if built.is_file() else None


def rea_built():
    """REA's compiled runtime is present (dist/mcp/main.js), so `rea mcp` can start."""
    return (rea_dir() / "dist" / "mcp" / "main.js").is_file()


def rea_version():
    """The checkout's version, from its package.json ("" when it cannot be read)."""
    try:
        raw = json.loads((rea_dir() / "package.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return ""
    version = raw.get("version") if isinstance(raw, dict) else None
    return version if isinstance(version, str) else ""


def doctor_hint():
    """How to run REA's own diagnosis from its checkout on this PC, as one sentence."""
    node, _ = node_command()
    runner = " ".join(f'"{part}"' if " " in part else part for part in node) if node else "node"
    return f"In {rea_dir()}, run `{runner} scripts/rea.mjs doctor` to see what REA is missing."


# ---- programs ----

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_probe_lock = threading.Lock()
_node_probe = None   # (fnm path, whether `fnm exec --using=24 node --version` worked), until the next load()


def fnm_exe():
    """fnm, the Node version manager (WinGet's link, else on PATH from scoop, choco or cargo), or None."""
    local = os.environ.get("LOCALAPPDATA")
    path = Path(local) / "Microsoft" / "WinGet" / "Links" / "fnm.exe" if local else None
    if path and path.is_file():
        return path
    found = shutil.which("fnm")
    return Path(found) if found else None


def _fnm_has_node(fnm):
    """Whether fnm can run REA's Node major. Asked once per load(); forget_node_probe() asks again."""
    global _node_probe
    with _probe_lock:
        if _node_probe and _node_probe[0] == str(fnm):
            return _node_probe[1]
    try:
        done = subprocess.run([str(fnm), "exec", f"--using={NODE_MAJOR}", "node", "--version"],
                              capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL,
                              creationflags=_NO_WINDOW)
        works = done.returncode == 0 and bool(re.search(r"v?\d+\.\d+\.\d+", done.stdout or ""))
    except (OSError, subprocess.SubprocessError):
        works = False
    with _probe_lock:
        _node_probe = (str(fnm), works)
    return works


def forget_node_probe():
    """Ask fnm again next time (after the user may have run `fnm install 24`)."""
    global _node_probe
    with _probe_lock:
        _node_probe = None


def fnm_lacks_node():
    """fnm is here but cannot run Node 24, so it was passed over."""
    fnm = fnm_exe()
    return bool(fnm) and not NODE_BIN and not _fnm_has_node(fnm)  # noqa: F821


def node_command():
    """(the Node command as a list, how it was found). REA needs Node NODE_REQUIREMENT.

    A node_bin in Settings wins. Otherwise fnm runs Node 24 without touching the machine's
    default Node, which other projects may pin older, when fnm has a Node 24; then a plain `node`
    on PATH. (None, "") when there is nothing runnable."""
    if NODE_BIN:  # noqa: F821
        if local_path_problem(NODE_BIN) or not Path(NODE_BIN).is_file():  # noqa: F821
            return None, ""
        return [NODE_BIN], "as Settings name it"  # noqa: F821
    fnm = fnm_exe()
    if fnm and _fnm_has_node(fnm):
        return [str(fnm), "exec", f"--using={NODE_MAJOR}", "node"], f"Node {NODE_MAJOR} through fnm"
    found = shutil.which("node")
    if found:
        return [found], "node on PATH" + (f" (fnm has no Node {NODE_MAJOR})" if fnm else "")
    return None, ""


def node_ok(version):
    """Whether a Node version string such as "v24.22.1" meets REA's requirement."""
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version or "")
    if not match:
        return False
    found = tuple(int(g) for g in match.groups())
    for minimum in NODE_MINIMUMS:
        if found[0] == minimum[0] and found >= minimum:
            return True
    return found[0] > NODE_MINIMUMS[-1][0]


def claude_bin():
    """Path of the Claude Code program, or None."""
    if CLAUDE_BIN:  # noqa: F821
        if local_path_problem(CLAUDE_BIN):  # noqa: F821
            return None
        return CLAUDE_BIN if Path(CLAUDE_BIN).is_file() else None  # noqa: F821
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
    return str(fallback) if fallback.is_file() else None


_BROWSER_PLACES = [
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
]


def browser_path():
    """Chrome or Edge, for the app window and a website subject's observed browser (settings
    override, else found)."""
    if BROWSER:  # noqa: F821 (set by apply)
        if local_path_problem(BROWSER):  # noqa: F821
            return None
        return BROWSER if Path(BROWSER).is_file() else None  # noqa: F821
    found = shutil.which("chrome") or shutil.which("msedge")
    if found:
        return found
    for place in _BROWSER_PLACES:
        path = os.path.expandvars(place)
        if "%" not in path and Path(path).is_file():
            return path
    return None


def web_profiles_dir():
    """Where each website subject's observed-browser profile lives (never the user's own Chrome)."""
    return DATA_DIR / "web-profiles"
