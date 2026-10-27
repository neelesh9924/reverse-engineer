"""Analysis subjects on disk. A subject is one thing you are taking apart — a binary, an
APK, a bundle, a capture, a website — with the target's location, and the conversation you
have had with Claude about it. data/subjects/<slug>/ holds subject.json, conversation.json,
the findings (app/evidence.py) and a work/ folder for files REA writes for this subject; the
target file itself is referenced where it lives, never copied in or changed.

Every read-modify-write of a subject's files holds that subject's lock, and a read that fails
for any reason other than the file not being there raises instead of passing for "empty", so a
passing hiccup (an antivirus scan, a reader holding the file) can never wipe a conversation.

Everything here is the user's and is ignored by git.
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from . import config

MAX_NAME = 120
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,78}$")
# "website" is a live URL observed in a browser; the others are local files REA opens.
KINDS = ("executable", "database", "archive", "artifact", "website")
FILE_KINDS = ("executable", "database", "archive", "artifact")
# open_binary's explicit interpretations; "" leaves REA's header-based detection.
FORMATS = ("dos-com", "raw-binary", "gamecube-dol")
# The CPU families REA accepts for a raw-binary load (REA src/domain/rawBinary.ts).
RAW_ARCHITECTURES = ("x86", "x86_64", "arm", "arm64", "6502", "m68k")
ENTRY_POINT_RE = re.compile(r"^0x[0-9A-Fa-f]{1,16}$")
# Targets at least this big are hashed on a background thread, so creating the subject is quick.
HASH_INLINE_MAX = 16 * 1024 * 1024

_EXT_KINDS = {
    "archive": (".apk", ".apks", ".xapk", ".aab", ".ipa", ".zip", ".jar", ".war", ".aar", ".asar",
                ".msix", ".msixbundle", ".appx", ".appxbundle", ".nupkg", ".crx", ".xpi", ".whl",
                ".tar", ".tgz", ".gz", ".xz", ".bz2", ".7z", ".rar", ".cab", ".dmg", ".pkg", ".deb",
                ".rpm", ".app", ".hap"),
    "executable": (".exe", ".dll", ".sys", ".so", ".dylib", ".elf", ".o", ".ko", ".efi", ".ocx", ".cpl",
                   ".scr", ".drv", ".com", ".dex", ".class", ".wasm", ".pyc", ".dol", ".axf", ".out"),
    "database": (".db", ".sqlite", ".sqlite3", ".db3", ".sqlitedb"),
    "artifact": (".pcap", ".pcapng", ".cap", ".sr", ".bin", ".img", ".rom", ".hex", ".fw", ".ips", ".crash",
                 ".dmp", ".mdmp", ".plist", ".nib", ".car", ".dtb", ".dts", ".js", ".mjs", ".map"),
}


class SubjectError(RuntimeError):
    """A subject could not be made or changed. `field` names the input at fault, when one is."""

    def __init__(self, message, field=""):
        super().__init__(message)
        self.field = field


# ---- locks ----

_locks_guard = threading.Lock()
_locks = {}


def lock_for(slug):
    """The lock every read-modify-write of this subject's files holds (re-entrant)."""
    with _locks_guard:
        return _locks.setdefault(slug, threading.RLock())


# ---- files ----

def _retry(work, attempts=5, pause=0.05, retry_on=(PermissionError,)):
    for attempt in range(attempts):
        try:
            return work()
        except retry_on:
            if attempt == attempts - 1:
                raise
            time.sleep(pause)
    return None


def write_text(path, text):
    """Write a file whole: to a unique temporary file beside it, then swapped in, so a reader
    never sees half of it and two writers never share a temporary file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        _retry(lambda: os.replace(tmp, path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json(path, data):
    """Write JSON with sorted keys and LF endings, so the same data is the same bytes."""
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def read_json(path, default):
    """The file's JSON, or `default` when there is no such file. Any other failure (a sharing
    violation, a half-written file) is retried briefly, then raises SubjectError."""
    path = Path(path)
    last = None
    for attempt in range(3):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (OSError, ValueError) as exc:
            last = exc
            if attempt < 2:
                time.sleep(0.05)
    raise SubjectError(f"{path.name} could not be read ({last}). Nothing was changed; try again.")


def remove_tree(path, attempts=6):
    """Delete a folder and everything in it, read-only files included. A file still held open
    (Windows) is retried for a moment. Returns True when the folder is gone."""
    path = Path(path)

    def unlock(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    handler = {"onexc": unlock} if sys.version_info >= (3, 12) else {"onerror": unlock}
    for attempt in range(attempts):
        if not path.exists():
            return True
        try:
            shutil.rmtree(path, **handler)
        except OSError:
            pass
        if not path.exists():
            return True
        time.sleep(0.1 * (attempt + 1))
    return not path.exists()


# ---- names and places ----

def slugify(name):
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-_")
    return base[:60].rstrip("-_") or "subject"


def folder(slug):
    return config.subjects_dir() / slug


def meta_path(slug):
    return folder(slug) / "subject.json"


def thread_path(slug):
    return folder(slug) / "conversation.json"


def web_profile_dir(slug):
    return config.web_profiles_dir() / slug


def exists(slug):
    return isinstance(slug, str) and bool(SLUG_RE.match(slug)) and meta_path(slug).is_file()


def slugs():
    root = config.subjects_dir()
    if not root.is_dir():
        return []
    return [p.name for p in root.iterdir() if p.is_dir() and SLUG_RE.match(p.name) and meta_path(p.name).is_file()]


def _taken(slug):
    # A leftover web profile counts as taken, so a new subject never inherits another's cookies.
    return folder(slug).exists() or web_profile_dir(slug).exists()


def free_slug(base):
    base = base if SLUG_RE.match(base or "") else slugify(base)
    if not _taken(base):
        return base
    stem = base[:70]
    for n in range(2, 1000):
        candidate = f"{stem}-{n}"
        if not _taken(candidate):
            return candidate
    return f"{stem}-{secrets.token_hex(3)}"


def slug_problem(slug):
    if not slug or not SLUG_RE.match(slug):
        return "A link is lowercase letters, numbers, - or _, and starts with a letter or number."
    return ""


def work_dir(slug):
    """data/subjects/<slug>/work: where tools write files for this subject (exports, snapshots,
    decompiled output). Made on first use."""
    if not exists(slug):
        raise SubjectError("That subject is no longer there.")
    path = folder(slug) / "work"
    path.mkdir(parents=True, exist_ok=True)
    return path


def attachments_dir(slug):
    """work/attachments: the files the user attached to the conversation (readable copies in its
    hidden .text/). Inside the work folder, so Claude's run can Read them and hand them to REA's
    tools by path, as the studio's path checks allow. attachments.save makes it."""
    return work_dir(slug) / "attachments"


# ---- what the user types ----

def clean_path(text):
    """A pasted path as a path: surrounding spaces and one pair of quotes removed (Explorer's
    "Copy as path" adds them), and a file:/// address turned into the path it names."""
    text = (text if isinstance(text, str) else "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    if text.lower().startswith("file:"):
        parts = urlsplit(text)
        if parts.netloc and parts.netloc.lower() != "localhost":
            return "\\\\" + parts.netloc + url2pathname(parts.path)   # a share; refused later, as text
        path = url2pathname(parts.path)
        text = path.lstrip("\\/") if re.match(r"^[\\/][A-Za-z]:", path) else path
    return text


def guess_kind(path_or_url):
    """The likely kind for a path or address, from its text alone (no filesystem call)."""
    text = clean_path(path_or_url)
    if re.match(r"^https?://", text, re.IGNORECASE):
        return "website"
    name = re.split(r"[\\/]", text.rstrip("\\/"))[-1].lower()
    for kind, extensions in _EXT_KINDS.items():
        if any(name.endswith(ext) for ext in extensions):
            return kind
    return "executable"


def website_url(text):
    """The address as the studio will open it, or None: http or https, a host, no user name or
    password, no spaces."""
    text = (text if isinstance(text, str) else "").strip()
    if not text or any(ch.isspace() for ch in text) or len(text) > 2048:
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return None
    if port == 0:
        return None
    return text


def _default_name(kind, target):
    if kind == "website":
        url = website_url(target)
        return urlsplit(url).hostname if url else ""
    return re.split(r"[\\/]", target.rstrip("\\/"))[-1] if target else ""


def _binary_options(kind, fmt, architecture, entry_point):
    """The open_binary options this subject keeps, checked by REA's rules. Raises SubjectError."""
    fmt = (fmt or "").strip().lower()
    architecture = (architecture or "").strip().lower()
    entry_point = (entry_point or "").strip()
    if kind == "website":
        if fmt or architecture or entry_point:
            raise SubjectError("A website has no file format, architecture or entry point.", "format")
        return {}
    if fmt and fmt not in FORMATS:
        raise SubjectError("The format is detected from the header, or one of dos-com, raw-binary, gamecube-dol.",
                           "format")
    if fmt == "raw-binary":
        if architecture not in RAW_ARCHITECTURES:
            raise SubjectError("A raw binary needs its architecture: " + ", ".join(RAW_ARCHITECTURES) + ".",
                               "architecture")
        if not ENTRY_POINT_RE.match(entry_point):
            raise SubjectError("A raw binary needs its load and entry address, written 0x… (for example 0x8000).",
                               "entry_point")
    else:
        if architecture:
            raise SubjectError("The architecture is given only for a raw binary; REA reads it from the header "
                               "otherwise.", "architecture")
        if entry_point and fmt != "gamecube-dol":
            raise SubjectError("An entry point is given only for a raw binary or a GameCube DOL.", "entry_point")
        if entry_point and not ENTRY_POINT_RE.match(entry_point):
            raise SubjectError("Write the entry point as 0x… (for example 0x80003100).", "entry_point")
    return {key: value for key, value in (("format", fmt), ("architecture", architecture),
                                          ("entry_point", entry_point)) if value}


# ---- the subject record ----

def _meta_strict(slug):
    record = read_json(meta_path(slug), None)
    if not isinstance(record, dict) or not record.get("id"):
        return None
    record.setdefault("name", slug)
    record.setdefault("target", {})
    return record


def meta(slug):
    """The subject's record, or None when it is not there (or cannot be read right now)."""
    if not exists(slug):
        return None
    with lock_for(slug):
        try:
            return _meta_strict(slug)
        except SubjectError:
            return None


def _stat_facts(path):
    """Size, mtime, and whether it is a folder; one stat. {} when it is not there."""
    try:
        st = os.stat(path)
    except OSError:
        return {}
    if stat.S_ISDIR(st.st_mode):
        return {"folder": True}
    if not stat.S_ISREG(st.st_mode):
        return {}
    return {"size": st.st_size, "mtime": int(st.st_mtime)}


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def target_facts(path):
    """What we can know about a target without opening it for analysis: that it is there, its
    size and modified time, and (for a small file) its sha256. Big files are hashed later."""
    facts = {"path": str(path), **_stat_facts(path)}
    if "size" in facts and facts["size"] < HASH_INLINE_MAX:
        try:
            facts["sha256"] = _sha256(path)
        except OSError:
            pass
    return facts


def create(name, target, kind="executable", fmt="", architecture="", slug=None, entry_point=""):
    """Make a subject. For a file kind, `target` is a full path on this PC (a file or a folder,
    which must be there); for "website", it is an http(s) address. The name defaults to the file
    name or the site's host. Returns the slug."""
    if kind not in KINDS:
        raise SubjectError("The kind is executable, database, archive, artifact or website.", "kind")
    if kind == "website":
        target = (target if isinstance(target, str) else "").strip()
        url = website_url(target)
        if not url:
            raise SubjectError("Give the website's address, starting http:// or https:// "
                               "(without a user name or password in it).", "url")
        options = _binary_options(kind, fmt, architecture, entry_point)
        target_record = {"url": url, "kind": "website"}
    else:
        target = clean_path(target)
        if not target:
            raise SubjectError("Give the target's full path on this PC.", "path")
        if re.match(r"^https?://", target, re.IGNORECASE):
            raise SubjectError("That is a web address. Choose Website as the kind to observe a site.", "path")
        problem = config.local_path_problem(target)   # as text, before the filesystem is touched
        if problem:
            raise SubjectError(problem, "path")
        target = str(Path(target))
        options = _binary_options(kind, fmt, architecture, entry_point)
        facts = target_facts(target)
        if "size" not in facts and not facts.get("folder"):
            raise SubjectError(f"There is no file or folder at {target}. Check the path; "
                               "Explorer's “Copy as path” gives it exactly.", "path")
        target_record = {**facts, "kind": kind, **options}
    name = " ".join((name if isinstance(name, str) else "").split()) or _default_name(kind, target)
    if not name:
        raise SubjectError("Give the subject a name.", "name")
    if len(name) > MAX_NAME:
        raise SubjectError(f"Keep the name under {MAX_NAME} characters.", "name")
    chosen = slug
    problem = slug_problem(chosen) if chosen else ""
    if problem:
        raise SubjectError(problem)
    record = {
        "id": secrets.token_hex(8),
        "name": name,
        "target": target_record,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    for _ in range(20):
        slug = chosen or free_slug(slugify(name))
        with lock_for(slug):
            if _taken(slug):
                if chosen:
                    raise SubjectError(f"A subject already has the link “{slug}”.")
                continue   # another request took this one a moment ago; pick the next
            try:
                write_json(thread_path(slug), [])
                write_json(meta_path(slug), record)
            except OSError as exc:
                remove_tree(folder(slug))
                raise SubjectError(f"The subject could not be saved: {exc}") from exc
            break
    else:
        raise SubjectError("No free link was found for that name; try another name.")
    if "size" in target_record and "sha256" not in target_record:
        _hash_later(slug, record["id"], target_record)
    return slug


def is_website(record):
    return bool(record) and record.get("target", {}).get("kind") == "website"


def open_arguments(slug):
    """The arguments open_binary needs for this subject's target."""
    record = meta(slug)
    if not record:
        raise SubjectError("That subject is no longer there.")
    target = record["target"]
    args = {"path": target.get("path", "")}
    for key in ("format", "architecture", "entry_point"):
        if target.get(key):
            args[key] = target[key]
    return args


# ---- the target, live ----

_hashing = set()
_hash_failed = set()    # (slug, record id, mtime) that could not be read; not retried on every poll
_hashing_guard = threading.Lock()


def _hash_later(slug, record_id, target):
    attempt = (slug, record_id, target.get("mtime"))
    with _hashing_guard:
        if slug in _hashing or attempt in _hash_failed:
            return False
        _hashing.add(slug)

    def work():
        done = False
        try:
            path = target["path"]
            before = _stat_facts(path)
            digest = _sha256(path)
            after = _stat_facts(path)
            if before != after or before.get("size") != target.get("size") or before.get("mtime") != target.get("mtime"):
                return   # it changed while being read; the target card says so, and rehash() starts over
            with lock_for(slug):
                if not exists(slug):
                    return
                record = _meta_strict(slug)
                if not record or record["id"] != record_id or record["target"].get("path") != path:
                    return
                record["target"]["sha256"] = digest
                write_json(meta_path(slug), record)
                done = True
        except (OSError, SubjectError):
            pass
        finally:
            with _hashing_guard:
                _hashing.discard(slug)
                if not done:
                    _hash_failed.add(attempt)

    threading.Thread(target=work, name=f"hash-{slug}", daemon=True).start()
    return True


def hashing(slug):
    """Whether the target's sha256 is being worked out now. A file subject whose hash was never
    finished (the studio closed mid-way) has it started again here."""
    with _hashing_guard:
        if slug in _hashing:
            return True
    record = meta(slug)
    target = (record or {}).get("target", {})
    if not record or is_website(record) or "sha256" in target or "size" not in target:
        return False
    if config.local_path_problem(target.get("path", "")):
        return False
    live = _stat_facts(target["path"])
    if live.get("size") != target.get("size") or live.get("mtime") != target.get("mtime"):
        return False
    return _hash_later(slug, record["id"], target)


def target_exists(slug):
    """Whether the target is there now (a website: always). One stat; a path that is not a local
    path is never stat'ed."""
    record = meta(slug)
    if not record:
        return False
    if is_website(record):
        return True
    path = record["target"].get("path", "")
    if config.local_path_problem(path):
        return False
    return bool(_stat_facts(path))


def target_info(slug):
    """The target as the overview shows it: where it is, whether it is there now, its size, sha256,
    when it was added, the open options, and whether it changed since it was added. None when the
    subject is not there."""
    record = meta(slug)
    if not record:
        return None
    target = record["target"]
    info = {"kind": target.get("kind", "executable"), "created": record.get("created", "")}
    if is_website(record):
        return {**info, "url": target.get("url", ""), "exists": True, "hashing": False, "changed": False}
    path = target.get("path", "")
    live = {} if config.local_path_problem(path) else _stat_facts(path)
    info.update({
        "path": path, "exists": bool(live), "folder": bool(target.get("folder")),
        "size": target.get("size"), "sha256": target.get("sha256", ""),
        "hashing": hashing(slug),
        "changed": bool(live) and "size" in target and (live.get("size"), live.get("mtime")) != (target.get("size"), target.get("mtime")),
    })
    for key in ("format", "architecture", "entry_point"):
        if target.get(key):
            info[key] = target[key]
    return info


def rehash(slug):
    """Read the target's size, time and sha256 again (after it changed on disk)."""
    with lock_for(slug):
        record = _meta_strict(slug) if exists(slug) else None
        if not record:
            raise SubjectError("That subject is no longer there.")
        if is_website(record):
            return
        target = record["target"]
        path = target.get("path", "")
        problem = config.local_path_problem(path)
        if problem:
            raise SubjectError(problem)
        for key in ("size", "mtime", "sha256", "folder"):
            target.pop(key, None)
        target.update(target_facts(path))
        write_json(meta_path(slug), record)
    with _hashing_guard:
        _hash_failed.difference_update({a for a in _hash_failed if a[0] == slug})
    if "size" in target and "sha256" not in target:
        _hash_later(slug, record["id"], target)


# ---- changes ----

def remove(slug):
    """Delete a subject from this PC: its record, conversation, findings, work folder, and the
    observed browser's profile (cookies, logins) of a website subject. Close its engine and
    browser first."""
    from . import evidence

    with lock_for(slug):
        if not exists(slug):
            raise SubjectError("That subject is no longer there.")
        with evidence.lock_for(slug):
            try:
                evidence.remove_all(slug)
            except evidence.EvidenceError:
                pass   # the folder, findings included, is deleted next
            gone = remove_tree(folder(slug))
    profile = web_profile_dir(slug)
    root = config.web_profiles_dir()
    if profile.exists() and profile.resolve().is_relative_to(root.resolve()):
        remove_tree(profile, attempts=10)
    if not gone:
        raise SubjectError("Part of the subject's folder could not be deleted; a program may still have a "
                           f"file in it open. Close it and delete {folder(slug)} by hand.")


def rename(slug, name):
    name = " ".join((name if isinstance(name, str) else "").split())
    if not name:
        raise SubjectError("Give the subject a name.")
    if len(name) > MAX_NAME:
        raise SubjectError(f"Keep the name under {MAX_NAME} characters.")
    with lock_for(slug):
        record = _meta_strict(slug) if exists(slug) else None
        if not record:
            raise SubjectError("That subject is no longer there.")
        record["name"] = name
        write_json(meta_path(slug), record)


# ---- the conversation ----

def thread(slug):
    """The conversation, oldest first. Raises SubjectError when the file is there but cannot be read."""
    if not exists(slug):
        return []
    with lock_for(slug):
        messages = read_json(thread_path(slug), [])
    if not isinstance(messages, list):
        raise SubjectError("conversation.json is not a list of messages; it was left as it is.")
    return messages


def add_message(slug, role, text, **extra):
    """Append one message to the subject's conversation and return it. Refuses (SubjectError)
    rather than overwrite a conversation it could not read."""
    with lock_for(slug):
        if not exists(slug):
            raise SubjectError("That subject is no longer there.")
        messages = thread(slug)
        message = {"id": secrets.token_hex(6), "role": role, "text": text or "",
                   "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **extra}
        messages.append(message)
        write_json(thread_path(slug), messages)
    return message


def clear_thread(slug):
    with lock_for(slug):
        if exists(slug):
            write_json(thread_path(slug), [])
