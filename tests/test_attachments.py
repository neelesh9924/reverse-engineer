"""app/attachments.py, the shared module (the same file in four studios): every kind of file told by its bytes, a file
whose name lies, names made safe and never over another file, the readable copies of Word, Excel, PowerPoint and
OpenDocument files and of a zip (made with zipfile here, as those programs make them), a zip bomb and a DTD refused,
the caps, a PDF's pages, what Claude is told for each kind, and how a studio serves one."""
import gzip
import io
import shutil
import struct
import tarfile
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

from support import PNG

from app import attachments
from app.attachments import Refused, describe, for_claude, headers, pdf_pages, safe_name, save, sniff, text_of

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
P = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
     'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"')
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MAIN = {"docx": ("word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"),
        "xlsx": ("xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"),
        "pptx": ("ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml")}


def package(kind, parts, level=zipfile.ZIP_DEFLATED):
    """An OOXML package as Word, Excel or PowerPoint writes one: its content types, its relationships, its parts."""
    out = io.BytesIO()
    main, content = MAIN[kind]
    with zipfile.ZipFile(out, "w", level) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.'
                         'openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/'
                         f'xml"/><Override PartName="/{main}" ContentType="{content}"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                         f'package/2006/relationships"><Relationship Id="rId1" Type="{REL}/officeDocument" Target="{main}"/>'
                         '</Relationships>')
        for name, body in parts.items():
            archive.writestr(name, body)
    return out.getvalue()


def rels(*links):
    inner = "".join(f'<Relationship Id="{rid}" Type="{REL}/{kind}" Target="{target}"/>' for rid, kind, target in links)
    return f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{inner}</Relationships>'


def run(text):
    return f"<w:r><w:t xml:space=\"preserve\">{text}</w:t></w:r>"


def docx(body, header=""):
    parts = {"word/document.xml": f'<?xml version="1.0" encoding="UTF-8"?><w:document {W}><w:body>{body}'
                                  '<w:sectPr><w:pgSz w:w="11906"/></w:sectPr></w:body></w:document>'}
    if header:
        parts["word/header1.xml"] = f'<?xml version="1.0"?><w:hdr {W}><w:p>{run(header)}</w:p></w:hdr>'
    return package("docx", parts)


def xlsx():
    book = (f'<?xml version="1.0"?><workbook {S} {R}><workbookPr/><sheets><sheet name="Coffee" sheetId="1" r:id="rId1"/>'
            '<sheet name="Old prices" sheetId="2" state="hidden" r:id="rId2"/></sheets></workbook>')
    shared = (f'<?xml version="1.0"?><sst {S} count="5"><si><t>Drink</t></si><si><t>Price</t></si>'
              '<si><r><t>Filter </t></r><r><rPr><b/></rPr><t>kaapi</t></r><rPh><t>PHONETIC</t></rPh></si>'
              '<si><t>Since</t></si><si><t>Cold brew, "iced"</t></si></sst>')
    styles = (f'<?xml version="1.0"?><styleSheet {S}><numFmts count="1"><numFmt numFmtId="164" formatCode="dd/mm/yyyy"/>'
              '</numFmts><cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/></cellXfs></styleSheet>')
    sheet = (f'<?xml version="1.0"?><worksheet {S}><sheetData>'
             '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="D1" t="s"><v>3</v></c></row>'
             '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>60</v></c><c r="C2" t="b"><v>1</v></c>'
             '<c r="D2" s="1"><v>45291</v></c></row>'
             '<row r="3"><c r="A3" t="s"><v>4</v></c><c r="B3"><f>B2*3</f><v>180</v></c><c r="C3" t="e"><v>#N/A</v></c>'
             '<c r="D3" s="2"><v>45292.5</v></c></row>'
             '<row r="5"><c r="A5" t="inlineStr"><is><t>Written in</t></is></c><c r="B5" t="str"><v>a formula</v></c></row>'
             '</sheetData></worksheet>')
    old = f'<?xml version="1.0"?><worksheet {S}><sheetData><row r="1"><c r="A1"><v>55</v></c></row></sheetData></worksheet>'
    return package("xlsx", {"xl/workbook.xml": book,
                            "xl/_rels/workbook.xml.rels": rels(("rId1", "worksheet", "worksheets/sheet1.xml"),
                                                               ("rId2", "worksheet", "/xl/worksheets/sheet2.xml"),
                                                               ("rId3", "sharedStrings", "sharedStrings.xml"),
                                                               ("rId4", "styles", "styles.xml")),
                            "xl/sharedStrings.xml": shared, "xl/styles.xml": styles,
                            "xl/worksheets/sheet1.xml": sheet, "xl/worksheets/sheet2.xml": old})


def slide(*paragraphs, table=()):
    body = "".join(f"<a:p><a:r><a:t>{p}</a:t></a:r></a:p>" for p in paragraphs)
    if table:
        rows = "".join("<a:tr>" + "".join(f"<a:tc><a:txBody><a:p><a:r><a:t>{c}</a:t></a:r></a:p></a:txBody></a:tc>"
                                          for c in row) + "</a:tr>" for row in table)
        body += f"<p:graphicFrame><a:graphic><a:graphicData><a:tbl>{rows}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
    return f'<?xml version="1.0"?><p:sld {P}><p:cSld><p:spTree><p:sp><p:txBody>{body}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>'


def pptx():
    # The slides' files are named against their order: the presentation's list decides it.
    presentation = (f'<?xml version="1.0"?><p:presentation {P} {R}><p:sldIdLst><p:sldId id="256" r:id="rId9"/>'
                    '<p:sldId id="257" r:id="rId8"/></p:sldIdLst></p:presentation>')
    notes = (f'<?xml version="1.0"?><p:notes {P}><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Say the prices aloud'
             '</a:t></a:r></a:p><a:p><a:fld type="slidenum"><a:t>1</a:t></a:fld></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>')
    return package("pptx", {"ppt/presentation.xml": presentation,
                            "ppt/_rels/presentation.xml.rels": rels(("rId8", "slide", "slides/slide1.xml"),
                                                                    ("rId9", "slide", "slides/slide2.xml")),
                            "ppt/slides/slide2.xml": slide("Brew Bros", "Coffee since 2009"),
                            "ppt/slides/_rels/slide2.xml.rels": rels(("rId1", "notesSlide", "../notesSlides/notesSlide1.xml")),
                            "ppt/notesSlides/notesSlide1.xml": notes,
                            "ppt/slides/slide1.xml": slide("Prices", table=[("Filter kaapi", "₹60"), ("Cold brew", "₹180")])})


def opendocument(mime, body):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), mime)   # first, and stored, as the format asks
        archive.writestr("content.xml", '<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:tc:'
                         'opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
                         'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:draw="urn:oasis:names:tc:'
                         f'opendocument:xmlns:drawing:1.0"><office:body>{body}</office:body></office:document-content>')
    return out.getvalue()


def zipped(entries, level=zipfile.ZIP_DEFLATED):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", level) as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
    return out.getvalue()


def pdf(pages, compressed=False, linearized=False):
    """A PDF of `pages` pages; `compressed`: its page tree inside a compressed object stream, as newer PDFs keep it."""
    kids = " ".join(f"{3 + i} 0 R" for i in range(pages))
    tree = f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>"
    head = f"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n".encode("latin-1")
    if linearized:
        head += f"1 0 obj << /Linearized 1 /L 999 /N {pages} /T 900 >> endobj\n".encode()
    if compressed:
        stream = zlib.compress(f"2 0 {tree}".encode())
        return head + (f"5 0 obj << /Type /ObjStm /N 1 /First 4 /Length {len(stream)} /Filter /FlateDecode >>\nstream\n"
                       .encode() + stream + b"\nendstream\nendobj\n%%EOF\n")
    body = head + f"2 0 obj {tree} endobj\n".encode()
    for i in range(pages):
        body += f"{3 + i} 0 obj << /Type /Page /Parent 2 0 R >> endobj\n".encode()
    return body + b"%%EOF\n"


def tar():
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as archive:
        info = tarfile.TarInfo("menu.txt")
        info.size = 4
        archive.addfile(info, io.BytesIO(b"menu"))
    return out.getvalue()


ZEROS = b"\x00" * 64
# One of each kind, as its program writes its first bytes.
SAMPLES = {
    "png": (PNG, "picture", "image/png"),
    "jpeg": (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + ZEROS, "picture", "image/jpeg"),
    "gif": (b"GIF89a\x01\x00\x01\x00\x80\x00\x00" + ZEROS, "picture", "image/gif"),
    "webp": (b"RIFF\x24\x00\x00\x00WEBPVP8 \x18\x00\x00\x00" + ZEROS, "picture", "image/webp"),
    "bmp": (b"BM" + struct.pack("<IHHI", 90, 0, 0, 54) + struct.pack("<I", 40) + ZEROS, "picture", "image/bmp"),
    "tiff": (b"II*\x00\x08\x00\x00\x00" + ZEROS, "picture", "image/tiff"),
    "heic": (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + ZEROS, "picture", "image/heic"),
    "avif": (b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1" + ZEROS, "picture", "image/avif"),
    "pdf": (pdf(2), "pdf", "application/pdf"),
    "docx": (docx(f"<w:p>{run('Since 2009')}</w:p>"), "office", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "xlsx": (xlsx(), "office", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "pptx": (pptx(), "office", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    "odt": (opendocument("application/vnd.oasis.opendocument.text", "<office:text><text:p>Hello</text:p></office:text>"),
            "office", "application/vnd.oasis.opendocument.text"),
    "doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + ZEROS, "office", "application/msword"),
    "mp3": (b"ID3\x03\x00\x00\x00\x00\x00\x0a" + ZEROS + b"\xff\xfb\x90\x64" + ZEROS, "audio", "audio/mpeg"),
    "mp3 frames": (b"\xff\xfb\x90\x64" + ZEROS, "audio", "audio/mpeg"),
    "wav": (b"RIFF\x24\x00\x00\x00WAVEfmt " + ZEROS, "audio", "audio/wav"),
    "ogg": (b"OggS\x00\x02" + ZEROS + b"\x01vorbis" + ZEROS, "audio", "audio/ogg"),
    "flac": (b"fLaC\x00\x00\x00\x22" + ZEROS, "audio", "audio/flac"),
    "m4a": (b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00M4A isom" + ZEROS, "audio", "audio/mp4"),
    "mp4": (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + ZEROS, "video", "video/mp4"),
    "mov": (b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  " + ZEROS, "video", "video/quicktime"),
    "webm": (b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82\x84webm" + ZEROS, "video", "video/webm"),
    "mkv": (b"\x1a\x45\xdf\xa3\xa3\x42\x86\x81\x01\x42\x82\x88matroska" + ZEROS, "video", "video/x-matroska"),
    "avi": (b"RIFF\x24\x00\x00\x00AVI LIST" + ZEROS, "video", "video/x-msvideo"),
    "zip": (zipped({"photos/front.jpg": b"\xff\xd8\xff" + ZEROS, "menu.txt": b"Filter kaapi 60"}), "archive", "application/zip"),
    "7z": (b"7z\xbc\xaf\x27\x1c\x00\x04" + ZEROS, "archive", "application/x-7z-compressed"),
    "rar": (b"Rar!\x1a\x07\x01\x00" + ZEROS, "archive", "application/vnd.rar"),
    "gz": (gzip.compress(b"the menu"), "archive", "application/gzip"),
    "tar": (tar(), "archive", "application/x-tar"),
    "exe": (b"MZ\x90\x00\x03\x00\x00\x00" + ZEROS, "other", "application/x-msdownload"),
    "binary": (bytes(range(256)), "other", "application/octet-stream"),
    "text": ("Filter kaapi ₹60\nCold brew ₹180\n".encode("utf-8"), "text", "text/plain"),
    "json": (b'{"menu": [{"drink": "Filter kaapi", "price": 60}]}', "text", "application/json"),
    "html": (b"<!doctype html><html><body><script>alert(1)</script></body></html>", "text", "text/html"),
    "svg": (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "text", "image/svg+xml"),
    "xml": (b'<?xml version="1.0"?><menu><drink price="60">Filter kaapi</drink></menu>', "text", "application/xml"),
    "rtf": (b"{\\rtf1\\ansi Filter kaapi 60}", "text", "application/rtf"),
    "utf-16": ("\ufeffFilter kaapi ₹60".encode("utf-16-le").replace(b"\xff\xfe\xff\xfe", b"\xff\xfe"), "text", "text/plain"),
    "starts like a program": (b"MZ Cafe: the menu, written as a note.", "text", "text/plain"),
    "starts like a sound": (b"ID3 tags are on the old CD player", "text", "text/plain"),
}


class Folder(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="attach-test-"))
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        attachments._known.clear()


class KindsTest(Folder):
    def test_every_kind_by_its_bytes_whatever_its_name(self):
        for label, (body, kind, mime) in SAMPLES.items():
            for name in (("file", "file.bin") if label != "doc" else ()) + (f"file.{label.split()[0]}",):
                found = sniff(body, name)
                self.assertEqual((found["kind"], found["mime"]), (kind, mime), f"{label} named {name}")
        # An older Office file is one of Microsoft's containers: its name says which (an installer is one too).
        self.assertEqual(sniff(SAMPLES["doc"][0], "setup.msi")["kind"], "other")

    def test_a_file_whose_name_lies_is_kept_by_what_it_is(self):
        renamed = {"menu.txt": (SAMPLES["pdf"][0], "menu.pdf", "pdf"),
                   "photo.pdf": (PNG, "photo.png", "picture"),
                   "story.zip": (SAMPLES["docx"][0], "story.docx", "office"),
                   "photos.docx": (SAMPLES["zip"][0], "photos.zip", "archive"),
                   "notes.pdf": (b"Open late on Fridays.", "notes.txt", "text"),
                   "page.png": (SAMPLES["html"][0], "page.html", "text"),
                   "tour": (SAMPLES["mp4"][0], "tour.mp4", "video"),
                   "Front.JPEG": (SAMPLES["jpeg"][0], "Front.jpeg", "picture"),    # its own extension, in small letters
                   "notes.log": (b"Open late", "notes.log", "text"),               # a text's own name is kept
                   "setup.exe": (SAMPLES["exe"][0], "setup.exe", "other")}
        for given, (body, kept, kind) in renamed.items():
            record = save(self.folder, given, body)
            self.assertEqual((record["name"], record["kind"]), (kept, kind), given)
            self.assertEqual((self.folder / kept).read_bytes(), body, "kept byte for byte")
        self.assertIn("Since 2009", (self.folder / ".text" / "story.docx.txt").read_text(encoding="utf-8"),
                      "a Word document sent as a zip is read as the Word document it is")

    def test_a_damaged_file_whose_name_says_what_it_is_is_refused(self):
        junk = b"\x00\x13\x37\x00" * 8   # no kind's signature, and not text
        for name, said in (("front.png", "picture"), ("menu.pdf", "PDF"), ("prices.xlsx", "Excel workbook"),
                           ("song.mp3", "MP3 sound"), ("tour.mp4", "MP4 video"), ("photos.zip", "zip archive")):
            with self.assertRaises(Refused, msg=name) as caught:
                save(self.folder, name, junk)
            self.assertEqual(str(caught.exception), f"{name} isn’t the {said} it says it is: it may be damaged. Attach it again.")
        self.assertEqual(list(self.folder.iterdir()) if self.folder.exists() else [], [], "nothing written")
        self.assertEqual(save(self.folder, "data.bin", junk)["kind"], "other", "a name that says no kind keeps it as a file")

    def test_text_in_an_older_windows_encoding_or_utf16_gets_a_utf8_copy(self):
        csv_body = "Drink,Price\nCaf\xe9 latte,120\n".encode("cp1252")
        record = save(self.folder, "prices.csv", csv_body)
        self.assertEqual((record["kind"], record["mime"], record["text"]), ("text", "text/csv", ".text/prices.csv.txt"))
        self.assertEqual((self.folder / record["text"]).read_text(encoding="utf-8"), "Drink,Price\nCafé latte,120\n")
        self.assertEqual(sniff(csv_body, "prices.bin")["kind"], "other", "only a file named as text is read that way")
        record = save(self.folder, "notes.txt", SAMPLES["utf-16"][0])
        self.assertEqual((self.folder / record["text"]).read_text(encoding="utf-8"), "Filter kaapi ₹60")
        self.assertNotIn("text", save(self.folder, "plain.txt", b"plain words"), "UTF-8 text is read as it is")


class NamesTest(Folder):
    def test_names_are_made_safe(self):
        for given, safe in [("../../evil.txt", "evil.txt"), ("..\\..\\evil.txt", "evil.txt"),
                            ("C:\\Windows\\win.ini", "win.ini"), ("C:menu.pdf", "menu.pdf"),
                            ("\\\\host\\share\\menu.pdf", "menu.pdf"), ("//host/share/menu.pdf", "menu.pdf"),
                            ("con.txt", "_con.txt"), ("CON", "_CON"), ("lpt1.pdf", "_lpt1.pdf"), ("nul.tar.gz", "_nul.tar.gz"),
                            ("aux", "_aux"), ("menu:secret.pdf", "menu-secret.pdf"), ('a<b>"c"|d?*.txt', "a-b--c--d--.txt"),
                            ("invoice\u202efdp.exe", "invoicefdp.exe"), ("tab\tand\nline.txt", "tabandline.txt"),
                            ("na\x00me.pdf", "name.pdf"), (" .hidden ", "hidden"), ("menu .pdf", "menu.pdf"),
                            ("trailing. . ", "trailing"), ("", "file"), ("...", "file"), (None, "file"),
                            ("Report.PDF", "Report.pdf"), ("Café Menu (2024).pdf", "Café Menu (2024).pdf"),
                            ("e\u0301te.txt", "\u00e9te.txt")]:
            self.assertEqual(safe_name(given), safe, repr(given))
        long = safe_name("x" * 300 + ".pdf")
        self.assertEqual((len(long), long[-4:]), (attachments.MAX_NAME, ".pdf"), "its extension kept, the rest cut")

    def test_a_file_is_kept_inside_its_folder_under_a_safe_name(self):
        for given in ("../../outside.txt", "..\\outside.txt", "C:\\outside.txt", "\\\\host\\share\\outside.txt", "con.txt"):
            record = save(self.folder, given, b"words")
            self.assertTrue((self.folder / record["path"]).resolve().is_relative_to(self.folder.resolve()), given)
            self.assertEqual(record["path"], record["name"], "a plain name, no folder in it")
        self.assertFalse((self.folder.parent / "outside.txt").exists())
        self.assertIn("_con.txt", [p.name for p in self.folder.iterdir()])

    def test_never_over_another_file(self):
        first = save(self.folder, "Menu.pdf", SAMPLES["pdf"][0])
        second = save(self.folder, "menu.pdf", pdf(3))
        third = save(self.folder, "MENU.pdf", pdf(4))
        no_ext = [save(self.folder, "notes", b"one")["name"], save(self.folder, "notes", b"two")["name"]]
        self.assertEqual([first["name"], second["name"], third["name"], *no_ext], ["Menu.pdf", "menu-2.pdf", "MENU-3.pdf", "notes", "notes-2"])
        self.assertEqual((self.folder / "Menu.pdf").read_bytes(), SAMPLES["pdf"][0], "the first one untouched")
        self.assertEqual([third["pages"], second["pages"]], [4, 3])
        self.assertEqual([p.name for p in self.folder.iterdir() if p.name.endswith(".tmp")], [], "no draft left behind")

    def test_what_cannot_be_kept_is_said_and_nothing_is_written(self):
        with self.assertRaisesRegex(Refused, r"^notes\.txt is empty: there is nothing in it to attach\.$"):
            save(self.folder, "notes.txt", b"")
        with self.assertRaisesRegex(Refused, r"^big\.mp4 is 30 MB: a file can be up to 25 MB\.$"):
            save(self.folder, "big.mp4", b"\x00" * (30 * 1024 * 1024))
        with self.assertRaisesRegex(Refused, r"^menu\.pdf is 2 KB: a file can be up to 1 KB\.$"):
            save(self.folder, "menu.pdf", b"%PDF-1.4" + b"\x00" * 2040, {"file": 1024})
        self.assertEqual(list(self.folder.iterdir()), [])


class ReadableTest(Folder):
    def copy(self, name, body):
        record = save(self.folder, name, body)
        return record, (self.folder / record["text"]).read_text(encoding="utf-8")

    def test_a_word_document_s_words(self):
        body = (f"<w:p><w:pPr><w:tabs><w:tab w:val=\"left\" w:pos=\"720\"/></w:tabs></w:pPr>{run('Brew Bros')}</w:p>"
                f"<w:p>{run('Filter kaapi')}<w:r><w:tab/></w:r>{run('₹60')}</w:p>"
                f"<w:p>{run('Call ')}<w:ins><w:r><w:t>98765 43210</w:t></w:r></w:ins><w:del><w:r><w:delText>OLD NUMBER"
                "</w:delText></w:r></w:del></w:p>"
                "<w:p><w:r><w:fldChar w:fldCharType=\"begin\"/></w:r><w:r><w:instrText>HYPERLINK \"https://evil.example\""
                f"</w:instrText></w:r>{run('our website')}</w:p><w:p/>"
                f"<w:tbl><w:tblPr/><w:tr><w:tc><w:p>{run('Cold brew')}</w:p></w:tc><w:tc><w:p>{run('₹180')}</w:p></w:tc></w:tr>"
                f"<w:tr><w:tc><w:p>{run('Since')}</w:p><w:p>{run('2009')}</w:p></w:tc><w:tc/></w:tr></w:tbl>")
        record, words = self.copy("Story.docx", docx(body, header="100 Feet Road, Indiranagar"))
        self.assertEqual(record["text"], ".text/Story.docx.txt")
        self.assertEqual(words, "The words of Story.docx (a Word document), as plain text:\n\nBrew Bros\nFilter kaapi\t₹60\n"
                                "Call 98765 43210\nour website\nCold brew | ₹180\nSince / 2009 | \n\nIn its headers and footers:\n"
                                "100 Feet Road, Indiranagar\n")

    def test_an_excel_workbook_one_block_of_rows_a_sheet(self):
        record, words = self.copy("prices.xlsx", xlsx())
        self.assertEqual(words, "The words of prices.xlsx (an Excel workbook), as plain text:\n\nSheet “Coffee”:\n"
                                "Drink,Price,,Since\nFilter kaapi,60,TRUE,2023-12-31\n\"Cold brew, \"\"iced\"\"\",180,#N/A,2024-01-01 12:00\n"
                                "Written in,a formula\n\nSheet “Old prices” (hidden):\n55\n")
        self.assertIn("(one block of rows per sheet, as CSV)", for_claude([record])[0])

    def test_a_presentation_slide_by_slide_with_its_notes(self):
        _, words = self.copy("deck.pptx", pptx())
        self.assertEqual(words, "The words of deck.pptx (a PowerPoint presentation), as plain text:\n\nSlide 1:\nBrew Bros\n"
                                "Coffee since 2009\nNotes: Say the prices aloud\n\nSlide 2:\nPrices\nFilter kaapi | ₹60\n"
                                "Cold brew | ₹180\n")

    def test_opendocument_text_sheets_and_slides(self):
        text = ('<office:text><text:h>Brew Bros</text:h><text:p>Filter<text:s text:c="2"/>kaapi<text:tab/>60'
                '<office:annotation><text:p>A COMMENT</text:p></office:annotation></text:p>'
                '<table:table><table:table-row><table:table-cell><text:p>Cold brew</text:p></table:table-cell>'
                '<table:table-cell><text:p>180</text:p></table:table-cell></table:table-row></table:table></office:text>')
        _, words = self.copy("story.odt", opendocument("application/vnd.oasis.opendocument.text", text))
        self.assertEqual(words.split("\n\n", 1)[1], "Brew Bros\nFilter  kaapi\t60\nCold brew | 180\n")
        sheet = ('<office:spreadsheet><table:table table:name="Coffee"><table:table-row><table:table-cell><text:p>Drink'
                 '</text:p></table:table-cell><table:table-cell table:number-columns-repeated="2"><text:p>60</text:p>'
                 '</table:table-cell><table:table-cell table:number-columns-repeated="16380"/></table:table-row>'
                 '<table:table-row table:number-rows-repeated="1048000"><table:table-cell table:number-columns-repeated="16384"/>'
                 '</table:table-row></table:table></office:spreadsheet>')
        _, words = self.copy("prices.ods", opendocument("application/vnd.oasis.opendocument.spreadsheet", sheet))
        self.assertEqual(words.split("\n\n", 1)[1], "Sheet “Coffee”:\nDrink,60,60\n", "empty repeats are not spelt out")
        slides = ('<office:presentation><draw:page draw:name="one"><draw:frame><draw:text-box><text:p>Brew Bros</text:p>'
                  '</draw:text-box></draw:frame></draw:page><draw:page><text:p>Prices</text:p></draw:page></office:presentation>')
        _, words = self.copy("deck.odp", opendocument("application/vnd.oasis.opendocument.presentation", slides))
        self.assertEqual(words.split("\n\n", 1)[1], "Slide 1:\nBrew Bros\n\nSlide 2:\nPrices\n")

    def test_a_zip_is_listed_never_unpacked(self):
        body = zipped({"photos/": b"", "photos/front.jpg": b"\xff\xd8\xff" + b"\x00" * 3000, "menu.txt": b"Filter kaapi 60",
                       "evil\u202etxt.exe": b"MZ"})
        record, words = self.copy("photos.zip", body)
        self.assertEqual(words, "What photos.zip holds (a zip archive):\n\n3 files, 3 KB when unpacked.\nphotos/front.jpg: 3 KB\n"
                                "menu.txt: 15 bytes\neviltxt.exe: 2 bytes\n")
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), [".text", "photos.zip"], "nothing unpacked")

    def test_an_older_office_file_is_kept_with_no_copy(self):
        record = save(self.folder, "menu.doc", SAMPLES["doc"][0])
        self.assertNotIn("text", record)
        self.assertEqual(for_claude([record]), ["- menu.doc: can't be opened here: an older .doc Word document, 72 bytes."])

    def test_a_damaged_office_file_is_refused_in_words(self):
        broken = package("docx", {"word/document.xml": f'<?xml version="1.0"?><w:document {W}><w:body><w:p>'})
        with self.assertRaisesRegex(Refused, r"^Story\.docx can't be read as a Word document: it may be damaged\. "
                                             r"Attach it again, or as a PDF\.$"):
            save(self.folder, "Story.docx", broken)
        self.assertEqual(list(self.folder.iterdir()), [], "nothing kept")


class SafetyTest(Folder):
    def test_a_dtd_or_an_entity_is_refused(self):
        laughs = ('<?xml version="1.0"?><!DOCTYPE w:document [<!ENTITY a "lol"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">'
                  f'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;">]><w:document {W}><w:body><w:p><w:r><w:t>&c;</w:t></w:r></w:p>'
                  '</w:body></w:document>')
        outside = (f'<?xml version="1.0"?><!DOCTYPE w:document SYSTEM "file:///C:/Windows/win.ini"><w:document {W}>'
                   '<w:body/></w:document>')
        for xml in (laughs, outside):
            with self.assertRaisesRegex(Refused, r"^x\.docx can't be read safely: it declares a DTD or an entity\.$"):
                save(self.folder, "x.docx", package("docx", {"word/document.xml": xml}))
        self.assertEqual(list(self.folder.iterdir()), [])
        undeclared = f'<?xml version="1.0"?><w:document {W}><w:body><w:p><w:r><w:t>&nope;</w:t></w:r></w:p></w:body></w:document>'
        with self.assertRaisesRegex(Refused, "it may be damaged"):
            save(self.folder, "x.docx", package("docx", {"word/document.xml": undeclared}))

    def test_a_zip_bomb_is_refused(self):
        # A Word document whose words unpack from a few dozen kilobytes to 40 MB: past what a part may unpack to.
        spaces = f'<?xml version="1.0"?><w:document {W}><w:body><w:p><w:r><w:t>' + " " * (40 * 1024 * 1024) + "</w:t></w:r></w:p></w:body></w:document>"
        bomb = package("docx", {"word/document.xml": spaces})
        self.assertLess(len(bomb), 200 * 1024)
        with self.assertRaisesRegex(Refused, r"^bomb\.docx can't be read safely: a part of it unpacks to 40\.1 MB\.$"):
            save(self.folder, "bomb.docx", bomb)
        # Its header lying about its size: zipfile stops at what it says, and the part is then refused as damaged.
        lying = bytearray(bomb)
        at = lying.rfind(b"PK\x01\x02")
        name = lying[at + 46:at + 46 + 17]
        self.assertEqual(bytes(name), b"word/document.xml")
        struct.pack_into("<I", lying, at + 24, 1000)        # the central directory's "uncompressed size"
        local = lying.find(b"PK\x03\x04word/document.xml"[:4] , lying.find(b"word/document.xml") - 30)
        struct.pack_into("<I", lying, local + 22, 1000)
        with self.assertRaises(Refused):
            save(self.folder, "lying.docx", bytes(lying))
        # Many parts, each within its own limit, that unpack past what the whole file may.
        with mock.patch.object(attachments, "MAX_UNPACKED", 64 * 1024):
            parts = {f"word/header{n}.xml": f'<?xml version="1.0"?><w:hdr {W}><w:p>{run("x" * 20000 + str(n))}</w:p></w:hdr>'
                     for n in range(1, 6)}
            parts["word/document.xml"] = f'<?xml version="1.0"?><w:document {W}><w:body/></w:document>'
            with self.assertRaisesRegex(Refused, r"can't be read safely: it unpacks to more than 64 KB\.$"):
                save(self.folder, "many.docx", package("docx", parts))
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_a_zip_holding_too_many_files_is_kept_and_not_listed(self):
        with mock.patch.object(attachments, "MAX_ENTRIES", 10):
            body = zipped({f"f{n}.txt": b"x" for n in range(11)})
            with self.assertRaisesRegex(Refused, r"^many\.zip can't be read safely: it holds more than 10 files\.$"):
                save(self.folder, "many.zip", body)

    def test_the_caps_say_where_they_cut(self):
        with mock.patch.object(attachments, "MAX_WORDS", 60):
            _, words = ReadableTest.copy(self, "long.docx", docx("".join(f"<w:p>{run(f'Line {n} of the menu')}</w:p>" for n in range(20))))
        self.assertTrue(words.endswith("Line 2 of the menu\n\n[Cut here: the words go on past 60 characters; the rest is "
                                       "not in this copy.]\n"), words)
        with mock.patch.object(attachments, "MAX_LISTED", 3):
            _, words = ReadableTest.copy(self, "lots.zip", zipped({f"photo-{n}.jpg": b"x" for n in range(10)}))
        self.assertTrue(words.endswith("photo-2.jpg: 1 byte\n…and 7 more.\n"), words)


class ForClaudeTest(Folder):
    def test_what_claude_is_told_for_each_kind(self):
        names = {"front.png": PNG, "photo.heic": SAMPLES["heic"][0], "menu.pdf": pdf(2), "report.pdf": pdf(57),
                 "notes.md": b"Open late on Fridays.", "prices.xlsx": xlsx(), "Story.docx": SAMPLES["docx"][0],
                 "photos.zip": SAMPLES["zip"][0], "voice.mp3": SAMPLES["mp3"][0], "clip.mov": SAMPLES["mov"][0],
                 "setup.exe": SAMPLES["exe"][0], "notes-utf16.txt": SAMPLES["utf-16"][0]}
        records = [save(self.folder, name, body) for name, body in names.items()]
        lines = for_claude(records, ".materials")
        self.assertEqual(lines, [
            f"- .materials/front.png (a PNG picture, {len(PNG)} bytes): Read it.",
            f"- .materials/photo.heic: can't be opened here: a HEIC picture, as an iPhone takes them, {len(SAMPLES['heic'][0])} bytes.",
            f"- .materials/menu.pdf (a PDF, 2 pages, {len(pdf(2))} bytes): Read it.",
            f"- .materials/report.pdf (a PDF, 57 pages, {attachments.size_words(len(pdf(57)))}): 57 pages: read it in ranges of up to 20 "
            "(Read with pages \"1-20\", then \"21-40\", and so on).",
            "- .materials/notes.md (Markdown text, 21 bytes): Read it.",
            f"- .materials/prices.xlsx (an Excel workbook, {attachments.size_words(len(xlsx()))}): Read .materials/.text/prices.xlsx.txt, "
            "its words (one block of rows per sheet, as CSV).",
            f"- .materials/Story.docx (a Word document, {attachments.size_words(len(SAMPLES['docx'][0]))}): Read "
            ".materials/.text/Story.docx.txt, its words.",
            f"- .materials/photos.zip (a zip archive, {len(SAMPLES['zip'][0])} bytes): Read .materials/.text/photos.zip.txt, the list "
            "of what it holds; the files inside can't be opened here.",
            f"- .materials/voice.mp3: can't be opened here: an MP3 sound, {len(SAMPLES['mp3'][0])} bytes.",
            f"- .materials/clip.mov: can't be opened here: a QuickTime .mov video, {len(SAMPLES['mov'][0])} bytes.",
            f"- .materials/setup.exe: can't be opened here: a Windows program, {len(SAMPLES['exe'][0])} bytes.",
            f"- .materials/notes-utf16.txt (text, {len(SAMPLES['utf-16'][0])} bytes): Read .materials/.text/notes-utf16.txt.txt, its "
            "words in UTF-8."])
        self.assertEqual(for_claude(records[:1]), [f"- front.png (a PNG picture, {len(PNG)} bytes): Read it."], "the run's own folder")
        self.assertEqual(for_claude(records[:1], "C:/runs/work/"), [f"- C:/runs/work/front.png (a PNG picture, {len(PNG)} bytes): Read it."])
        unknown = {**records[2], "pages": None}
        self.assertEqual(for_claude([unknown])[0], f"- menu.pdf (a PDF, {len(pdf(2))} bytes): its page count could not be read: read "
                                                   "it in ranges of up to 20 pages.")

    def test_a_pdf_s_pages(self):
        self.assertEqual([pdf_pages(pdf(1)), pdf_pages(pdf(12)), pdf_pages(pdf(57, compressed=True)),
                          pdf_pages(pdf(3, linearized=True)), pdf_pages(b"%PDF-1.4\nnothing to count\n%%EOF")],
                         [1, 12, 57, 3, None])
        reordered = b"%PDF-1.4\n2 0 obj << /Count 7 /Kids [3 0 R] /Type /Pages >> endobj\n%%EOF"
        self.assertEqual(pdf_pages(reordered), 7)


class DescribeTest(Folder):
    def test_a_kept_file_described_and_its_copy_made_again(self):
        save(self.folder, "prices.xlsx", xlsx())
        copy = self.folder / ".text" / "prices.xlsx.txt"
        made = copy.read_bytes()
        copy.unlink()                                   # as a website brought back from the backup has it
        attachments._known.clear()
        record = describe(self.folder, "prices.xlsx")
        self.assertEqual((record["text"], copy.read_bytes()), (".text/prices.xlsx.txt", made))
        copy.unlink()                                   # taken away while the record is known: made again too
        self.assertEqual(describe(self.folder, "prices.xlsx")["text"], ".text/prices.xlsx.txt")
        self.assertTrue(copy.is_file())
        self.assertIsNone(describe(self.folder, "gone.pdf"))
        self.assertIn("Filter kaapi,60", text_of(self.folder, record))
        notes = save(self.folder, "notes.md", b"Call 98765 43210")
        self.assertEqual(text_of(self.folder, notes), "Call 98765 43210")
        self.assertEqual(text_of(self.folder, save(self.folder, "front.png", PNG)), "")
        self.assertEqual(text_of(self.folder, notes, most=4), "", "past what the studio reads")

    def test_a_studio_s_own_path_in_a_larger_folder(self):
        materials = self.folder / ".materials"
        save(materials, "Story.docx", SAMPLES["docx"][0])
        record = describe(self.folder, ".materials/Story.docx")
        self.assertEqual((record["path"], record["text"]), (".materials/Story.docx", ".materials/.text/Story.docx.txt"))
        self.assertTrue((self.folder / record["text"]).is_file())


class ServeTest(Folder):
    def test_view_a_picture_or_a_pdf_download_the_rest_always_sandboxed(self):
        picture = save(self.folder, "front.png", PNG)
        menu = save(self.folder, "menu.pdf", pdf(2))
        page = save(self.folder, "page.html", SAMPLES["html"][0])
        drawing = save(self.folder, "logo.svg", SAMPLES["svg"][0])
        sheet = save(self.folder, "Café prices.xlsx", xlsx())
        for record, kind, disposition in [(picture, "image/png", "inline"), (menu, "application/pdf", "inline"),
                                          (page, "application/octet-stream", "attachment"),
                                          (drawing, "application/octet-stream", "attachment"),
                                          (sheet, "application/octet-stream", "attachment")]:
            said = headers(record)
            self.assertEqual((said["Content-Type"], said["Content-Disposition"].split(";")[0]), (kind, disposition), record["name"])
            self.assertTrue(said["Content-Security-Policy"].startswith("sandbox; default-src 'none'"))
            self.assertEqual(said["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers(sheet)["Content-Disposition"],
                         "attachment; filename=\"Cafe prices.xlsx\"; filename*=UTF-8''Caf%C3%A9%20prices.xlsx")


if __name__ == "__main__":
    unittest.main()
