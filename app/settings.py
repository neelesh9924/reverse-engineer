"""The Settings page: what it shows, and saving what it changes into data/settings.json.

A save is checked whole first: nothing is written when anything in it cannot be used. A path is
checked as text (config.local_path_problem) before the filesystem is asked about it. Settings
not shown here (the port, the time limits) are kept as they are.
"""
import re
from pathlib import Path

from . import backends, catalog, config, subjects

EDITABLE = ("theme", "rea_dir", "node_bin", "claude_bin", "model", "backends",
            "claude_destructive", "notice_seen", "analysis_settings")
SWITCHES = {
    "claude_destructive": "Letting Claude run state-changing tools is on or off.",
    "notice_seen": "The notice is read or not.",
}
THEMES = ("auto", "light", "dark")
_MODEL = re.compile(r"^[A-Za-z0-9._\[\]-]{1,64}$")
_SETTING_NAME = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
MAX_ANALYSIS_SETTINGS = 32


def public():
    """The settings as the page shows them, with where things resolved to."""
    values = config.current()
    out = {key: values[key] for key in EDITABLE}
    out["rea_dir_used"] = str(config.rea_dir())
    node, how = config.node_command()
    out["node_used"] = " ".join(node) if node else ""
    out["node_how"] = how
    out["node_requirement"] = config.NODE_REQUIREMENT
    claude = config.claude_bin()
    out["claude_used"] = claude or ""
    out["rea_version"] = config.rea_version()
    return out


def _path_problem(text, what):
    """What is wrong with a path setting, checked as text first ("" when it is fine or empty)."""
    if not text:
        return ""
    problem = config.local_path_problem(text)
    return f"{what}: {problem}" if problem else ""


def check(changes):
    """(the values to keep, what is wrong) for the changes the page sends."""
    keep = {}
    problems = []
    for key, value in (changes or {}).items():
        if key not in EDITABLE:
            continue
        if key in SWITCHES:
            if not isinstance(value, bool):
                problems.append(SWITCHES[key])
            keep[key] = value is True
            continue
        if key == "backends":
            cleaned, errs = _check_backends(value)
            problems.extend(errs)
            keep[key] = cleaned
            continue
        if key == "analysis_settings":
            cleaned, errs = _check_analysis(value)
            problems.extend(errs)
            keep[key] = cleaned
            continue
        text = subjects.clean_path(value) if key in ("rea_dir", "node_bin", "claude_bin") else str(value or "").strip()
        if key == "theme":
            if text not in THEMES:
                problems.append("The look is auto, light or dark.")
        elif key == "model":
            if text and not _MODEL.match(text):
                problems.append("The model is a name like sonnet or opus.")
        elif key == "rea_dir":
            problem = _path_problem(text, "REA folder")
            if problem:
                problems.append(problem)
            elif text and not (Path(text) / "scripts" / "rea.mjs").is_file():
                problems.append("That folder is not a rea-agents checkout (no scripts\\rea.mjs in it).")
        elif key == "node_bin":
            problem = _path_problem(text, "Node")
            if problem:
                problems.append(problem)
            elif text and not Path(text).is_file():
                problems.append("There is no program at that path for Node.")
        elif key == "claude_bin":
            problem = _path_problem(text, "Claude Code")
            if problem:
                problems.append(problem)
            elif text and not Path(text).is_file():
                problems.append("There is no program at that path for Claude Code.")
        keep[key] = text
    return keep, problems


def _check_backends(value):
    """Validate the per-backend {id: {active, path}} map the Settings page sends."""
    if not isinstance(value, dict):
        return {}, ["The backends setting must be a map."]
    cleaned = {}
    problems = []
    for key, entry in value.items():
        spec = backends.BY_ID.get(key)
        if spec is None or not isinstance(entry, dict):
            continue
        active = entry.get("active", True) is not False
        path = subjects.clean_path(entry.get("path", "") or "")
        kind = spec[4]
        if path:
            problem = _path_problem(path, spec[1])
            if problem:
                problems.append(problem)
            elif kind == "dir" and not Path(path).is_dir():
                problems.append(f"{spec[1]}: that is not a folder on this PC.")
            elif kind == "file" and not Path(path).is_file():
                problems.append(f"{spec[1]}: there is no file at that path.")
        # keep only what differs from the default (active, no path), so the file stays small
        if not active or path:
            cleaned[key] = {"active": active, "path": path}
    return cleaned, problems


def _describe(schema):
    """A value schema in plain words, for a problem message."""
    options = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(options, list):
        return " or ".join(_describe(o) for o in options if isinstance(o, dict))
    kind = schema.get("type")
    if kind == "null":
        return "empty (REA's default)"
    if kind in ("integer", "number"):
        word = "a whole number" if kind == "integer" else "a number"
        low, high = schema.get("minimum"), schema.get("maximum")
        if low is not None and high is not None:
            return f"{word} from {low} to {high}"
        if low is not None:
            return f"{word} of at least {low}"
        return word
    if "enum" in schema:
        return "one of " + ", ".join(str(v) for v in schema["enum"])
    if kind == "boolean":
        return "on or off"
    return "text" if kind == "string" else "a value REA accepts"


def _value_fits(schema, value):
    """Whether `value` fits a JSON Schema of the small kind REA's analysis settings use."""
    if not isinstance(schema, dict):
        return False
    options = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(options, list):
        return any(_value_fits(o, value) for o in options)
    if "const" in schema and value != schema["const"]:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    kind = schema.get("type")
    if kind == "null":
        return value is None
    if kind == "boolean":
        return isinstance(value, bool)
    if kind in ("integer", "number"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if kind == "integer" and not (isinstance(value, int) or float(value).is_integer()):
            return False
        if "minimum" in schema and value < schema["minimum"]:
            return False
        if "maximum" in schema and value > schema["maximum"]:
            return False
        return True
    if kind == "string":
        if not isinstance(value, str):
            return False
        return len(value) >= schema.get("minLength", 0) and len(value) <= schema.get("maxLength", 4096)
    return kind is None and ("const" in schema or "enum" in schema)


def _check_analysis(value):
    """Validate {setting: value} for REA's analysis settings, against REA's own schema when it is built."""
    if not isinstance(value, dict):
        return {}, ["The analysis settings must be a map of setting to value."]
    if len(value) > MAX_ANALYSIS_SETTINGS:
        return {}, [f"Keep at most {MAX_ANALYSIS_SETTINGS} analysis settings."]
    specs = catalog.analysis_setting_specs()
    cleaned = {}
    problems = []
    for name, item in value.items():
        if not isinstance(name, str) or len(name) > 100 or not _SETTING_NAME.match(name):
            problems.append(f"“{str(name)[:100]}” is not an analysis setting name.")
            continue
        if specs:
            if name not in specs:
                problems.append(f"“{name}” is not one of REA's analysis settings.")
                continue
            if isinstance(item, float) and item.is_integer() and specs[name].get("type") != "number":
                item = int(item)
            if not _value_fits(specs[name], item):
                problems.append(f"“{name}” must be {_describe(specs[name])}.")
                continue
        elif not (item is None or isinstance(item, (bool, int, float)) or (isinstance(item, str) and len(item) <= 200)):
            problems.append(f"“{name}” must be a number, on/off, short text or empty.")
            continue
        cleaned[name] = item
    return cleaned, problems


def save(changes):
    """Save the page's changes. Returns (what changed, the problems); nothing is saved when there are any."""
    keep, problems = check(changes)
    if problems:
        return [], problems
    raw, unreadable = config.read_settings()
    if unreadable:
        # Writing now would replace the hand-set settings the page does not show with the page's own.
        return [], ["data/settings.json could not be read, so nothing was saved. Fix or remove it first, "
                    "then try again."]
    before = config.current()
    changed = [key for key, value in keep.items() if before[key] != value]
    for key, value in keep.items():
        if value == config.DEFAULTS[key]:
            raw.pop(key, None)
        else:
            raw[key] = value
    subjects.write_json(config.settings_file(), raw)
    config.load()
    return changed, []
