"""Attachments: any file a studio's owner gives it, kept safely and made readable for Claude.

SHARED. This file is shared, byte for byte, by four studios built alike, each with the same three files:
app/attachments.py (this), static/attach.js and static/attach.css. Change it in one studio, then copy it to all four;
never edit one copy alone. It imports nothing from any studio and names nothing of anyone's: what a studio decides (the
folder, the limits, where its own kinds of file go) is passed in. Python's standard library only.

- save(folder, name, body, limits): one upload kept in `folder`. Its name made safe (the last part only: no folder,
  drive or share; no control or formatting character, none Windows refuses, no name Windows keeps for itself; at most
  MAX_NAME characters, its extension kept, or set right when the bytes say the file is something else, since Claude's
  Read goes by it), never over another file (menu.pdf, then menu-2.pdf), written whole and then swapped in, never run.
  Returns its record: {name, path (in the folder), kind, mime, size, sha256}, with "pages" for a PDF and "text" (the
  path of its readable copy) for a file Claude cannot read itself. Raises Refused, in words, for one it cannot keep.
- sniff(body, name): what a file is, by its bytes first and by its name only after that: picture, pdf, text, office,
  audio, video, archive or other, with its type and extension.
- The readable copy: a Word, Excel or PowerPoint file's words (OpenDocument too; an Excel workbook one block of rows per
  sheet, as CSV), a zip's list of what it holds, a UTF-16 text in UTF-8: kept beside the file in a hidden folder
  (.text/<its name>.txt), so it never stands among the owner's files, and made again by describe() when it is missing
  (a file brought back from a backup comes without it). The XML is read by expat told to refuse a DTD and every entity;
  a zip's parts are unpacked within MAX_PART each and MAX_UNPACKED in all (a zip bomb is refused); the words kept stop
  at MAX_WORDS, and the copy says so where it was cut.
- for_claude(records, base): the lines a run's prompt gets for these files: each one's path as the run can Read it
  (`base` is how the run reaches the folder), what it is, and how to read it.
- headers(record): how a studio serves one back to its own page: a picture or a PDF to view, anything else to
  download; sandboxed with nothing let in, never sniffed. The studio checks the path as text before it resolves it.
"""
import codecs
import csv
import hashlib
import io
import json
import math
import os
import posixpath
import re
import secrets
import threading
import time
import unicodedata
import zipfile
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from xml.parsers import expat

MB = 1024 * 1024
# What a studio allows unless it passes its own: a file, a message's files together, how many in one message.
LIMITS = {"file": 25 * MB, "message": 70 * MB, "count": 12}
TEXT_DIR = ".text"          # the readable copies, beside the files, hidden
MAX_NAME = 120              # characters of a kept name, its extension included
MAX_EXT = 10                # characters of an extension kept as one
MAX_WORDS = 1_000_000       # characters of a readable copy
MAX_PART = 32 * MB          # what one part of an office file may unpack to
MAX_UNPACKED = 96 * MB      # what all the parts read from one file may unpack to
MAX_ENTRIES = 50_000        # entries a zip may hold to be read or listed
MAX_LISTED = 2_000          # entries a zip's list names
MAX_COLUMNS = 512           # columns of a sheet kept
MAX_PDF_STREAMS = 4_000     # compressed object streams of a PDF looked into for its pages
PDF_RANGE = 20              # pages Claude's Read takes at once
PDF_WHOLE = 10              # a PDF of more pages is read in ranges
# How a studio serves a kept file back to its page: sandboxed (an origin of its own, no script, no form), nothing let in.
POLICY = "sandbox; default-src 'none'; img-src data:; media-src data:; style-src 'unsafe-inline'"
# What the studio's page may show as it is: pictures a browser draws, and PDFs (in the browser's own viewer).
VIEWABLE = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/avif", "image/x-icon",
            "application/pdf"}
# The pictures Claude sees when it Reads them.
SEEN = {"image/png", "image/jpeg", "image/gif", "image/webp"}

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_ODT = "application/vnd.oasis.opendocument.text"
_ODS = "application/vnd.oasis.opendocument.spreadsheet"
_ODP = "application/vnd.oasis.opendocument.presentation"
# Each type the bytes tell: its kind, the extension a file of it is kept under, the others it may already have, and
# what it is, in words.
TYPES = {
    "image/png": ("picture", "png", (), "a PNG picture"),
    "image/jpeg": ("picture", "jpg", ("jpeg", "jpe", "jfif"), "a JPEG picture"),
    "image/gif": ("picture", "gif", (), "a GIF picture"),
    "image/webp": ("picture", "webp", (), "a WebP picture"),
    "image/bmp": ("picture", "bmp", ("dib",), "a BMP picture"),
    "image/tiff": ("picture", "tif", ("tiff", "dng", "nef", "cr2", "arw"), "a TIFF picture"),
    "image/heic": ("picture", "heic", ("heif", "hif"), "a HEIC picture, as an iPhone takes them"),
    "image/avif": ("picture", "avif", (), "an AVIF picture"),
    "image/x-icon": ("picture", "ico", ("cur",), "an icon"),
    "application/pdf": ("pdf", "pdf", (), "a PDF"),
    _DOCX: ("office", "docx", ("docm", "dotx", "dotm"), "a Word document"),
    _XLSX: ("office", "xlsx", ("xlsm", "xltx", "xltm"), "an Excel workbook"),
    _PPTX: ("office", "pptx", ("pptm", "potx", "potm", "ppsx", "ppsm"), "a PowerPoint presentation"),
    _ODT: ("office", "odt", ("ott",), "an OpenDocument text"),
    _ODS: ("office", "ods", ("ots",), "an OpenDocument spreadsheet"),
    _ODP: ("office", "odp", ("otp",), "an OpenDocument presentation"),
    "application/msword": ("office", "doc", ("dot",), "an older .doc Word document"),
    "application/vnd.ms-excel": ("office", "xls", ("xlt",), "an older .xls Excel workbook"),
    "application/vnd.ms-powerpoint": ("office", "ppt", ("pps", "pot"), "an older .ppt PowerPoint presentation"),
    "audio/mpeg": ("audio", "mp3", (), "an MP3 sound"),
    "audio/mp4": ("audio", "m4a", ("m4b", "m4p", "m4r"), "an M4A sound"),
    "audio/aac": ("audio", "aac", (), "an AAC sound"),
    "audio/wav": ("audio", "wav", ("wave",), "a WAV sound"),
    "audio/ogg": ("audio", "ogg", ("oga", "opus", "spx"), "an Ogg sound"),
    "audio/flac": ("audio", "flac", (), "a FLAC sound"),
    "audio/amr": ("audio", "amr", (), "an AMR voice note"),
    "audio/aiff": ("audio", "aiff", ("aif", "aifc"), "an AIFF sound"),
    "audio/midi": ("audio", "mid", ("midi",), "MIDI music"),
    "audio/x-ms-wma": ("audio", "wma", (), "a WMA sound"),
    "video/mp4": ("video", "mp4", ("m4v",), "an MP4 video"),
    "video/quicktime": ("video", "mov", ("qt",), "a QuickTime .mov video"),
    "video/3gpp": ("video", "3gp", ("3g2", "3gpp"), "a 3GP video"),
    "video/webm": ("video", "webm", (), "a WebM video"),
    "video/x-matroska": ("video", "mkv", ("mk3d",), "an MKV video"),
    "video/x-msvideo": ("video", "avi", (), "an AVI video"),
    "video/x-ms-wmv": ("video", "wmv", ("asf",), "a WMV video"),
    "video/x-flv": ("video", "flv", (), "an FLV video"),
    "video/mpeg": ("video", "mpg", ("mpeg", "vob", "m2v"), "an MPEG video"),
    "video/mp2t": ("video", "ts", ("mts", "m2ts"), "an MPEG-TS video"),
    "video/ogg": ("video", "ogv", (), "an Ogg video"),
    "application/zip": ("archive", "zip", ("jar", "apk", "epub", "xpi", "whl", "nupkg", "vsix", "kmz"), "a zip archive"),
    "application/x-7z-compressed": ("archive", "7z", (), "a 7-Zip archive"),
    "application/vnd.rar": ("archive", "rar", (), "a RAR archive"),
    "application/gzip": ("archive", "gz", ("tgz", "gzip"), "a gzip archive"),
    "application/x-bzip2": ("archive", "bz2", ("tbz", "tbz2"), "a bzip2 archive"),
    "application/x-xz": ("archive", "xz", ("txz",), "an xz archive"),
    "application/x-tar": ("archive", "tar", (), "a tar archive"),
    "application/zstd": ("archive", "zst", ("zstd",), "a Zstandard archive"),
    "application/x-msdownload": ("other", "exe", ("dll", "scr", "sys", "com", "cpl", "efi"), "a Windows program"),
    "application/x-executable": ("other", "elf", ("so", "bin", "run"), "a Linux program"),
    "application/x-mach-binary": ("other", "macho", ("dylib",), "a Mac program"),
    "application/x-ole-storage": ("other", "ole", ("msi", "msg"), "an older Microsoft file"),
    "application/octet-stream": ("other", "", (), "a file"),
    # text: what decodes as UTF-8 (or UTF-16 with its mark)
    "text/plain": ("text", "txt", (), "text"),
    "text/markdown": ("text", "md", ("markdown", "mdown"), "Markdown text"),
    "text/csv": ("text", "csv", (), "a CSV table"),
    "text/tab-separated-values": ("text", "tsv", ("tab",), "a TSV table"),
    "application/json": ("text", "json", ("jsonl", "ndjson", "geojson", "ipynb", "har"), "JSON data"),
    "application/xml": ("text", "xml", ("xsd", "xsl", "xslt", "plist", "kml", "gpx", "rss", "atom"), "XML data"),
    "application/yaml": ("text", "yaml", ("yml",), "YAML data"),
    "text/html": ("text", "html", ("htm", "xhtml"), "an HTML page's code"),
    "image/svg+xml": ("text", "svg", (), "an SVG drawing's code"),
    "application/rtf": ("text", "rtf", (), "RTF rich text"),
    "text/calendar": ("text", "ics", (), "an iCalendar calendar"),
    "text/vcard": ("text", "vcf", ("vcard",), "a vCard contact card"),
}
# Text by its name, when its words do not say more: code, logs and settings are text to read.
_TEXT_NAMES = {**{ext: mime for mime, (kind, ext0, also, _) in TYPES.items() if kind == "text"
                  for ext in (ext0, *also)},
               **{ext: "text/plain" for ext in (
                   "txt", "text", "log", "ini", "cfg", "conf", "toml", "env", "properties", "py", "js", "mjs", "cjs", "ts",
                   "tsx", "jsx", "css", "scss", "less", "sql", "sh", "bat", "cmd", "ps1", "c", "h", "cpp", "hpp", "cs",
                   "java", "kt", "go", "rs", "rb", "php", "swift", "r", "m", "lua", "pl", "dart", "vue", "srt", "vtt",
                   "tex", "bib", "diff", "patch", "gitignore", "dockerfile", "makefile", "readme")}}
_TEXT_NAMES["md"] = "text/markdown"
# Extensions that say the file is not text: a text file named so is kept under its text's own extension.
_NOT_TEXT = {ext for mime, (kind, ext0, also, _) in TYPES.items() if kind != "text" for ext in (ext0, *also) if ext}
# Names Windows keeps for itself, whatever follows the first dot.
_RESERVED = ({"con", "prn", "aux", "nul", "conin$", "conout$", "clock$"}
             | {f"{p}{n}" for p in ("com", "lpt") for n in "0123456789¹²³"})
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ASF = b"\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c"
_HEIF = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"}

_lock = threading.Lock()   # choosing a free name and putting the file there, one save at a time
_known = {}                # a file's path -> ((its size, its time), its record): describe() reads a file once


class Refused(ValueError):
    """A file that cannot be kept, in the owner's words."""


def size_words(n):
    """A size as the owner reads it: 820 bytes, 12 KB, 1.2 MB."""
    if n < 1024:
        return f"{n} bytes" if n != 1 else "1 byte"
    if n < MB:
        return f"{max(1, round(n / 1024))} KB"
    value = f"{math.ceil(n * 10 / MB) / 10:.1f}"   # rounded up: a file just past a limit never reads as within it
    return f"{value[:-2] if value.endswith('.0') else value} MB"


# ---- names ----

def safe_name(name, fallback="file"):
    """A name a file can be kept under on any PC (see the module's words): `fallback` when nothing of it is left."""
    text = unicodedata.normalize("NFC", str(name or ""))
    text = re.split(r"[\\/]", text)[-1]                     # the last part: no folder, no drive's root, no share
    text = re.sub(r"^[A-Za-z]:", "", text)                  # a drive's own folder: C:menu.pdf
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf", "Cs"))
    text = re.sub(r'[<>:"|?*]', "-", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    stem, ext = _split(text)
    if stem.split(".")[0].strip().lower() in _RESERVED:
        stem = f"_{stem}"
    stem = stem[:MAX_NAME - len(ext) - 1].rstrip(" .") if ext else stem[:MAX_NAME].rstrip(" .")
    if not stem:
        stem = fallback
    return f"{stem}.{ext}" if ext else stem


def _split(name):
    """(stem, extension in small letters) of a name; an extension is 1 to MAX_EXT letters or digits after the last dot."""
    stem, dot, ext = name.rpartition(".")
    if dot and stem.strip(" .") and 0 < len(ext) <= MAX_EXT and ext.isalnum():
        return stem.rstrip(" ."), ext.lower()
    return name, ""


def _named(name, found):
    """The name a file is kept under: its own, with its extension set right when the bytes say it is another kind of
    file (Claude's Read goes by the extension: a PDF called menu.txt would be read as text)."""
    stem, ext = _split(name)
    want = found["ext"]
    if found["kind"] == "text":
        if ext in _NOT_TEXT:
            return f"{stem}.{want}"
        return name
    if not want or found["kind"] == "other":
        return name
    _, keep, also, _ = TYPES[found["mime"]]
    if ext in (keep, *also):
        return name
    return f"{stem if ext else name}.{want}"


def _free(folder, name):
    """`name`, or name-2, name-3… : one no file in `folder` has, whatever the letters' case (Windows does not tell
    Menu.pdf from menu.pdf)."""
    try:
        taken = {p.name.lower() for p in folder.iterdir()}
    except OSError:
        taken = set()
    stem, ext = _split(name)
    candidate, n = name, 2
    while candidate.lower() in taken:
        tail = f"-{n}.{ext}" if ext else f"-{n}"
        candidate, n = f"{stem[:MAX_NAME - len(tail)]}{tail}", n + 1
    return candidate


# ---- what a file is ----

def _found(mime, encoding=""):
    kind, ext, _, words = TYPES[mime]
    return {"kind": kind, "mime": mime, "ext": ext, "words": words, "encoding": encoding}


def sniff(body, name=""):
    """What a file is: {kind, mime, ext (the extension it is kept under), words (what it is, said plainly), encoding (a
    text's: utf-8, utf-16 or cp1252)}. By its bytes first: a PDF; then what reads as text (no signature of a binary kind
    is then trusted over its words: a note that starts "MZ" or "ID3" is a note); then the binary kinds' signatures. Its
    name only where the bytes leave a choice (an MP4 holding a sound, an older Office file, an old Windows text)."""
    raw = bytes(body)
    head = raw[:4096]
    ext = _split(safe_name(name))[1]
    if b"%PDF-" in head[:1024]:
        return _found("application/pdf")
    text, encoding = _text(raw, ext)
    if text is not None:
        return _found(_text_type(text, ext), encoding)
    mime = _magic(head, ext) or "application/octet-stream"
    if mime == "application/zip":
        mime = _zip_type(raw)
    return _found(mime)


def _magic(head, ext):
    """The type a binary file's first bytes say, or None."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and len(head) >= 12:
        return {b"WEBP": "image/webp", b"WAVE": "audio/wav", b"AVI ": "video/x-msvideo"}.get(head[8:12])
    if head[:2] == b"BM" and len(head) >= 18 and int.from_bytes(head[14:18], "little") in (12, 40, 52, 56, 64, 108, 124):
        return "image/bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if head[:4] == b"\x00\x00\x01\x00" and len(head) >= 6 and 0 < int.from_bytes(head[4:6], "little") < 256:
        return "image/x-icon"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in _HEIF:
            return "image/heic"
        if brand in (b"avif", b"avis"):
            return "image/avif"
        if brand in (b"M4A ", b"M4B ", b"M4P ", b"F4A ", b"F4B "):
            return "audio/mp4"
        if brand == b"qt  ":
            return "video/quicktime"
        if brand.startswith((b"3gp", b"3g2")):
            return "video/3gpp"
        return "audio/mp4" if ext in ("m4a", "m4b", "m4p", "m4r", "aac") else "video/mp4"
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "application/zip"
    if head[:8] == _OLE:
        return {"doc": "application/msword", "dot": "application/msword", "xls": "application/vnd.ms-excel",
                "xlt": "application/vnd.ms-excel", "ppt": "application/vnd.ms-powerpoint",
                "pps": "application/vnd.ms-powerpoint", "pot": "application/vnd.ms-powerpoint"}.get(ext, "application/x-ole-storage")
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm" if b"webm" in head[:64] else "video/x-matroska"
    if head[:4] == b"OggS":
        return "video/ogg" if b"\x80theora" in head else "audio/ogg"
    if head[:4] == b"fLaC":
        return "audio/flac"
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa", b"\xff\xe3"):
        return "audio/mpeg"
    if head[:2] in (b"\xff\xf1", b"\xff\xf9"):
        return "audio/aac"
    if head[:6] == b"#!AMR\n":
        return "audio/amr"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "audio/aiff"
    if head[:4] == b"MThd":
        return "audio/midi"
    if head[:16] == _ASF:
        return "audio/x-ms-wma" if ext == "wma" else "video/x-ms-wmv"
    if head[:4] == b"FLV\x01":
        return "video/x-flv"
    if head[:4] in (b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"):
        return "video/mpeg"
    if len(head) > 376 and head[0] == head[188] == head[376] == 0x47:
        return "video/mp2t"
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return "application/x-7z-compressed"
    if head[:6] == b"Rar!\x1a\x07":   # RAR 4 and 5
        return "application/vnd.rar"
    if head[:2] == b"\x1f\x8b":
        return "application/gzip"
    if head[:3] == b"BZh" and head[3:4].isdigit():
        return "application/x-bzip2"
    if head[:6] == b"\xfd7zXZ\x00":
        return "application/x-xz"
    if head[:4] == b"\x28\xb5\x2f\xfd":
        return "application/zstd"
    if len(head) > 262 and head[257:262] == b"ustar":
        return "application/x-tar"
    if head[:2] == b"MZ":
        return "application/x-msdownload"
    if head[:4] == b"\x7fELF":
        return "application/x-executable"
    if head[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
        return "application/x-mach-binary"
    return None


def _zip_type(body):
    """A zip's own type: a Word, Excel or PowerPoint file (or an OpenDocument) when its parts say so, else a zip."""
    if _entries(body) > MAX_ENTRIES:
        return "application/zip"
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            names = set(archive.namelist())
            main = _ooxml_main(archive)
            if main:
                return main[0]
            if "mimetype" in names:
                info = archive.getinfo("mimetype")
                if info.file_size <= 100:
                    said = archive.read(info).decode("ascii", "replace").strip()
                    if said in (_ODT, _ODS, _ODP):
                        return said
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError, KeyError, NotImplementedError, EOFError, zlib.error,
            expat.ExpatError, _Unsafe):
        pass
    return "application/zip"


def _text_type(text, ext):
    """The type of a text: by its name when that names a kind of text; else by its first words (RTF, an SVG drawing,
    an HTML page, XML, JSON); else plain text."""
    if ext in _TEXT_NAMES and ext not in ("txt", "text", "log"):
        return _TEXT_NAMES[ext]
    start = text.lstrip("﻿ \t\r\n")[:2048].lower()
    if start.startswith("{\\rtf"):
        return "application/rtf"
    if "<svg" in start[:1024] and start.startswith(("<svg", "<?xml", "<!--")):
        return "image/svg+xml"
    if start.startswith(("<!doctype html", "<html")):
        return "text/html"
    if start.startswith("<?xml"):
        return "application/xml"
    if start[:1] in ("{", "[") and len(text) <= 5 * MB:
        try:
            json.loads(text)
            return "application/json"
        except ValueError:
            pass
    return _TEXT_NAMES.get(ext, "text/plain")


def _text(raw, ext=""):
    """(its words, how they were written) when a file is text: UTF-8 (its mark allowed), UTF-16 with its mark, or, for a
    file named as text (a .csv, a .txt), the Windows code page Excel and Notepad save in. No NUL, no control character
    a text has no use for. (None, "") for anything else."""
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            text = raw.decode("utf-16")
        except UnicodeDecodeError:
            return None, ""
        return (text, "utf-16") if "\x00" not in text else (None, "")
    if b"\x00" in raw:
        return None, ""
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    if ext in _TEXT_NAMES:
        try:
            text = raw.decode("cp1252")
        except UnicodeDecodeError:
            return None, ""
        if not re.search(r"[\x00-\x08\x0b\x0e-\x1f\x7f]", text):
            return text, "cp1252"
    return None, ""


# ---- keeping one ----

def save(folder, name, body, limits=None):
    """Keep one upload in `folder` (see the module's words). Returns its record; raises Refused."""
    limits = {**LIMITS, **(limits or {})}
    label = safe_name(name)
    body = bytes(body)
    if not body:
        raise Refused(f"{label} is empty: there is nothing in it to attach.")
    if len(body) > limits["file"]:
        raise Refused(f"{label} is {size_words(len(body))}: a file can be up to {size_words(limits['file'])}.")
    found = sniff(body, label)
    if found["kind"] == "other":
        claimed = _claimed(_split(label)[1])
        if claimed:   # its name says a kind whose bytes always say so, and they say nothing: a damaged file
            raise Refused(f"{label} isn’t the {claimed} it says it is: it may be damaged. Attach it again.")
    kept = _named(label, found)
    words = _readable(body, found, kept)   # before anything is written: one that cannot be read is not kept
    folder = Path(folder)
    with _lock:
        folder.mkdir(parents=True, exist_ok=True)
        final = _free(folder, kept)
        _write(folder / final, body)
        if words is not None:
            _write(folder / TEXT_DIR / f"{final}.txt", _headed(words, final, found).encode("utf-8"))
    record = _record(final, found, body)
    path = folder / final
    _known[str(path)] = (_stamp(path), dict(record))
    return record


def _claimed(ext):
    """What an extension says a file is, said plainly ("picture", "PDF"…), when it names a kind whose bytes always show
    it (a picture, a PDF, an office file, a sound, a video, an archive); "" for any other, or none."""
    for kind, keep, also, words in TYPES.values():
        if kind in ("picture", "pdf", "office", "audio", "video", "archive") and ext and ext in (keep, *also):
            return "picture" if kind == "picture" else re.sub(r"^an? ", "", words.split(",")[0])
    return ""


def _record(relative, found, body):
    record = {"name": posixpath.basename(relative), "path": relative, "kind": found["kind"], "mime": found["mime"],
              "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    if found["mime"] == "application/pdf":
        record["pages"] = pdf_pages(body)
    if _has_copy(found):
        record["text"] = copy_path(relative)
    return record


def _has_copy(found):
    return (found["mime"] in _READERS or found["mime"] == "application/zip"
            or (found["kind"] == "text" and found["encoding"] != "utf-8"))


def copy_path(relative):
    """Where the readable copy of a kept file is (whether or not it has one): .text/<its name>.txt beside it."""
    head, tail = posixpath.split(relative)
    return posixpath.join(head, TEXT_DIR, f"{tail}.txt")


def describe(folder, relative):
    """The record of a file already kept under `folder` (`relative`: its path there, as save gave it or as the studio
    keeps it), or None when there is none. Read once while it stays the same; its readable copy made again when it is
    missing or older than the file. `relative` is the studio's own, never a request's: the studio checks those first."""
    folder = Path(folder)
    path = folder / relative
    try:
        if not path.is_file():
            return None
        stamp = _stamp(path)
    except OSError:
        return None
    relative = Path(relative).as_posix()
    known = _known.get(str(path))
    if known and known[0] == stamp:   # what it is, as last read: its paths as this caller names them
        record = {**known[1], "path": relative}
        if "text" in record:
            record["text"] = copy_path(relative)
        if "text" not in record or (folder / record["text"]).is_file():
            return record
    try:
        body = _read(path)
    except OSError:
        return None
    found = sniff(body, path.name)
    record = _record(relative, found, body)
    if "text" in record and not _fresh(folder / record["text"], stamp):
        try:
            words = _readable(body, found, path.name)
            with _lock:
                _write(folder / record["text"], _headed(words, path.name, found).encode("utf-8"))
        except (Refused, OSError, TypeError):
            record.pop("text")
    _known[str(path)] = (stamp, dict(record))
    return record


def _fresh(copy, stamp):
    try:
        return copy.is_file() and copy.stat().st_mtime_ns >= stamp[1]
    except OSError:
        return False


def _stamp(path):
    info = path.stat()
    return info.st_size, info.st_mtime_ns


def text_of(folder, record, most=200 * 1024):
    """What a kept file says, for the studio to read: a text file's words, or its readable copy's; "" for one that has
    neither, or whose words pass `most` bytes."""
    if record is None:
        return ""
    relative = record.get("text") or (record["path"] if record["kind"] == "text" else None)
    if not relative:
        return ""
    path = Path(folder) / relative
    try:
        if path.stat().st_size > most:
            return ""
        return _text(_read(path), "txt")[0] or ""
    except OSError:
        return ""


def _write(path, body):
    """The whole file under another (hidden) name, then swapped in: a reader never meets half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    draft = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with open(draft, "wb") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        _again(lambda: os.replace(draft, path))
    finally:
        try:
            draft.unlink()
        except OSError:
            pass


def _read(path):
    return _again(lambda: Path(path).read_bytes())


def _again(work, tries=10):
    """work(), tried again while Windows says the file is busy (another program reading it as it is swapped)."""
    for attempt in range(tries):
        try:
            return work()
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(min(0.1, 0.02 * 1.5 ** attempt))
    return None


# ---- what Claude is told ----

def for_claude(records, base=""):
    """The lines a run's prompt gets for these files: "- <path as the run Reads it> (<what it is>): <how to read it>", or
    "- <path>: can't be opened here: <what it is, its size>." `base` is the folder as the run reaches it ("" when it is
    the run's own folder)."""
    lines = []
    for record in records:
        if record:
            path = _joined(base, record["path"])
            what = what_it_is(record)
            how = _how(record, base)
            lines.append(f"- {path}: {how}" if how.startswith("can't") else f"- {path} ({what}): {how}")
    return lines


def _joined(base, relative):
    return f"{str(base).rstrip('/')}/{relative}" if base else relative


def what_it_is(record):
    """What a kept file is, in words, with its size: "a PDF, 12 pages, 1.2 MB"."""
    words = TYPES.get(record.get("mime"), TYPES["application/octet-stream"])[3]
    parts = [words]
    if record.get("pages"):
        parts.append(f"{record['pages']} {'page' if record['pages'] == 1 else 'pages'}")
    parts.append(size_words(record.get("size") or 0))
    return ", ".join(parts)


def _how(record, base):
    kind, mime = record["kind"], record["mime"]
    if record.get("text"):
        copy = _joined(base, record["text"])
        if mime == "application/zip":
            return f"Read {copy}, the list of what it holds; the files inside can't be opened here."
        if kind == "text":
            return f"Read {copy}, its words in UTF-8."
        sheets = " (one block of rows per sheet, as CSV)" if mime in (_XLSX, _ODS) else ""
        return f"Read {copy}, its words{sheets}."
    if kind == "pdf":
        pages = record.get("pages")
        if pages and pages <= PDF_WHOLE:
            return "Read it."
        if pages:
            return (f"{pages} pages: read it in ranges of up to {PDF_RANGE} (Read with pages \"1-{PDF_RANGE}\", then "
                    f"\"{PDF_RANGE + 1}-{2 * PDF_RANGE}\", and so on).")
        return f"its page count could not be read: read it in ranges of up to {PDF_RANGE} pages."
    if kind == "text" or mime in SEEN:
        return "Read it."
    return f"can't be opened here: {what_it_is(record)}."


# ---- how a studio serves one ----

def headers(record):
    """The headers a studio serves a kept file with: a picture or a PDF to view in the browser, anything else as a
    download; always sandboxed with nothing let in, and never sniffed."""
    view = record["mime"] in VIEWABLE
    return {"Content-Type": record["mime"] if view else "application/octet-stream",
            "Content-Disposition": f"{'inline' if view else 'attachment'}; {_filename(record['name'])}",
            "Content-Security-Policy": POLICY, "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "same-origin", "Referrer-Policy": "no-referrer"}


def _filename(name):
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    plain = re.sub(r'[^A-Za-z0-9 ._()\-]', "_", plain).strip() or "file"
    return f'filename="{plain}"; filename*=UTF-8\'\'{quote(name, safe="")}'


# ---- the readable copy ----

class _Full(Exception):
    """The readable copy is as long as it may be."""


class _Unsafe(Exception):
    """A part that is not safe to read: a DTD or an entity, or more than it may unpack to."""


class _Lines:
    """The readable copy as it is written: lines, at most `most` characters in all."""

    def __init__(self, most=None):
        self.lines, self.size, self.most, self.cut = [], 0, MAX_WORDS if most is None else most, False

    def add(self, line):
        if self.size + len(line) + 1 > self.most:
            self.cut = True
            raise _Full
        self.lines.append(line)
        self.size += len(line) + 1

    def text(self):
        out = "\n".join(self.lines)
        if self.cut:
            out += f"\n\n[Cut here: the words go on past {self.most:,} characters; the rest is not in this copy.]"
        return out


def _headed(words, name, found):
    """The readable copy as it is kept: what it is first (a UTF-16 text's copy is its words alone)."""
    if found["kind"] == "text":
        return words
    if found["mime"] == "application/zip":
        return f"What {name} holds ({found['words']}):\n\n{words}\n"
    return f"The words of {name} ({found['words']}), as plain text:\n\n{words}\n"


def _readable(body, found, name):
    """The words of a file Claude cannot read itself (an office file, a zip's list, a UTF-16 text), or None for one it
    can, or that has none. Raises Refused for an office file or a zip that cannot be read, or not safely."""
    mime = found["mime"]
    if found["kind"] == "text":
        return _text(body, "txt")[0] if found["encoding"] != "utf-8" else None
    reader = _READERS.get(mime) or (_zip_list if mime == "application/zip" else None)
    if reader is None:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            if _entries(body) > MAX_ENTRIES:
                raise _Unsafe(f"it holds more than {MAX_ENTRIES:,} files")
            return reader(archive, _Budget())
    except _Unsafe as exc:
        raise Refused(f"{name} can't be read safely: {exc}.") from None
    except RuntimeError as exc:   # zipfile: a part locked with a password
        raise Refused(f"{name} can't be read: {'it is locked with a password' if 'encrypted' in str(exc) else exc}.") from None
    except (zipfile.BadZipFile, expat.ExpatError, KeyError, ValueError, OSError, EOFError, NotImplementedError,
            zlib.error, IndexError):
        raise Refused(f"{name} can't be read as {found['words']}: it may be damaged. Attach it again, or as a PDF.") from None


def _entries(body):
    """How many entries a zip says it holds, from its end record (before Python reads the whole list of them)."""
    tail = bytes(body[-(65536 + 22):])
    at = tail.rfind(b"PK\x05\x06")
    if at < 0 or at + 22 > len(tail):
        return 0
    count = int.from_bytes(tail[at + 10:at + 12], "little")
    if count == 0xFFFF:   # zip64: its own end record says
        found = tail.rfind(b"PK\x06\x06")
        if found >= 0 and found + 40 <= len(tail):
            count = int.from_bytes(tail[found + 32:found + 40], "little")
    return count


class _Budget:
    """What one file's parts may unpack to, in all."""

    def __init__(self):
        self.left = MAX_UNPACKED

    def spend(self, n):
        self.left -= n
        if self.left < 0:
            raise _Unsafe(f"it unpacks to more than {size_words(MAX_UNPACKED)}")


def _xml(archive, part, budget, start=None, end=None, text=None):
    """Read one XML part of a zip with expat, element by element (names without their namespace, attributes as expat
    gives them: "namespace name"), refusing a DTD and every entity, and within MAX_PART and the file's budget."""
    info = archive.getinfo(part)
    if info.file_size > MAX_PART:
        raise _Unsafe(f"a part of it unpacks to {size_words(info.file_size)}")
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.buffer_text = True

    def refuse(*_):
        raise _Unsafe("it declares a DTD or an entity")

    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.UnparsedEntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = refuse
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    if start:
        parser.StartElementHandler = lambda name, attrs: start(name.rpartition(" ")[2], attrs)
    if end:
        parser.EndElementHandler = lambda name: end(name.rpartition(" ")[2])
    if text:
        parser.CharacterDataHandler = text
    read = 0
    with archive.open(info) as stream:
        while True:
            chunk = stream.read(1 << 16)
            if not chunk:
                break
            read += len(chunk)
            budget.spend(len(chunk))
            if read > MAX_PART:
                raise _Unsafe(f"a part of it unpacks to more than {size_words(MAX_PART)}")
            parser.Parse(chunk, False)
    parser.Parse(b"", True)


def _attr(attrs, local, space=None):
    """An attribute by its own name: one in no namespace, or (with `space`) one whose namespace holds those words."""
    for key, value in attrs.items():
        uri, _, name = key.rpartition(" ")
        if name == local and (space in uri if space else not uri):
            return value
    return None


def _rels(archive, part, budget):
    """{relationship id: (its type's last word, the part it points to)} of a part of an OOXML package."""
    head, tail = posixpath.split(part)
    rels = posixpath.join(head, "_rels", f"{tail}.rels")
    out = {}
    if rels not in archive.namelist():
        return out

    def start(name, attrs):
        if name == "Relationship" and attrs.get("TargetMode") != "External":
            target = attrs.get("Target") or ""
            path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(head, target))
            out[attrs.get("Id")] = ((attrs.get("Type") or "").rpartition("/")[2], path)

    _xml(archive, rels, budget, start=start)
    return out


def _ooxml_main(archive):
    """(the type, the main part) of a Word, Excel or PowerPoint package, from its [Content_Types].xml; None."""
    found = []
    kinds = (("wordprocessingml.document.main", _DOCX), ("wordprocessingml.template.main", _DOCX),
             ("document.macroEnabled.main", _DOCX), ("template.macroEnabledTemplate.main", _DOCX),
             ("spreadsheetml.sheet.main", _XLSX), ("spreadsheetml.template.main", _XLSX), ("sheet.macroEnabled.main", _XLSX),
             ("presentationml.presentation.main", _PPTX), ("presentationml.slideshow.main", _PPTX),
             ("presentationml.template.main", _PPTX), ("presentation.macroEnabled.main", _PPTX),
             ("slideshow.macroEnabled.main", _PPTX))

    def start(name, attrs):
        if name == "Override" and not found:
            said = attrs.get("ContentType") or ""
            for words, mime in kinds:
                if words in said:
                    found.append((mime, (attrs.get("PartName") or "").lstrip("/")))
                    return

    names = archive.namelist()
    if "[Content_Types].xml" in names:
        _xml(archive, "[Content_Types].xml", _Budget(), start=start)
        if found and found[0][1] in names:
            return found[0]
    # no word of it there: where Word, Excel and PowerPoint keep it
    for mime, part in ((_DOCX, "word/document.xml"), (_XLSX, "xl/workbook.xml"), (_PPTX, "ppt/presentation.xml")):
        if part in names:
            return mime, part
    return None


_SKIPPED = {"pPr", "rPr", "tblPr", "trPr", "tcPr", "sectPr", "tblGrid", "delText", "instrText", "rPh", "endParaRPr",
            "lstStyle", "bodyPr", "tabLst"}


class _Paragraphs:
    """The words of WordprocessingML or DrawingML (a Word document, a slide, its notes): a line a paragraph, a table's
    row a line with " | " between its cells; nothing of properties, deleted text or field codes."""

    def __init__(self, out, skip=()):
        self.out, self.skip_names, self.skip = out, _SKIPPED | set(skip), 0
        self.para, self.in_t, self.tables = None, 0, []

    def start(self, name, attrs):
        if self.skip or name in self.skip_names:
            self.skip += 1
            return
        if name == "p":
            self.para = []
        elif name == "t":
            self.in_t += 1
        elif name == "tab" and self.para is not None:
            self.para.append("\t")
        elif name in ("br", "cr") and self.para is not None:
            self.para.append("\n")
        elif name == "tbl":
            self.tables.append({"row": None, "cell": None})
        elif name == "tr" and self.tables:
            self.tables[-1]["row"] = []
        elif name == "tc" and self.tables:
            self.tables[-1]["cell"] = []

    def end(self, name):
        if self.skip:
            self.skip -= 1
            return
        if name == "t":
            self.in_t = max(0, self.in_t - 1)
        elif name == "p" and self.para is not None:
            line = "".join(self.para).strip()
            self.para = None
            table = self.tables[-1] if self.tables else None
            if table and table["cell"] is not None:
                if line:
                    table["cell"].append(line)
            elif line:
                self.out.add(line)
        elif name == "tc" and self.tables and self.tables[-1]["cell"] is not None:
            table = self.tables[-1]
            if table["row"] is not None:
                table["row"].append(" / ".join(table["cell"]))
            table["cell"] = None
        elif name == "tr" and self.tables and self.tables[-1]["row"] is not None:
            row = self.tables[-1]["row"]
            self.tables[-1]["row"] = None
            if any(cell for cell in row):
                self.out.add(" | ".join(row))
        elif name == "tbl" and self.tables:
            self.tables.pop()

    def text(self, data):
        if self.in_t and not self.skip and self.para is not None:
            self.para.append(data)


def _word(archive, budget):
    out = _Lines()
    main = _ooxml_main(archive)[1]
    try:
        reader = _Paragraphs(out)
        _xml(archive, main, budget, reader.start, reader.end, reader.text)
        extra = sorted(n for n in archive.namelist() if re.fullmatch(r"word/(header|footer)\d*\.xml", n))
        seen = set(out.lines)
        if extra:
            more = _Lines()
            for part in extra:
                _xml(archive, part, budget, *_handlers(_Paragraphs(more)))
            lines = [line for line in dict.fromkeys(more.lines) if line not in seen]
            if lines:
                out.add("")
                out.add("In its headers and footers:")
                for line in lines:
                    out.add(line)
    except _Full:
        pass
    return out.text()


def _handlers(reader):
    return reader.start, reader.end, reader.text


def _slides(archive, budget):
    out = _Lines()
    main = _ooxml_main(archive)[1]
    rels = _rels(archive, main, budget)
    order = []

    def start(name, attrs):
        if name == "sldId":
            order.append(_attr(attrs, "id", "relationships"))

    _xml(archive, main, budget, start=start)
    try:
        for n, rid in enumerate(order, 1):
            kind, part = rels.get(rid, ("", ""))
            if kind != "slide" or part not in archive.namelist():
                continue
            out.add(f"Slide {n}:")
            _xml(archive, part, budget, *_handlers(_Paragraphs(out)))
            notes = [p for k, p in _rels(archive, part, budget).values() if k == "notesSlide" and p in archive.namelist()]
            for note in notes[:1]:
                said = _Lines()
                _xml(archive, note, budget, *_handlers(_Paragraphs(said, skip=("fld",))))
                if said.lines:
                    out.add("Notes: " + " ".join(said.lines))
            out.add("")
    except _Full:
        pass
    return out.text().rstrip()


def _column(ref):
    """A cell's column from its reference (A1 -> 0, AB7 -> 27), or None."""
    letters = re.match(r"[A-Z]+", ref or "")
    if not letters:
        return None
    n = 0
    for ch in letters.group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _csv(cells):
    line = io.StringIO()
    csv.writer(line, lineterminator="").writerow(cells)
    return line.getvalue()


_DATE_FORMATS = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))


def _sheets(archive, budget):
    out = _Lines()
    main = _ooxml_main(archive)[1]
    rels = _rels(archive, main, budget)
    sheets, flags = [], {"1904": False}

    def start_book(name, attrs):
        if name == "sheet":
            sheets.append((attrs.get("name") or "", _attr(attrs, "id", "relationships"), attrs.get("state") or ""))
        elif name == "workbookPr" and attrs.get("date1904") in ("1", "true"):
            flags["1904"] = True

    _xml(archive, main, budget, start=start_book)
    shared = _shared_strings(archive, rels, budget)
    dates = _date_styles(archive, rels, budget)
    base = datetime(1904, 1, 1) if flags["1904"] else datetime(1899, 12, 30)
    try:
        for name, rid, state in sheets:
            kind, part = rels.get(rid, ("", ""))
            if kind != "worksheet" or part not in archive.namelist():
                continue
            out.add(f"Sheet “{name}”{' (hidden)' if state in ('hidden', 'veryHidden') else ''}:")
            _xml(archive, part, budget, *_handlers(_SheetRows(out, shared, dates, base)))
            out.add("")
    except _Full:
        pass
    return out.text().rstrip()


def _shared_strings(archive, rels, budget):
    part = next((p for k, p in rels.values() if k == "sharedStrings"), "xl/sharedStrings.xml")
    strings = []
    if not part or part not in archive.namelist():
        return strings
    state = {"si": None, "t": 0, "skip": 0}

    def start(name, attrs):
        if name == "rPh":
            state["skip"] += 1
        elif name == "si":
            state["si"] = []
        elif name == "t":
            state["t"] += 1

    def end(name):
        if name == "rPh":
            state["skip"] -= 1
        elif name == "si" and state["si"] is not None:
            strings.append("".join(state["si"]))
            state["si"] = None
        elif name == "t":
            state["t"] -= 1

    def text(data):
        if state["si"] is not None and state["t"] and not state["skip"]:
            state["si"].append(data)

    _xml(archive, part, budget, start, end, text)
    return strings


def _date_styles(archive, rels, budget):
    """The cell styles (their numbers) that show a date."""
    part = next((p for k, p in rels.values() if k == "styles"), "xl/styles.xml")
    if not part or part not in archive.namelist():
        return set()
    custom, cells, place = {}, [], {"xfs": False}

    def start(name, attrs):
        if name == "numFmt":
            code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", attrs.get("formatCode") or "").lower()
            custom[attrs.get("numFmtId")] = bool(re.search(r"[dy]", code)) or bool(re.search(r"m.*s|h", code) and ":" in code)
        elif name == "cellXfs":
            place["xfs"] = True
        elif name == "xf" and place["xfs"]:
            cells.append(attrs.get("numFmtId") or "0")

    def end(name):
        if name == "cellXfs":
            place["xfs"] = False

    _xml(archive, part, budget, start, end)
    out = set()
    for index, fmt in enumerate(cells):
        if (fmt.isdigit() and int(fmt) in _DATE_FORMATS) or custom.get(fmt):
            out.add(index)
    return out


class _SheetRows:
    """One worksheet's rows as CSV lines: shared strings, inline strings, booleans, errors, numbers, and dates where the
    cell's style shows one."""

    def __init__(self, out, shared, dates, base):
        self.out, self.shared, self.dates, self.base = out, shared, dates, base
        self.row, self.cell, self.part, self.skip = None, None, None, 0

    def start(self, name, attrs):
        if name == "rPh":
            self.skip += 1
        elif name == "row":
            self.row = {}
        elif name == "c" and self.row is not None:
            column = _column(attrs.get("r"))
            if column is None:
                column = max(self.row) + 1 if self.row else 0
            style = attrs.get("s") or "0"
            self.cell = {"col": column, "type": attrs.get("t") or "n", "style": int(style) if style.isdigit() else 0,
                         "v": [], "is": []}
        elif name in ("v", "t") and self.cell is not None:
            self.part = "v" if name == "v" else "is"

    def end(self, name):
        if name == "rPh":
            self.skip -= 1
        elif name in ("v", "t"):
            self.part = None
        elif name == "c" and self.cell is not None and self.row is not None:
            if self.cell["col"] < MAX_COLUMNS:
                self.row[self.cell["col"]] = self._value(self.cell)
            self.cell = None
        elif name == "row" and self.row is not None:
            row, self.row = self.row, None
            filled = [c for c, v in row.items() if v != ""]
            if filled:
                self.out.add(_csv([row.get(c, "") for c in range(max(filled) + 1)]))

    def text(self, data):
        if self.cell is not None and self.part and not self.skip:
            self.cell[self.part].append(data)

    def _value(self, cell):
        value = "".join(cell["v"])
        kind = cell["type"]
        if kind == "s":
            return self.shared[int(value)] if value.strip().isdigit() and int(value) < len(self.shared) else ""
        if kind == "inlineStr":
            return "".join(cell["is"])
        if kind == "b":
            return "TRUE" if value.strip() == "1" else "FALSE"
        if kind in ("e", "str", "d"):
            return value
        if cell["style"] in self.dates and value:
            try:
                when = self.base + timedelta(days=float(value))
                return when.strftime("%Y-%m-%d %H:%M" if when.hour or when.minute else "%Y-%m-%d")
            except (ValueError, OverflowError):
                return value
        return value


class _OpenDocument:
    """The words of an OpenDocument's content.xml: paragraphs and headings a line each; a spreadsheet's tables a block
    of CSV rows each; a presentation's pages a block each; in a text, a table's row a line with " | " between cells."""

    def __init__(self, out, mime):
        self.out, self.mime = out, mime
        self.para, self.skip, self.tables, self.pages = None, 0, [], 0

    def start(self, name, attrs):
        if self.skip or name in ("annotation", "tracked-changes", "note-citation"):
            self.skip += 1
            return
        if name in ("p", "h"):
            self.para = []
        elif name == "s" and self.para is not None:
            count = _attr(attrs, "c", "text") or "1"
            self.para.append(" " * min(100, int(count) if count.isdigit() else 1))
        elif name == "tab" and self.para is not None:
            self.para.append("\t")
        elif name == "line-break" and self.para is not None:
            self.para.append("\n")
        elif name == "page" and self.mime == _ODP:
            self.pages += 1
            self.out.add(f"Slide {self.pages}:")
        elif name == "table":
            label = _attr(attrs, "name", "table") or ""
            self.tables.append({"row": None, "cell": None, "repeat": 1})
            if self.mime == _ODS:
                self.out.add(f"Sheet “{label}”:")
        elif name == "table-row" and self.tables:
            repeat = _attr(attrs, "number-rows-repeated", "table") or "1"
            self.tables[-1].update(row=[], rows=min(1000, int(repeat) if repeat.isdigit() else 1))
        elif name in ("table-cell", "covered-table-cell") and self.tables:
            repeat = _attr(attrs, "number-columns-repeated", "table") or "1"
            self.tables[-1].update(cell=[], repeat=min(MAX_COLUMNS, int(repeat) if repeat.isdigit() else 1))

    def end(self, name):
        if self.skip:
            self.skip -= 1
            return
        if name in ("p", "h") and self.para is not None:
            line = "".join(self.para).strip()
            self.para = None
            table = self.tables[-1] if self.tables else None
            if table and table["cell"] is not None:
                if line:
                    table["cell"].append(line)
            elif line:
                self.out.add(line)
        elif name in ("table-cell", "covered-table-cell") and self.tables and self.tables[-1]["cell"] is not None:
            table = self.tables[-1]
            if table["row"] is not None and len(table["row"]) < MAX_COLUMNS:
                value = " / ".join(table["cell"])
                table["row"].extend([value] * min(table["repeat"], MAX_COLUMNS - len(table["row"])))
            table["cell"] = None
        elif name == "table-row" and self.tables and self.tables[-1]["row"] is not None:
            table = self.tables[-1]
            row, table["row"] = table["row"], None
            while row and row[-1] == "":
                row.pop()
            if row:
                line = _csv(row) if self.mime == _ODS else " | ".join(row)
                for _ in range(table.get("rows", 1)):
                    self.out.add(line)
        elif name == "table" and self.tables:
            self.tables.pop()
            if self.mime == _ODS:
                self.out.add("")
        elif name == "page" and self.mime == _ODP:
            self.out.add("")

    def text(self, data):
        if self.para is not None and not self.skip:
            self.para.append(data)


def _open_document(archive, budget):
    mime = archive.read("mimetype").decode("ascii", "replace").strip()
    out = _Lines()
    try:
        _xml(archive, "content.xml", budget, *_handlers(_OpenDocument(out, mime)))
    except _Full:
        pass
    return out.text().rstrip()


def _zip_list(archive, budget):
    infos = [i for i in archive.infolist() if not i.is_dir()]
    out = _Lines()
    total = sum(i.file_size for i in infos)
    out.add(f"{len(infos):,} {'file' if len(infos) == 1 else 'files'}, {size_words(total)} when unpacked.")
    try:
        for info in infos[:MAX_LISTED]:
            name = "".join(ch for ch in info.filename if unicodedata.category(ch) not in ("Cc", "Cf", "Cs"))
            locked = " (locked with a password)" if info.flag_bits & 0x1 else ""
            out.add(f"{name}: {size_words(info.file_size)}{locked}")
        if len(infos) > MAX_LISTED:
            out.add(f"…and {len(infos) - MAX_LISTED:,} more.")
    except _Full:
        pass
    return out.text()


_READERS = {_DOCX: _word, _XLSX: _sheets, _PPTX: _slides, _ODT: _open_document, _ODS: _open_document,
            _ODP: _open_document}


# ---- a PDF's pages ----

def pdf_pages(body):
    """How many pages a PDF has, as far as its bytes say without a PDF library: its linearization's count, else its page
    tree's (the largest /Count beside /Kids or /Type /Pages), else its pages (/Type /Page) counted; inside compressed
    object streams too (unpacking at most MAX_PART). None when nothing says."""
    raw = bytes(body)
    found = re.search(rb"/Linearized\s+[\d.]+(?:(?!>>).){0,400}?/N\s+(\d+)", raw[:4096], re.S)
    if found:
        return int(found.group(1)) or None
    texts = [raw] + _object_streams(raw)
    counts, pages = [], 0
    for text in texts:
        for match in re.finditer(rb"/Kids\s*\[[^\]]*\]\s*/Count\s+(\d+)|/Count\s+(\d+)\s*/Kids"
                                 rb"|/Type\s*/Pages\b[^>]{0,200}?/Count\s+(\d+)|/Count\s+(\d+)[^>]{0,200}?/Type\s*/Pages\b",
                                 text, re.S):
            counts.append(int(next(g for g in match.groups() if g)))
        pages += len(re.findall(rb"/Type\s*/Page(?![A-Za-z])", text))
    if counts and max(counts):
        return max(counts)
    return pages or None


def _object_streams(raw):
    """The unpacked contents of a PDF's compressed object streams (where a newer PDF keeps its page tree)."""
    out, left = [], MAX_PART
    for n, match in enumerate(re.finditer(rb"/Type\s*/ObjStm\b", raw)):
        if n >= MAX_PDF_STREAMS or left <= 0:
            break
        begin = raw.find(b"stream", match.end())
        if begin < 0 or b"/FlateDecode" not in raw[max(0, match.start() - 400):begin]:
            continue
        begin += len(b"stream")
        begin += 2 if raw[begin:begin + 2] == b"\r\n" else 1
        stop = raw.find(b"endstream", begin)
        if stop < 0:
            continue
        try:
            unpacker = zlib.decompressobj()
            data = unpacker.decompress(raw[begin:stop], left)
        except zlib.error:
            continue
        left -= len(data)
        out.append(data)
    return out
