"""The Settings checks: is Claude Code here and signed in, is a suitable Node here, is REA
built, and which analysis backends this PC has. Nothing here sends Claude a request or
runs a REA analysis; backends are detected by looking for their programs, not by running
them against a target.
"""
import json
import os
import re
import subprocess

from . import backends as backends_mod
from . import catalog, config
from .claude import build_env
from .winjob import CREATE_NO_WINDOW

NAMES = ("claude", "node", "rea", "backends")


def _run(cmd, timeout):
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=build_env(), stdin=subprocess.DEVNULL,
                          creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    return done.stdout or "", done.stderr or ""


def claude(runner):
    base = runner.base_command()
    if not base:
        return {"ok": False, "found": False,
                "error": "Claude Code was not found on this PC. Install it, or give its path in Settings."}
    try:
        out, err = _run(base + ["--version"], 30)
        version = (out or err).strip().splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return {"ok": False, "found": True, "path": " ".join(base), "error": f"Claude Code did not start from {base[-1]}."}
    try:
        out, _ = _run(base + ["auth", "status", "--json"], 30)
        status = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        status = None
    if not isinstance(status, dict):
        return {"ok": False, "found": True, "path": " ".join(base), "version": version,
                "error": "Claude Code did not say whether it is signed in. Run claude once in a terminal."}
    if status.get("loggedIn") is not True:
        return {"ok": False, "found": True, "path": " ".join(base), "version": version, "signedIn": False,
                "error": "Claude Code is not signed in. Run claude once in a terminal and sign in."}
    return {"ok": True, "found": True, "version": version, "signedIn": True, "path": " ".join(base),
            "account": str(status.get("email") or ""), "plan": str(status.get("subscriptionType") or "")}


def node():
    config.forget_node_probe()   # a re-check notices a Node installed since the last one
    base, how = config.node_command()
    need = f"REA needs Node {config.NODE_REQUIREMENT}."
    if not base:
        if config.fnm_lacks_node():
            error = (f"fnm is installed but has no Node {config.NODE_MAJOR}, and no other Node was found. "
                     f"Run `fnm install {config.NODE_MAJOR}`. {need}")
        else:
            error = f"No Node was found. {need} Install it (for example `fnm install {config.NODE_MAJOR}`), or give its path in Settings."
        return {"ok": False, "found": False, "requirement": config.NODE_REQUIREMENT, "error": error}
    where = {"found": True, "how": how, "path": " ".join(base), "requirement": config.NODE_REQUIREMENT}
    try:
        out, err = _run(base + ["--version"], 30)
        version = (out or err).strip().splitlines()[-1].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return {"ok": False, **where, "error": f"Node did not start. {need}"}
    if not re.search(r"\d+\.\d+\.\d+", version):
        return {"ok": False, **where, "error": f"Node reported “{version}”, which could not be read."}
    ok = config.node_ok(version)
    out = {"ok": ok, **where, "version": version.lstrip("v")}
    if not ok:
        out["error"] = f"Node {version.lstrip('v')} is too old for REA. {need}"
    return out


def rea():
    entry = config.rea_entry()
    where = str(config.rea_dir())
    version = config.rea_version()
    if entry is None:
        return {"ok": False, "found": False, "dir": where,
                "error": f"REA is not at {where}. Clone github.com/morluto/rea there, or set the folder in Settings."}
    if not config.rea_built():
        return {"ok": False, "found": True, "built": False, "dir": where, "version": version,
                "error": f"REA is cloned but not built. In it run `npm ci` then `npm run build:cached` "
                         f"with Node {config.NODE_REQUIREMENT}."}
    parsed = catalog.load()
    if not parsed:
        return {"ok": False, "found": True, "built": True, "dir": where, "version": version,
                "error": "REA is built, but its tool catalogue could not be read. Rebuild it (`npm run build:cached`)."}
    out = {"ok": True, "found": True, "built": True, "dir": where, "version": version or parsed["version"],
           "built_version": parsed["version"], "tools": parsed["count"], "prompts": len(parsed["prompts"])}
    if version and parsed["version"] and version != parsed["version"]:
        out["warning"] = (f"The checkout is REA {version}, but its build is {parsed['version']}. "
                          "Rebuild it (`npm run build:cached`) so the tools match.")
    return out


def backends():
    """Which analysis backends are present, what the user chose for each, and what REA will be
    given. One source of truth lives in app/backends.py."""
    return backends_mod.inventory()


def run_all(runner, put=None):
    """Run every check, each on its own: one that fails never keeps an earlier result or stops the
    others. put(name, result) is called as each one finishes. Returns {name: result}."""
    work = {"claude": lambda: claude(runner), "node": node, "rea": rea, "backends": backends}
    results = {}
    for name in NAMES:
        try:
            result = work[name]()
        except Exception as exc:  # a check must never take the others down with it
            result = {"ok": False, "error": f"The check failed: {exc}"}
        results[name] = result
        if put is not None:
            put(name, result)
    return results
