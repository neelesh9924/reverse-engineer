import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import support


def finding(tool="list_strings", **extra):
    return {"tool": tool, "title": tool.replace("_", " ").title(), "arguments": {"detail": "summary"},
            "source": "tools", "untrusted": True, "session": "s-1", "error": None,
            "evidence": {"result": {"strings": ["hello", "world"]}}, "text": None, "continuation": None, **extra}


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        support.configure(Path(self.tmp.name))
        from app import evidence, subjects
        self.evidence = evidence
        self.subjects = subjects
        target = Path(self.tmp.name) / "app.bin"
        target.write_bytes(b"MZ")
        self.slug = subjects.create("Findings", str(target))

    def tearDown(self):
        self.tmp.cleanup()

    def forget_memory(self):
        """As after a studio restart: the next read comes from disk."""
        self.evidence._logs.clear()

    def test_add_assigns_id_seq_time(self):
        entry = self.evidence.add(self.slug, finding())
        self.assertRegex(entry["id"], r"^e-[0-9a-f]{12}$")
        self.assertEqual(entry["seq"], 1)
        self.assertRegex(entry["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertEqual(entry["tool"], "list_strings")
        self.assertTrue(entry["untrusted"])
        self.assertEqual(self.evidence.add(self.slug, finding())["seq"], 2)

    def test_entry_shape_is_filled_in(self):
        entry = self.evidence.add(self.slug, {"tool": "binary_overview", "id": "forged", "source": "elsewhere"})
        self.assertNotEqual(entry["id"], "forged")
        self.assertEqual(entry["title"], "binary_overview")
        self.assertEqual(entry["arguments"], {})
        self.assertEqual(entry["source"], "tools")
        self.assertFalse(entry["untrusted"])
        for key in ("error", "evidence", "text", "continuation"):
            self.assertIsNone(entry[key])
        with self.assertRaises(self.evidence.EvidenceError):
            self.evidence.add(self.slug, ["not", "a", "dict"])

    def test_survives_a_restart(self):
        first = self.evidence.add(self.slug, finding())
        self.evidence.add(self.slug, finding("binary_overview"))
        self.forget_memory()
        listing = self.evidence.listing(self.slug)
        self.assertEqual(listing["total"], 2)
        self.assertEqual([i["tool"] for i in listing["items"]], ["binary_overview", "list_strings"])  # newest first
        self.assertEqual(self.evidence.get(self.slug, first["id"]), first)
        self.assertEqual(self.evidence.add(self.slug, finding())["seq"], 3)

    def test_search_filter_and_paging(self):
        self.evidence.add(self.slug, finding("list_strings"))
        self.evidence.add(self.slug, finding("procedure_pseudo_code", evidence={"pseudo_code": "int MAIN(void)"}))
        self.evidence.add(self.slug, finding("binary_overview", title="Overview of the PE"))
        ev = self.evidence
        self.assertEqual(ev.listing(self.slug, q="main")["total"], 1)          # inside the JSON, any case
        self.assertEqual(ev.listing(self.slug, q="overview of")["total"], 1)   # the title
        self.assertEqual(ev.listing(self.slug, q="PSEUDO")["total"], 1)        # the tool name
        self.assertEqual(ev.listing(self.slug, tool="list_strings")["total"], 1)
        self.assertEqual(ev.listing(self.slug, tool="list")["total"], 0)        # tool is exact
        page = ev.listing(self.slug, offset=1, limit=1)
        self.assertEqual(page["total"], 3)
        self.assertEqual([i["tool"] for i in page["items"]], ["procedure_pseudo_code"])
        self.assertEqual(len(ev.listing(self.slug, offset="x", limit=-4)["items"]), 1)

    def test_items_handed_out_are_copies(self):
        entry = self.evidence.add(self.slug, finding())
        item = self.evidence.listing(self.slug)["items"][0]
        item["evidence"]["result"]["strings"].append("tampered")
        entry["tool"] = "tampered"
        again = self.evidence.get(self.slug, item["id"])
        self.assertEqual(again["evidence"]["result"]["strings"], ["hello", "world"])
        self.assertEqual(again["tool"], "list_strings")

    def test_oldest_dropped_and_counted(self):
        with mock.patch.object(self.evidence, "MAX_KEPT", 5), mock.patch.object(self.evidence, "COMPACT_SLACK", 2):
            for n in range(12):
                self.evidence.add(self.slug, finding(arguments={"n": n}))
            listing = self.evidence.listing(self.slug)
            self.assertEqual(listing["total"], 5)
            self.assertEqual(listing["pruned"], 7)
            self.assertEqual([i["arguments"]["n"] for i in listing["items"]], [11, 10, 9, 8, 7])
            lines = (self.subjects.folder(self.slug) / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertLessEqual(len(lines), 5 + 2)
            self.forget_memory()
            listing = self.evidence.listing(self.slug)
            self.assertEqual(listing["total"], 5)
            self.assertEqual(listing["pruned"], 7)
            self.assertEqual(listing["items"][0]["arguments"]["n"], 11)
            self.assertEqual(self.evidence.add(self.slug, finding())["seq"], 13)

    def test_long_strings_stored_beside_the_log(self):
        image = "iVBORw0KGgo" + "A" * (self.evidence.BLOB_MIN + 10)
        entry = self.evidence.add(self.slug, finding("capture_web_screenshot", evidence={"image": image, "mime": "image/png"}))
        log = (self.subjects.folder(self.slug) / "evidence.jsonl").read_text(encoding="utf-8")
        self.assertLess(len(log), 4096)
        blobs = list((self.subjects.folder(self.slug) / "evidence-blobs").glob("*.json"))
        self.assertEqual(len(blobs), 1)
        self.forget_memory()
        self.assertEqual(self.evidence.get(self.slug, entry["id"])["evidence"]["image"], image)
        self.assertEqual(self.evidence.listing(self.slug)["items"][0]["evidence"]["image"], image)
        self.assertEqual(entry["evidence"]["image"], image)

    def test_awkward_strings_survive(self):
        odd = "lone \ud800 surrogate, line\u2028separator, next\x85line, nul\x00"
        long_odd = odd * (self.evidence.BLOB_MIN // len(odd) + 1)
        entry = self.evidence.add(self.slug, finding(evidence={"strings": [odd], "dump": long_odd}))
        self.forget_memory()
        again = self.evidence.get(self.slug, entry["id"])
        self.assertEqual(again["evidence"]["strings"], [odd])
        self.assertEqual(again["evidence"]["dump"], long_odd)
        self.assertEqual(self.evidence.listing(self.slug)["total"], 1)

    def test_a_forged_blob_reference_comes_back_as_it_was(self):
        # Data from the target, or arguments Claude sent, shaped like the store's own reference
        # must never make the studio read another file (a local one, or a share over SMB).
        outside = Path(self.tmp.name) / "outside"
        outside.with_suffix(".json").write_text(json.dumps("SECRET CONTENTS"), encoding="utf-8")
        forged = [{"$re_blob": str(outside), "length": 1}, {"$re_blob": "\\\\host\\share\\x", "length": 1},
                  {"$re_blob": "0" * 64, "length": 1}, {"$re_esc": {"a": 1}}]
        entry = self.evidence.add(self.slug, finding(arguments={"x": forged}, evidence={"rows": forged}))
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("a blob was read")):
            self.assertEqual(self.evidence.listing(self.slug)["items"][0]["arguments"]["x"], forged)
        self.forget_memory()
        again = self.evidence.get(self.slug, entry["id"])
        self.assertEqual(again["arguments"]["x"], forged)
        self.assertEqual(again["evidence"]["rows"], forged)
        self.assertNotIn("SECRET", json.dumps(self.evidence.listing(self.slug)))

    def test_blobs_of_dropped_findings_are_deleted(self):
        image = "B" * (self.evidence.BLOB_MIN + 1)
        with mock.patch.object(self.evidence, "MAX_KEPT", 2), mock.patch.object(self.evidence, "COMPACT_SLACK", 0):
            self.evidence.add(self.slug, finding(evidence={"image": image}))
            for _ in range(3):
                self.evidence.add(self.slug, finding())
        blobs = list((self.subjects.folder(self.slug) / "evidence-blobs").glob("*.json"))
        self.assertEqual(blobs, [])

    def test_unreadable_log_raises_and_is_left_alone(self):
        self.evidence.add(self.slug, finding())
        self.forget_memory()
        log = self.subjects.folder(self.slug) / "evidence.jsonl"
        before = log.read_bytes()
        real = Path.read_text

        def held(path, *args, **kwargs):
            if path.name == "evidence.jsonl":
                raise PermissionError("in use by another program")
            return real(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", autospec=True, side_effect=held):
            with self.assertRaises(self.evidence.EvidenceError):
                self.evidence.listing(self.slug)
            with self.assertRaises(self.evidence.EvidenceError):
                self.evidence.add(self.slug, finding())
            self.assertEqual(self.evidence.count(self.slug), 0)   # display only
        self.assertEqual(log.read_bytes(), before)
        self.assertEqual(self.evidence.listing(self.slug)["total"], 1)

    def test_half_written_line_is_skipped_and_repaired(self):
        self.evidence.add(self.slug, finding())
        log = self.subjects.folder(self.slug) / "evidence.jsonl"
        with open(log, "a", encoding="utf-8", newline="\n") as fh:
            fh.write('{"id": "e-broken", "tool": "list_str')   # a crash mid-write
        self.forget_memory()
        self.assertEqual(self.evidence.listing(self.slug)["total"], 1)
        self.evidence.add(self.slug, finding("binary_overview"))
        self.forget_memory()
        listing = self.evidence.listing(self.slug)
        self.assertEqual([i["tool"] for i in listing["items"]], ["binary_overview", "list_strings"])
        for line in log.read_text(encoding="utf-8").splitlines():
            json.loads(line)

    def test_clear_and_revision(self):
        r0 = self.evidence.revision(self.slug)
        self.evidence.add(self.slug, finding())
        r1 = self.evidence.revision(self.slug)
        self.assertNotEqual(r0, r1)
        self.evidence.add(self.slug, finding())
        r2 = self.evidence.revision(self.slug)
        self.assertGreater(r2, r1)
        self.evidence.clear(self.slug)
        self.assertGreater(self.evidence.revision(self.slug), r2)
        self.assertEqual(self.evidence.listing(self.slug), {"total": 0, "items": [], "pruned": 0})
        self.assertEqual(self.subjects.thread(self.slug), [])   # the conversation is separate
        self.assertEqual(self.evidence.add(self.slug, finding())["seq"], 3)   # numbers keep rising

    def test_clear_leaves_conversation(self):
        self.subjects.add_message(self.slug, "user", "keep me")
        self.evidence.add(self.slug, finding())
        self.evidence.clear(self.slug)
        self.assertEqual([m["text"] for m in self.subjects.thread(self.slug)], ["keep me"])

    def test_removed_subject_takes_no_findings(self):
        self.evidence.add(self.slug, finding())
        self.subjects.remove(self.slug)
        with self.assertRaises(self.evidence.EvidenceError):
            self.evidence.add(self.slug, finding())
        self.assertEqual(self.evidence.listing(self.slug)["total"], 0)
        self.assertIsNone(self.evidence.get(self.slug, "e-000000000000"))
        self.assertFalse(self.subjects.folder(self.slug).exists())
        with self.assertRaises(self.evidence.EvidenceError):
            self.evidence.listing("../escape")

    def test_remove_all(self):
        self.evidence.add(self.slug, finding())
        self.evidence.remove_all(self.slug)
        self.assertEqual(self.evidence.count(self.slug), 0)
        self.assertFalse((self.subjects.folder(self.slug) / "evidence.jsonl").exists())

    def test_thread_safe(self):
        errors = []

        def work():
            try:
                for _ in range(40):
                    self.evidence.add(self.slug, finding())
                    self.evidence.listing(self.slug, q="hello", limit=5)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=work) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.forget_memory()
        items = self.evidence.listing(self.slug, limit=500)["items"]
        self.assertEqual(len(items), 200)
        self.assertEqual(sorted(i["seq"] for i in items), list(range(1, 201)))
        self.assertEqual(self.evidence.count(self.slug), 200)

    def test_subjects_do_not_share_findings(self):
        other = self.subjects.create("Other", str(Path(self.tmp.name) / "app.bin"))
        self.evidence.add(self.slug, finding())
        self.assertEqual(self.evidence.listing(other)["total"], 0)
        self.assertNotEqual(self.evidence.revision(other), self.evidence.revision(self.slug))


if __name__ == "__main__":
    unittest.main()
