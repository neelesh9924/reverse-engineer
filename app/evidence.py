"""The findings of each subject, on disk: every tool result the studio saw (whether Claude, the
Tools page or a panel ran it), kept so they survive switching subjects, an engine restart and
a studio restart.

data/subjects/<slug>/evidence.jsonl holds one entry per line, appended as they come. Only the
newest MAX_KEPT are kept; when there are more, the oldest are dropped and the `pruned` count in
evidence-meta.json grows, so the page can say how many older ones were removed. Very long
strings (a screenshot's base64, a big dump) are stored once each beside it, in
evidence-blobs/<sha256>.json, so the log stays small to read; every entry handed out has them
back in place. A reference cannot be forged: an object in a finding that already uses one of the
two marker keys (data from the target, or arguments Claude sent) is stored wrapped and comes
back as it was, and only a 64-hex-digit name inside the blob folder is ever read.

Each subject has its own lock. A log that is there but cannot be read raises EvidenceError
rather than passing for "no findings", so nothing is written over it.
"""
import hashlib
import itertools
import json
import os
import re
import secrets
import threading
import time

from . import subjects

MAX_KEPT = 500
COMPACT_SLACK = 100            # the log is rewritten when it holds this many lines more than it keeps
BLOB_MIN = 64 * 1024           # strings at least this long are stored beside the log
_BLOB = "$re_blob"             # {"$re_blob": "<sha256>", "length": n} stands in for a stored string
_ESC = "$re_esc"               # {"$re_esc": {...}} holds an object of the finding's own that uses a marker key
_SHA = re.compile(r"^[0-9a-f]{64}$")
SOURCES = ("claude", "tools", "panel")


class EvidenceError(RuntimeError):
    pass


class _Log:
    def __init__(self, entries=(), lines=0, pruned=0, seq=0, dirty=False):
        self.entries = list(entries)   # oldest first, with long strings as blob references
        self.lines = lines             # lines in the file now (kept + dropped but not yet compacted)
        self.pruned = pruned
        self.seq = seq
        self.dirty = dirty             # the file has a bad or unfinished line: rewrite before appending


_guard = threading.Lock()
_locks = {}
_logs = {}                         # by folder, so a different data folder never sees another's log
_revs = {}
_counter = itertools.count(1)       # one sequence for every subject, so a value never comes back


def lock_for(slug):
    """The lock every change to this subject's findings holds (re-entrant)."""
    with _guard:
        return _locks.setdefault(slug, threading.RLock())


def _bump(slug):
    with _guard:
        _revs[slug] = next(_counter)


def revision(slug):
    """A number that changes every time this subject's findings change (0 before the first change)."""
    with _guard:
        return _revs.get(slug, 0)


def _check_slug(slug):
    if not isinstance(slug, str) or not subjects.SLUG_RE.match(slug):
        raise EvidenceError("That subject is no longer there.")


def _key(slug):
    return str(subjects.folder(slug))


def _log_path(slug):
    return subjects.folder(slug) / "evidence.jsonl"


def _meta_path(slug):
    return subjects.folder(slug) / "evidence-meta.json"


def _blob_dir(slug):
    return subjects.folder(slug) / "evidence-blobs"


# ---- reading ----

def _read_text(path):
    last = None
    for attempt in range(3):
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except (OSError, ValueError) as exc:
            last = exc
            if attempt < 2:
                time.sleep(0.05)
    raise EvidenceError(f"The findings could not be read ({last}). Nothing was changed; try again.")


def _load(slug):
    """This subject's log, read once and then kept in memory. Call with the lock held."""
    log = _logs.get(_key(slug))
    if log is not None:
        return log
    text = _read_text(_log_path(slug))
    try:
        meta = subjects.read_json(_meta_path(slug), {})
    except subjects.SubjectError as exc:
        raise EvidenceError(str(exc)) from exc
    meta = meta if isinstance(meta, dict) else {}
    entries = []
    lines = 0
    dirty = bool(text) and not text.endswith("\n")
    for line in text.split("\n"):
        if not line.strip():
            continue
        lines += 1
        try:
            entry = json.loads(line)
        except ValueError:
            dirty = True
            continue
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            entries.append(entry)
        else:
            dirty = True
    kept = entries[-MAX_KEPT:]
    seqs = [e.get("seq") for e in entries if isinstance(e.get("seq"), int)]
    pruned = meta.get("pruned") if isinstance(meta.get("pruned"), int) and meta["pruned"] >= 0 else 0
    seq = max([meta.get("seq") if isinstance(meta.get("seq"), int) else 0] + seqs)
    log = _Log(kept, lines, pruned, seq, dirty)
    _logs[_key(slug)] = log
    return log


def _blob_ref(value):
    """The sha256 of a blob reference the store wrote, or None for any other value."""
    if isinstance(value, dict) and set(value) == {_BLOB, "length"} and isinstance(value["length"], int):
        sha = value[_BLOB]
        if isinstance(sha, str) and _SHA.match(sha):
            return sha
    return None


def _escaped(value):
    """The object an escape wrapper holds, or None when `value` is not one."""
    if isinstance(value, dict) and set(value) == {_ESC} and isinstance(value[_ESC], dict):
        return value[_ESC]
    return None


def _read_blob(slug, sha):
    folder = _blob_dir(slug).resolve()
    path = (folder / f"{sha}.json").resolve()
    if path.parent != folder:
        return ""
    try:
        text = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return text if isinstance(text, str) else ""


def _inline(slug, value, cache):
    """`value` with every blob reference replaced by the string it stands for, and every
    escaped object back as it was."""
    if isinstance(value, dict):
        own = _escaped(value)
        if own is not None:
            return {key: _inline(slug, item, cache) for key, item in own.items()}
        sha = _blob_ref(value)
        if sha is not None:
            if sha not in cache:
                cache[sha] = _read_blob(slug, sha)
            return cache[sha]
        return {key: _inline(slug, item, cache) for key, item in value.items()}
    if isinstance(value, list):
        return [_inline(slug, item, cache) for item in value]
    return value


def _blobs_in(value, found):
    if isinstance(value, dict):
        own = _escaped(value)
        if own is not None:
            return _blobs_in(own, found)
        sha = _blob_ref(value)
        if sha is not None:
            found.add(sha)
            return found
        for item in value.values():
            _blobs_in(item, found)
    elif isinstance(value, list):
        for item in value:
            _blobs_in(item, found)
    return found


# ---- writing ----

def _store_long_strings(slug, value):
    """`value` with each string of BLOB_MIN or more characters written to a blob file and replaced
    by a reference to it."""
    if isinstance(value, str):
        if len(value) < BLOB_MIN:
            return value
        sha = hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()
        path = _blob_dir(slug) / f"{sha}.json"
        if not path.is_file():
            subjects.write_text(path, json.dumps(value))   # as JSON: ASCII, so any string survives
        return {_BLOB: sha, "length": len(value)}
    if isinstance(value, dict):
        stored = {key: _store_long_strings(slug, item) for key, item in value.items()}
        # An object of the finding's own that uses a marker key is wrapped, so it never passes for one.
        return {_ESC: stored} if _BLOB in value or _ESC in value else stored
    if isinstance(value, list):
        return [_store_long_strings(slug, item) for item in value]
    return value


def _normalise(entry):
    if not isinstance(entry, dict):
        raise EvidenceError("A finding must be an object.")
    try:
        item = json.loads(json.dumps(entry, ensure_ascii=False, default=str))
    except (TypeError, ValueError) as exc:
        raise EvidenceError(f"A finding could not be stored ({exc}).") from exc
    for key in ("id", "seq", "at"):
        item.pop(key, None)
    tool = item.get("tool") if isinstance(item.get("tool"), str) else ""
    item["tool"] = tool
    item["title"] = item.get("title") if isinstance(item.get("title"), str) and item.get("title") else tool
    item["arguments"] = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
    item["source"] = item.get("source") if item.get("source") in SOURCES else "tools"
    item["untrusted"] = item.get("untrusted") is True
    item["session"] = item.get("session") if isinstance(item.get("session"), str) else ""
    item["error"] = item.get("error") if isinstance(item.get("error"), dict) else None
    item.setdefault("evidence", None)
    item["text"] = item.get("text") if isinstance(item.get("text"), str) else None
    item["continuation"] = item.get("continuation") if isinstance(item.get("continuation"), dict) else None
    return item


def _line(entry):
    # ASCII escapes, so a lone surrogate from a binary's strings or a U+2028 inside one can never
    # break the file or split a line.
    return json.dumps(entry, sort_keys=True) + "\n"


def _append(path, text):
    def work():
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    for attempt in range(5):
        try:
            return work()
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)
    return None


def _write_meta(slug, log):
    subjects.write_json(_meta_path(slug), {"pruned": log.pruned, "seq": log.seq})


def _compact(slug, log):
    """Rewrite the log as exactly what is kept, and delete blobs nothing refers to any more."""
    subjects.write_text(_log_path(slug), "".join(_line(e) for e in log.entries))
    log.lines = len(log.entries)
    log.dirty = False
    blob_dir = _blob_dir(slug)
    if blob_dir.is_dir():
        used = set()
        for entry in log.entries:
            _blobs_in(entry, used)
        for path in blob_dir.glob("*.json"):
            if path.stem not in used:
                try:
                    path.unlink()
                except OSError:
                    pass


def add(slug, entry):
    """Store one finding; returns it as stored, with its id ("e-<12 hex>"), seq and time."""
    _check_slug(slug)
    with lock_for(slug):
        if not subjects.exists(slug):
            raise EvidenceError("That subject is no longer there.")
        log = _load(slug)
        item = _normalise(entry)
        item["id"] = "e-" + secrets.token_hex(6)
        item["seq"] = log.seq + 1
        item["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            light = _store_long_strings(slug, item)
            if log.dirty:
                _compact(slug, log)
            _append(_log_path(slug), _line(light))
        except OSError as exc:
            raise EvidenceError(f"The finding could not be saved: {exc}") from exc
        log.entries.append(light)
        log.lines += 1
        log.seq = item["seq"]
        try:
            if len(log.entries) > MAX_KEPT:
                dropped = len(log.entries) - MAX_KEPT
                del log.entries[:dropped]
                log.pruned += dropped
                _write_meta(slug, log)
            if log.lines > MAX_KEPT + COMPACT_SLACK:
                _compact(slug, log)
        except OSError:
            # The finding is saved; tidying up is tried again on the next one.
            log.dirty = log.lines > MAX_KEPT + COMPACT_SLACK
        _bump(slug)
        return item


def listing(slug, q="", tool="", offset=0, limit=200):
    """{"total", "items", "pruned"}: the findings, newest first, that match `tool` (exactly) and
    `q` (anywhere in the tool name, title or the entry's JSON, ignoring case). `total` counts the
    matches; `pruned` counts older findings removed to keep the newest MAX_KEPT."""
    _check_slug(slug)
    q = (q if isinstance(q, str) else "").strip().lower()
    tool = tool if isinstance(tool, str) else ""
    offset = max(0, offset) if isinstance(offset, int) and not isinstance(offset, bool) else 0
    limit = min(MAX_KEPT, max(1, limit)) if isinstance(limit, int) and not isinstance(limit, bool) else 200
    with lock_for(slug):
        if not subjects.exists(slug):
            return {"total": 0, "items": [], "pruned": 0}
        log = _load(slug)
        matches = []
        for entry in reversed(log.entries):
            if tool and entry.get("tool") != tool:
                continue
            if q and q not in json.dumps(entry, ensure_ascii=False).lower():
                continue
            matches.append(entry)
        page = matches[offset:offset + limit]
        cache = {}
        items = [_inline(slug, entry, cache) for entry in page]
        return {"total": len(matches), "items": items, "pruned": log.pruned}


def get(slug, entry_id):
    """One finding by its id, or None."""
    _check_slug(slug)
    with lock_for(slug):
        if not subjects.exists(slug):
            return None
        for entry in reversed(_load(slug).entries):
            if entry.get("id") == entry_id:
                return _inline(slug, entry, {})
    return None


def count(slug):
    """How many findings are kept for this subject. Display only: 0 when they cannot be read now."""
    try:
        _check_slug(slug)
        with lock_for(slug):
            return len(_load(slug).entries) if subjects.exists(slug) else 0
    except EvidenceError:
        return 0


def _delete_files(slug):
    for path in (_log_path(slug), _meta_path(slug)):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise EvidenceError(f"The findings could not be deleted: {exc}") from exc
    subjects.remove_tree(_blob_dir(slug))


def clear(slug):
    """Delete this subject's findings (the conversation is not touched)."""
    _check_slug(slug)
    with lock_for(slug):
        if not subjects.exists(slug):
            return
        try:
            seq = _load(slug).seq
        except EvidenceError:
            seq = 0
        _delete_files(slug)
        log = _Log(seq=seq)
        _logs[_key(slug)] = log
        try:
            _write_meta(slug, log)   # keeps seq rising, so a later finding never reuses a number
        except OSError:
            pass
        _bump(slug)


def remove_all(slug):
    """Forget this subject's findings entirely, files and memory (when the subject is removed)."""
    _check_slug(slug)
    with lock_for(slug):
        _logs.pop(_key(slug), None)
        if subjects.folder(slug).is_dir():
            _delete_files(slug)
        _bump(slug)
