import json
import os
import pathlib
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import support


def no_filesystem(*_args, **_kwargs):
    raise AssertionError("the filesystem was asked about a path that should have been refused as text")


class SubjectsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        support.configure(Path(self.tmp.name))
        from app import config, evidence, subjects
        self.config = config
        self.evidence = evidence
        self.subjects = subjects
        self.target = Path(self.tmp.name) / "app.bin"
        self.target.write_bytes(b"MZ\x90\x00binary bytes")

    def tearDown(self):
        self.tmp.cleanup()

    def wait_hashed(self, slug, seconds=10):
        end = time.monotonic() + seconds
        while self.subjects.hashing(slug) and time.monotonic() < end:
            time.sleep(0.02)

    # ---- creating ----

    def test_create_and_meta(self):
        slug = self.subjects.create("Business App 1.1.4", str(self.target), "executable")
        self.assertTrue(self.subjects.exists(slug))
        record = self.subjects.meta(slug)
        self.assertEqual(record["name"], "Business App 1.1.4")
        self.assertEqual(record["target"]["kind"], "executable")
        self.assertEqual(record["target"]["size"], self.target.stat().st_size)
        self.assertEqual(len(record["target"]["sha256"]), 64)
        self.assertNotIn("exists", record["target"])   # worked out live, never stored
        self.assertTrue(self.subjects.target_exists(slug))
        self.assertIn(slug, self.subjects.slugs())

    def test_positional_signature_kept(self):
        slug = self.subjects.create("Pos", str(self.target), "executable", "", "", "pos-link")
        self.assertEqual(slug, "pos-link")

    def test_quoted_explorer_path_accepted(self):
        slug = self.subjects.create("Quoted", f'  "{self.target}"  ')
        self.assertEqual(self.subjects.meta(slug)["target"]["path"], str(self.target))

    def test_relative_path_rejected(self):
        with self.assertRaises(self.subjects.SubjectError):
            self.subjects.create("x", "app.bin", "executable")

    def test_missing_file_refused_plainly(self):
        with self.assertRaises(self.subjects.SubjectError) as caught:
            self.subjects.create("Gone", str(Path(self.tmp.name) / "nope.exe"))
        self.assertIn("no file", str(caught.exception))
        self.assertEqual(self.subjects.slugs(), [])

    def test_network_and_device_paths_refused_before_any_stat(self):
        for text in (r"\\host\share\a.exe", "//host/share/a.exe", r"\\?\UNC\host\share\a.exe",
                     r"\\.\PhysicalDrive0", r"\/host/share/a.exe", "C:\\work\\con\\a.exe", "C:\\work\\nul.txt",
                     "file://host/share/a.exe", "C:\\a\0b"):
            with mock.patch("os.stat", no_filesystem), mock.patch.object(pathlib.Path, "stat", no_filesystem), \
                    mock.patch("builtins.open", no_filesystem):
                with self.assertRaises(self.subjects.SubjectError, msg=text):
                    self.subjects.create("Share", text)

    def test_web_address_as_a_path_explained(self):
        with self.assertRaises(self.subjects.SubjectError) as caught:
            self.subjects.create("Oops", "https://example.com/app.exe", "executable")
        self.assertIn("Website", str(caught.exception))

    def test_unc_message_says_network_share(self):
        with self.assertRaises(self.subjects.SubjectError) as caught:
            self.subjects.create("Share", r"\\host\share\a.exe")
        self.assertIn("network share", str(caught.exception))

    def test_name_defaults_to_file_name(self):
        slug = self.subjects.create("", str(self.target))
        self.assertEqual(self.subjects.meta(slug)["name"], "app.bin")

    def test_folder_target_accepted(self):
        bundle = Path(self.tmp.name) / "Thing.app"
        bundle.mkdir()
        slug = self.subjects.create("Bundle", str(bundle), "archive")
        target = self.subjects.meta(slug)["target"]
        self.assertTrue(target["folder"])
        self.assertNotIn("sha256", target)
        self.assertTrue(self.subjects.target_exists(slug))
        self.assertFalse(self.subjects.hashing(slug))

    def test_free_slug_avoids_collision(self):
        a = self.subjects.create("Same Name", str(self.target))
        b = self.subjects.create("Same Name", str(self.target))
        self.assertNotEqual(a, b)

    def test_concurrent_creates_get_distinct_slugs(self):
        made, errors = [], []

        def make():
            try:
                made.append(self.subjects.create("Racer", str(self.target)))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=make) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(made)), 6)

    def test_stable_bytes(self):
        slug = self.subjects.create("Stable", str(self.target))
        raw = self.subjects.meta_path(slug).read_text(encoding="utf-8")
        self.assertTrue(raw.endswith("\n"))
        self.assertNotIn("\r", raw)
        self.assertLess(raw.index('"created"'), raw.index('"id"'))

    # ---- open_binary options (cov-15) ----

    def test_raw_binary_needs_architecture_and_entry_point(self):
        with self.assertRaises(self.subjects.SubjectError):
            self.subjects.create("Raw", str(self.target), fmt="raw-binary")
        with self.assertRaises(self.subjects.SubjectError):
            self.subjects.create("Raw", str(self.target), fmt="raw-binary", architecture="x86_64")
        with self.assertRaises(self.subjects.SubjectError):
            self.subjects.create("Raw", str(self.target), fmt="raw-binary", architecture="vax", entry_point="0x10")
        with self.assertRaises(self.subjects.SubjectError):
            self.subjects.create("Raw", str(self.target), fmt="raw-binary", architecture="arm", entry_point="8000")

    def test_open_arguments_carry_format(self):
        slug = self.subjects.create("Raw", str(self.target), "executable", fmt="raw-binary",
                                    architecture="x86_64", entry_point="0x8000")
        args = self.subjects.open_arguments(slug)
        self.assertEqual(args, {"path": str(self.target), "format": "raw-binary",
                                "architecture": "x86_64", "entry_point": "0x8000"})

    def test_other_format_rules(self):
        with self.assertRaises(self.subjects.SubjectError):
            self.subjects.create("Dol", str(self.target), fmt="gamecube-dol", architecture="powerpc")
        slug = self.subjects.create("Dol", str(self.target), fmt="gamecube-dol", entry_point="0x80003100")
        self.assertEqual(self.subjects.open_arguments(slug)["entry_point"], "0x80003100")
        with self.assertRaises(self.subjects.SubjectError):
            self.subjects.create("Com", str(self.target), fmt="dos-com", entry_point="0x100")
        with self.assertRaises(self.subjects.SubjectError):
            self.subjects.create("Elf", str(self.target), architecture="arm")
        with self.assertRaises(self.subjects.SubjectError):
            self.subjects.create("Odd", str(self.target), fmt="coff")
        slug = self.subjects.create("Detect", str(self.target))
        self.assertEqual(self.subjects.open_arguments(slug), {"path": str(self.target)})

    # ---- websites ----

    def test_website(self):
        slug = self.subjects.create("", "https://example.com/app", "website")
        record = self.subjects.meta(slug)
        self.assertEqual(record["name"], "example.com")
        self.assertEqual(record["target"], {"url": "https://example.com/app", "kind": "website"})
        self.assertTrue(self.subjects.target_exists(slug))
        for bad in ("ftp://example.com", "https://user:pw@example.com/", "https:///nohost", "example.com",
                    "https://exa mple.com", "javascript:alert(1)"):
            with self.assertRaises(self.subjects.SubjectError, msg=bad):
                self.subjects.create("Site", bad, "website")
        with self.assertRaises(self.subjects.SubjectError):
            self.subjects.create("Site", "https://example.com", "website", fmt="raw-binary")

    # ---- helpers ----

    def test_clean_path(self):
        clean = self.subjects.clean_path
        self.assertEqual(clean('  "C:\\a b\\x.apk" '), "C:\\a b\\x.apk")
        self.assertEqual(clean("'C:\\x.exe'"), "C:\\x.exe")
        self.assertEqual(clean("C:\\x.exe"), "C:\\x.exe")
        self.assertEqual(clean(None), "")
        self.assertEqual(clean('"'), '"')
        if os.name == "nt":
            self.assertEqual(clean("file:///C:/Tools/a%20b.exe"), "C:\\Tools\\a b.exe")
        self.assertTrue(clean("file://host/share/a.exe").startswith("\\\\host"))

    def test_guess_kind(self):
        guess = self.subjects.guess_kind
        self.assertEqual(guess("https://example.com"), "website")
        self.assertEqual(guess('"C:\\apps\\Thing.APK"'), "archive")
        self.assertEqual(guess("C:\\apps\\app.asar"), "archive")
        self.assertEqual(guess("C:\\apps\\Bundle.app\\"), "archive")
        self.assertEqual(guess("C:\\bin\\tool.exe"), "executable")
        self.assertEqual(guess("/usr/lib/libx.so"), "executable")
        self.assertEqual(guess("C:\\data\\store.sqlite"), "database")
        self.assertEqual(guess("C:\\caps\\trace.pcapng"), "artifact")
        self.assertEqual(guess("C:\\fw\\image.bin"), "artifact")
        self.assertEqual(guess("C:\\bin\\noextension"), "executable")

    def test_work_dir(self):
        slug = self.subjects.create("Worker", str(self.target))
        work = self.subjects.work_dir(slug)
        self.assertTrue(work.is_dir())
        self.assertEqual(work, self.subjects.folder(slug) / "work")
        with self.assertRaises(self.subjects.SubjectError):
            self.subjects.work_dir("nobody-here")

    # ---- the target, live (COR-14, COR-21) ----

    def test_target_exists_is_live(self):
        slug = self.subjects.create("Live", str(self.target))
        self.assertTrue(self.subjects.target_exists(slug))
        moved = self.target.with_suffix(".moved")
        self.target.rename(moved)
        self.assertFalse(self.subjects.target_exists(slug))
        self.assertFalse(self.subjects.target_info(slug)["exists"])
        moved.rename(self.target)
        self.assertTrue(self.subjects.target_exists(slug))

    def test_big_target_hashed_in_background(self):
        big = Path(self.tmp.name) / "firmware.img"
        big.write_bytes(b"\x00" * 4096)
        gate = threading.Event()
        real = self.subjects._sha256

        def slow(path):
            gate.wait(5)
            return real(path)

        with mock.patch.object(self.subjects, "HASH_INLINE_MAX", 1024), mock.patch.object(self.subjects, "_sha256", slow):
            slug = self.subjects.create("Big", str(big))
            self.assertNotIn("sha256", self.subjects.meta(slug)["target"])   # created at once, not after the hash
            self.assertTrue(self.subjects.hashing(slug))
            self.assertTrue(self.subjects.target_info(slug)["hashing"])
            gate.set()
            self.wait_hashed(slug)
        import hashlib
        self.assertEqual(self.subjects.meta(slug)["target"]["sha256"], hashlib.sha256(big.read_bytes()).hexdigest())
        self.assertFalse(self.subjects.hashing(slug))

    def test_interrupted_hash_resumes(self):
        big = Path(self.tmp.name) / "disk.vhd"
        big.write_bytes(b"\x01" * 2048)
        with mock.patch.object(self.subjects, "HASH_INLINE_MAX", 1024), \
                mock.patch.object(self.subjects, "_hash_later", lambda *a: False):
            slug = self.subjects.create("Resume", str(big))   # as if the studio closed before hashing
        self.assertNotIn("sha256", self.subjects.meta(slug)["target"])
        self.subjects.hashing(slug)
        self.wait_hashed(slug)
        self.assertEqual(len(self.subjects.meta(slug)["target"]["sha256"]), 64)

    def test_changed_target_and_rehash(self):
        slug = self.subjects.create("Changes", str(self.target))
        self.assertFalse(self.subjects.target_info(slug)["changed"])
        self.target.write_bytes(b"a different, longer binary")
        info = self.subjects.target_info(slug)
        self.assertTrue(info["changed"])
        old_sha = self.subjects.meta(slug)["target"]["sha256"]
        self.subjects.rehash(slug)
        info = self.subjects.target_info(slug)
        self.assertFalse(info["changed"])
        self.assertNotEqual(info["sha256"], old_sha)

    # ---- the conversation (COR-03) ----

    def test_conversation(self):
        slug = self.subjects.create("Chatty", str(self.target))
        self.subjects.add_message(slug, "user", "hello")
        self.subjects.add_message(slug, "claude", "hi")
        thread = self.subjects.thread(slug)
        self.assertEqual([m["role"] for m in thread], ["user", "claude"])
        self.subjects.clear_thread(slug)
        self.assertEqual(self.subjects.thread(slug), [])

    def test_unreadable_conversation_is_never_overwritten(self):
        slug = self.subjects.create("Fragile", str(self.target))
        self.subjects.add_message(slug, "user", "one")
        path = self.subjects.thread_path(slug)
        path.write_text('[{"id": "x", "role": "user", "text": "one"', encoding="utf-8")  # half-written
        before = path.read_bytes()
        with self.assertRaises(self.subjects.SubjectError):
            self.subjects.add_message(slug, "user", "two")
        with self.assertRaises(self.subjects.SubjectError):
            self.subjects.thread(slug)
        self.assertEqual(path.read_bytes(), before)

    def test_concurrent_writers_lose_nothing(self):
        slug = self.subjects.create("Busy", str(self.target))
        errors = []

        def write(n):
            try:
                for i in range(25):
                    self.subjects.add_message(slug, "user", f"{n}-{i}")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def read():
            try:
                for _ in range(50):
                    self.subjects.thread(slug)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(n,)) for n in range(6)] + [threading.Thread(target=read)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.subjects.thread(slug)), 150)
        leftovers = [p.name for p in self.subjects.folder(slug).iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    # ---- rename and remove (COR-13, SEC-09) ----

    def test_rename_and_remove(self):
        slug = self.subjects.create("Before", str(self.target))
        self.subjects.rename(slug, "After")
        self.assertEqual(self.subjects.meta(slug)["name"], "After")
        self.subjects.remove(slug)
        self.assertFalse(self.subjects.exists(slug))
        self.assertFalse(self.subjects.folder(slug).exists())
        self.assertTrue(self.target.is_file())   # the target itself is never touched

    def test_remove_deletes_findings_and_web_profile(self):
        slug = self.subjects.create("Site", "https://example.com", "website")
        self.evidence.add(slug, {"tool": "inspect_web_page", "evidence": {"ok": True}})
        profile = self.config.web_profiles_dir() / slug
        (profile / "Default").mkdir(parents=True)
        (profile / "Default" / "Cookies").write_bytes(b"session=secret")
        os.chmod(profile / "Default" / "Cookies", 0o444)   # read-only files go too
        rev = self.evidence.revision(slug)
        self.subjects.remove(slug)
        self.assertFalse(profile.exists())
        self.assertNotEqual(self.evidence.revision(slug), rev)
        again = self.subjects.create("Site", "https://example.org", "website")
        self.assertEqual(again, slug)
        self.assertEqual(self.evidence.listing(again)["total"], 0)

    def test_leftover_web_profile_is_never_inherited(self):
        (self.config.web_profiles_dir() / "site").mkdir(parents=True)
        slug = self.subjects.create("Site", "https://example.com", "website")
        self.assertEqual(slug, "site-2")


if __name__ == "__main__":
    unittest.main()
