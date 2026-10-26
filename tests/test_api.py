"""The HTTP API the page uses, endpoint by endpoint, against the fake engine: findings and their
report, tool defaults, confirmation, runs with progress and cancel, workflow completion, debug
sessions, engine open/close, and analysis settings re-applied after each start. Also the pure
helpers that turn an MCP result into a finding."""
import json
import tempfile
import threading
import unittest
from pathlib import Path

import support


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.httpd, self.client = support.start_server(self.root)
        self.target = self.root / "targets" / "app.apk"
        self.target.parent.mkdir()
        self.target.write_bytes(b"PK\x03\x04 sample")
        self.slug = self.new_subject("App", self.target)

    def tearDown(self):
        support.stop_server(self.httpd)
        self.tmp.cleanup()

    def new_subject(self, name, path):
        _, reply = self.client.post("/api/subjects/new", {"name": name, "path": str(path), "kind": "archive"})
        self.assertTrue(reply.get("ok"), reply)
        return reply["slug"]

    def run_tool(self, name, arguments=None, slug=None, **extra):
        _, reply = self.client.post("/api/tool/run", {"slug": slug or self.slug, "name": name,
                                                      "arguments": arguments or {}, **extra})
        return reply

    def findings(self, query=""):
        return self.client.get(f"/api/evidence?slug={self.slug}{query}")[1]

    # ---- findings ----

    def test_findings_are_listed_newest_first_and_filtered(self):
        self.run_tool("binary_overview")
        self.run_tool("list_strings", {"detail": "complete"})
        self.run_tool("procedure_pseudo_code", {"procedure": "main"})
        found = self.findings()
        self.assertEqual(found["total"], 3)
        self.assertEqual([e["tool"] for e in found["items"]], ["procedure_pseudo_code", "list_strings", "binary_overview"])
        self.assertEqual(self.findings("&tool=list_strings")["total"], 1)
        self.assertEqual(self.findings("&q=return%200")["items"][0]["tool"], "procedure_pseudo_code")
        page = self.findings("&offset=1&limit=1")
        self.assertEqual((page["total"], len(page["items"])), (3, 1))
        self.assertEqual(page["items"][0]["tool"], "list_strings")
        self.assertEqual(self.client.brief(self.slug)["evidence_count"], 3)

    def test_entry_shape_untrusted_and_titles(self):
        entry = self.run_tool("list_strings")["entry"]
        for key in ("id", "seq", "at", "tool", "title", "arguments", "source", "untrusted", "session", "error",
                    "evidence", "text", "continuation"):
            self.assertIn(key, entry)
        self.assertEqual(entry["title"], "List Strings")
        self.assertTrue(entry["untrusted"], "REA's untrusted_content marker is kept")
        self.assertFalse(self.run_tool("binary_overview")["entry"]["untrusted"])

    def test_error_contract_is_parsed(self):
        reply = self.run_tool("inspect_kernel_module", {"path": str(self.target)})
        self.assertTrue(reply.get("ok"), reply)
        error = reply["entry"]["error"]
        self.assertEqual(error["code"], "provider_unavailable")
        self.assertEqual(error["category"], "provider")
        self.assertFalse(error["retryable"])
        self.assertEqual(error["remediation"], {"action": "Install the provider."})
        self.assertIsNone(reply["entry"]["evidence"])
        runs = [r for r in self.client.state()["runs"].values() if r["tool"] == "inspect_kernel_module"]
        self.assertEqual([r["status"] for r in runs], ["error"])

    def test_record_false_keeps_no_finding(self):
        reply = self.run_tool("binary_overview", record=False)
        self.assertTrue(reply.get("ok"), reply)
        self.assertIsNone(reply["entry"])
        self.assertEqual(self.findings()["total"], 0)

    def test_source_is_kept(self):
        self.assertEqual(self.run_tool("binary_overview", source="panel")["entry"]["source"], "panel")
        self.assertEqual(self.run_tool("binary_overview", source="claude")["entry"]["source"], "tools")

    def test_clear_findings(self):
        self.run_tool("binary_overview")
        before = self.client.brief(self.slug)["evidence_rev"]
        _, reply = self.client.post("/api/evidence/clear", {"slug": self.slug})
        self.assertTrue(reply.get("ok"), reply)
        self.assertEqual(self.findings()["total"], 0)
        self.assertGreater(self.client.brief(self.slug)["evidence_rev"], before)

    def test_report_downloads(self):
        self.run_tool("binary_overview")
        self.run_tool("inspect_kernel_module", {"path": str(self.target)})
        status, headers, body = self.client.fetch(f"/api/evidence/report?slug={self.slug}&format=md")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn(f"{self.slug}-findings.md", headers["Content-Disposition"])
        text = body.decode("utf-8")
        self.assertIn("# App: findings", text)
        self.assertLess(text.index("Binary Overview"), text.index("Inspect Kernel Module"), "oldest first")
        self.assertIn("This is a stand-in engine for tests.", text)
        self.assertIn("No kernel module provider.", text)
        status, headers, body = self.client.fetch(f"/api/evidence/report?slug={self.slug}&format=json")
        self.assertEqual(status, 200)
        self.assertIn(f"{self.slug}-findings.json", headers["Content-Disposition"])
        payload = json.loads(body)
        self.assertEqual([f["tool"] for f in payload["findings"]], ["binary_overview", "inspect_kernel_module"])
        self.assertEqual(payload["subject"]["name"], "App")
        status, _, _ = self.client.fetch("/api/evidence/report?slug=nope&format=md")
        self.assertEqual(status, 404)

    # ---- defaults ----

    def defaults(self, name, slug=None):
        return self.client.get(f"/api/tool/defaults?slug={slug or self.slug}&name={name}")[1]

    def test_defaults_fill_the_target_and_outputs(self):
        from app import subjects
        work = subjects.work_dir(self.slug)
        opened = self.defaults("open_binary")
        self.assertEqual(opened["arguments"], {"path": str(self.target)})
        self.assertIn("path", opened["notes"][0])
        self.assertEqual(self.defaults("analyze_javascript_application")["arguments"]["input_path"], str(self.target))
        self.assertEqual(self.defaults("install_adb_package")["arguments"]["apk_path"], str(self.target))
        out = Path(self.defaults("decompile_all")["arguments"]["output_directory"])
        self.assertEqual(out.parent, work)
        self.assertTrue(out.name.startswith("decompile_all-"))
        self.assertFalse(out.exists(), "REA wants the output directory absent")
        exported = Path(self.defaults("export_evidence_bundle")["arguments"]["path"])
        self.assertEqual(exported.parent, work)
        self.assertNotIn("path", self.defaults("import_evidence_bundle")["arguments"])
        self.assertEqual(self.defaults("binary_overview"), {"arguments": {}, "notes": []})
        self.assertIn("error", self.defaults("rm_rf"))

    # ---- confirmation, start, runs ----

    def test_destructive_tools_need_confirmation(self):
        arguments = {"serial": "emulator-5554", "apk_path": str(self.target)}
        reply = self.run_tool("install_adb_package", arguments)
        self.assertTrue(reply.get("needs_confirmation"), reply)
        self.assertEqual(self.findings()["total"], 0)
        reply = self.run_tool("install_adb_package", arguments, confirmed="yes")
        self.assertTrue(reply.get("needs_confirmation"), "only true confirms")
        reply = self.run_tool("install_adb_package", arguments, confirmed=True)
        self.assertTrue(reply.get("ok"), reply)

    def test_start_false_never_starts_an_engine(self):
        reply = self.run_tool("binary_overview", start=False)
        self.assertTrue(reply.get("not_running"), reply)
        self.assertEqual(self.client.brief(self.slug)["engine"]["state"], "stopped")
        self.client.post("/api/subject/open", {"slug": self.slug})
        self.assertTrue(self.run_tool("binary_overview", start=False).get("ok"))

    def test_bad_run_id_refused(self):
        self.assertIn("error", self.run_tool("binary_overview", run_id="../x"))

    def test_run_progress_and_cancel(self):
        run_id = "run-cancel-1"
        out = self.root / "out"
        replies = []
        worker = threading.Thread(target=lambda: replies.append(self.run_tool(
            "decompile_all", {"output_directory": str(out), "seconds": 10}, run_id=run_id)))
        worker.start()
        run = support.wait_for(lambda: (self.client.state()["runs"].get(run_id) or {}).get("progress")
                               and self.client.state()["runs"][run_id], 15, 0.1, "no progress for the run")
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["tool"], "decompile_all")
        _, reply = self.client.post("/api/tool/cancel", {"slug": self.slug, "run_id": run_id})
        self.assertTrue(reply.get("ok"), reply)
        worker.join(15)
        self.assertFalse(worker.is_alive(), "the cancelled run did not end")
        result = replies[0]
        if "result" in result:
            self.assertTrue(result["result"]["isError"])
            self.assertEqual(result["entry"]["error"]["code"], "cancelled")
        self.assertEqual(self.client.state()["runs"][run_id]["status"], "cancelled")

    def test_engine_is_not_swapped_away_from_a_running_call(self):
        other = self.root / "targets" / "other.bin"
        other.write_bytes(b"MZ")
        other_slug = self.new_subject("Other", other)
        worker = threading.Thread(target=lambda: self.run_tool(
            "decompile_all", {"output_directory": str(self.root / "o2"), "seconds": 3}, run_id="busy-1"))
        worker.start()
        support.wait_for(lambda: (self.client.state()["runs"].get("busy-1") or {}).get("status") == "running", 10)
        _, reply = self.client.post("/api/subject/open", {"slug": other_slug})
        self.assertIn("busy", reply.get("error", ""))
        _, removed = self.client.post("/api/remove", {"slug": self.slug})
        self.assertIn("error", removed)
        _, closed = self.client.post("/api/subject/close", {"slug": self.slug, "what": "engine"})
        self.assertIn("error", closed)
        worker.join(15)

    # ---- engine, sessions, workflows ----

    def test_open_and_close_the_engine(self):
        _, reply = self.client.post("/api/subject/open", {"slug": self.slug})
        self.assertTrue(reply.get("ok"), reply)
        self.assertEqual(self.client.brief(self.slug)["engine"]["state"], "running")
        _, reply = self.client.post("/api/subject/close", {"slug": self.slug, "what": "engine"})
        self.assertTrue(reply.get("ok"), reply)
        self.assertEqual(self.client.brief(self.slug)["engine"]["state"], "stopped")
        _, reply = self.client.post("/api/subject/close", {"slug": self.slug, "what": "everything"})
        self.assertIn("error", reply)

    def test_findings_name_their_engine_session(self):
        first = self.run_tool("binary_overview")["entry"]["session"]
        self.client.post("/api/subject/close", {"slug": self.slug, "what": "engine"})
        second = self.run_tool("binary_overview")["entry"]["session"]
        self.assertTrue(first and second)
        self.assertNotEqual(first, second)

    def test_sessions_follow_start_and_close(self):
        self.assertEqual(self.client.get(f"/api/sessions?slug={self.slug}")[1], {"sessions": []})
        started = self.run_tool("start_frida_session", {"mode": "spawn", "program": str(self.target)}, confirmed=True)
        self.assertTrue(started.get("ok"), started)
        sessions = self.client.get(f"/api/sessions?slug={self.slug}")[1]["sessions"]
        self.assertEqual([(s["kind"], s["session_id"], s["tool"]) for s in sessions],
                         [("frida", "frida-1", "start_frida_session")])
        closed = self.run_tool("close_frida_session", {"session_id": "frida-1"}, confirmed=True)
        self.assertTrue(closed.get("ok"), closed)
        self.assertEqual(self.client.get(f"/api/sessions?slug={self.slug}")[1]["sessions"], [])

    def test_sessions_end_with_their_engine(self):
        self.run_tool("start_frida_session", {"mode": "spawn"}, confirmed=True)
        self.client.post("/api/subject/close", {"slug": self.slug, "what": "engine"})
        self.assertEqual(self.client.get(f"/api/sessions?slug={self.slug}")[1]["sessions"], [])
        self.client.post("/api/subject/open", {"slug": self.slug})
        self.assertEqual(self.client.get(f"/api/sessions?slug={self.slug}")[1]["sessions"], [],
                         "a session from an earlier engine is not offered as open")

    def test_prompt_completion(self):
        body = {"slug": self.slug, "name": "investigate_feature", "argument": "procedure", "value": "ma",
                "context": {"arguments": {"feature": "login"}}}
        _, reply = self.client.post("/api/prompt/complete", body)
        self.assertEqual(reply, {"values": []}, "no suggestions while the engine is stopped")
        self.client.post("/api/subject/open", {"slug": self.slug})
        _, reply = self.client.post("/api/prompt/complete", body)
        self.assertEqual(reply["values"], ["main", "sub_401000"])
        _, reply = self.client.post("/api/prompt/complete", {**body, "name": "nope"})
        self.assertEqual(reply, {"values": []})

    def test_analysis_settings_are_applied_after_each_start(self):
        _, saved = self.client.post("/api/settings", {"values": {
            "analysis_settings": {"ghidra.decompile_timeout_seconds": 120}}})
        self.assertTrue(saved.get("ok"), saved)
        self.client.post("/api/subject/open", {"slug": self.slug})
        studio = self.httpd.studio
        self.assertEqual(studio.analysis[self.slug]["applied"], ["ghidra.decompile_timeout_seconds"])
        self.assertEqual(studio.analysis[self.slug]["error"], "")
        first = studio.analysis[self.slug]["session"]
        self.client.post("/api/subject/close", {"slug": self.slug, "what": "engine"})
        self.client.post("/api/subject/open", {"slug": self.slug})
        self.assertNotEqual(studio.analysis[self.slug]["session"], first, "applied again for the new engine")
        self.assertEqual(studio.analysis[self.slug]["applied"], ["ghidra.decompile_timeout_seconds"])
        self.assertEqual(self.findings()["total"], 0, "applying settings is not a finding")

    def test_unknown_subject_is_refused_everywhere(self):
        for path, body in (("/api/tool/run", {"name": "binary_overview"}), ("/api/tool/cancel", {}),
                           ("/api/evidence/clear", {}), ("/api/subject/open", {}), ("/api/clear", {}),
                           ("/api/ask", {"text": "hi"})):
            _, reply = self.client.post(path, {"slug": "nope", **body})
            self.assertIn("error", reply, path)
        self.assertEqual(self.client.get("/api/thread?slug=nope")[1]["messages"], [])
        self.assertEqual(self.client.get("/api/sessions?slug=nope")[1], {"sessions": []})

    def test_unreadable_findings_get_an_answer(self):
        from unittest import mock
        from app import evidence
        self.client.post("/api/subject/open", {"slug": self.slug})
        failing = mock.patch.object(evidence, "listing", side_effect=evidence.EvidenceError("The findings could not be read."))
        with failing:
            status, reply = self.client.get(f"/api/evidence?slug={self.slug}")
            self.assertEqual((status, reply), (200, {"error": "The findings could not be read."}))
            status, reply = self.client.get(f"/api/evidence/report?slug={self.slug}&format=md")
            self.assertEqual(status, 503)
            self.assertIn("could not be read", reply["error"])
            status, reply = self.client.get(f"/api/sessions?slug={self.slug}")
            self.assertEqual(reply["sessions"], [])
            self.assertIn("could not be read", reply["error"])

    def test_closing_waits_for_the_checks(self):
        support.stop_server(self.httpd)
        thread = self.httpd.studio._checks_thread
        self.assertFalse(thread is not None and thread.is_alive(), "the checks still read REA's files")
        self.httpd, self.client = support.start_server(self.root)

    def test_a_subject_is_reserved_while_ask_sets_it_up(self):
        # While Ask starts this subject's engine and browser, another subject may not take them.
        from app import subjects
        studio = self.httpd.studio
        other = self.new_subject("Other", self.target)
        release, entered = threading.Event(), threading.Event()
        opened = studio.open_engine

        def slow_open(slug):
            engine = opened(slug)
            entered.set()
            release.wait(10)
            return engine

        studio.assistant.open_engine = slow_open
        box = {}
        asker = threading.Thread(target=lambda: box.update(reply=self.client.post(
            "/api/ask", {"slug": self.slug, "text": "go"})[1]))
        with support.env(FAKE_CLAUDE_TOOL=""):
            asker.start()
            self.assertTrue(entered.wait(15))
            try:
                self.assertTrue(studio.assistant.working_on(self.slug))
                _, refused = self.client.post("/api/subject/open", {"slug": other})
                self.assertIn("another subject", refused.get("error", ""))
                _, refused = self.client.post("/api/tool/run", {"slug": other, "name": "binary_overview"})
                self.assertIn("another subject", refused.get("error", ""))
                _, refused = self.client.post("/api/subject/close", {"slug": self.slug, "what": "engine"})
                self.assertIn("Claude", refused.get("error", ""))
            finally:
                release.set()
            asker.join(15)
            self.assertTrue(box["reply"].get("ok"), box)
            support.wait_for(lambda: (self.client.state()["job"] or {}).get("status") == "done", 30)
        self.assertIsNone(studio.assistant.pending)
        self.assertTrue(subjects.exists(other))

    def test_changing_the_rea_folder_closes_the_engine(self):
        self.client.post("/api/subject/open", {"slug": self.slug})
        self.assertEqual(self.client.brief(self.slug)["engine"]["state"], "running")
        second = support.fake_rea_checkout(self.root / "second")
        box = {}
        runner = threading.Thread(target=lambda: box.update(reply=self.run_tool(
            "decompile_all", {"output_directory": str(self.root / "out"), "seconds": 3})))
        runner.start()
        support.wait_for(lambda: any(r["status"] == "running" for r in self.client.state()["runs"].values()), 10)
        _, busy = self.client.post("/api/settings", {"values": {"rea_dir": str(second)}})
        self.assertIn("using REA", busy.get("error", ""))
        runner.join(15)
        _, reply = self.client.post("/api/settings", {"values": {"rea_dir": str(second)}})
        self.assertTrue(reply.get("ok"), reply)
        self.assertEqual(self.client.brief(self.slug)["engine"]["state"], "stopped",
                         "the engine from the old checkout kept running")


class EntryTest(unittest.TestCase):
    """app.assistant's result-to-finding helpers and app.server's session reading, on their own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        support.configure(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_next_offset_continues_the_same_call(self):
        from app.assistant import entry_for
        result = {"structuredContent": {"result": {"items": [1], "total": 3, "next_offset": 1}}}
        entry = entry_for("list_unknowns", {"limit": 1}, result)
        self.assertEqual(entry["continuation"], {"tool": "list_unknowns", "arguments": {"limit": 1, "offset": 1},
                                                 "label": "Load more"})
        view = {"source": {"kind": "retained-evidence", "evidence_id": "ev_1"}, "view": {"kind": "native-inventory",
                                                                                        "offset": 0, "limit": 32}}
        result = {"structuredContent": {"normalized_result": {"coverage": {"next_offset": 32}}}}
        entry = entry_for("inspect_analysis_view", view, result)
        self.assertEqual(entry["continuation"]["arguments"]["view"]["offset"], 32)
        self.assertEqual(view["view"]["offset"], 0, "the original arguments are left alone")
        done = {"structuredContent": {"result": {"items": [], "next_offset": None}}}
        self.assertIsNone(entry_for("list_unknowns", {}, done)["continuation"])

    def test_resource_constraint_names_the_view(self):
        from app.assistant import entry_for
        follow = {"name": "inspect_analysis_view", "arguments": {"source": {"kind": "retained-evidence"},
                                                                 "view": {"kind": "native-observation"}}}
        error = {"error": {"code": "response_too_large", "category": "resource_constraint", "message": "Too big.",
                           "retryable": False, "details": {"reported_limits": {"continuation": follow}}}}
        result = {"isError": True, "content": [{"type": "text", "text": json.dumps(error)}],
                  "_meta": {"provenance": {"untrusted_content": True, "source": "analysis_target"}}}
        entry = entry_for("analyze_function", {"address": "0x1"}, result)
        self.assertEqual(entry["continuation"]["tool"], "inspect_analysis_view")
        self.assertEqual(entry["continuation"]["arguments"], follow["arguments"])
        self.assertTrue(entry["untrusted"], "errors carry the marker too")
        self.assertEqual(entry["error"]["details"], error["error"]["details"])

    def test_plain_text_results_and_errors(self):
        from app.assistant import entry_for
        failed = entry_for("x", {}, {"isError": True, "content": [{"type": "text", "text": "boom"}]})
        self.assertEqual(failed["error"]["message"], "boom")
        self.assertEqual(failed["error"]["category"], "unknown")
        text = entry_for("x", {}, {"content": [{"type": "text", "text": "plain words"}]})
        self.assertEqual((text["text"], text["evidence"]), ("plain words", None))
        parsed = entry_for("x", {}, {"content": [{"type": "text", "text": "{\"a\": 1}"}]})
        self.assertEqual((parsed["text"], parsed["evidence"]), (None, {"a": 1}))
        marked = entry_for("x", {}, {"structuredContent": {"_meta": {"provenance": {"untrusted_content": True}}}})
        self.assertTrue(marked["untrusted"])

    def test_cutter_sessions_follow_the_latest_listing(self):
        from app.server import open_sessions
        items = [
            {"tool": "list_cutter_sessions", "session": "s", "error": None,
             "evidence": {"result": {"sessions": [{"session_id": "c1"}, {"session_id": "c2"}]}}},
            {"tool": "start_gdb_session", "session": "s", "error": None, "evidence": {"result": {"session_id": "g1"}}},
            {"tool": "list_cutter_sessions", "session": "s", "error": None,
             "evidence": {"result": {"sessions": [{"session_id": "c2"}]}}},
            {"tool": "start_rizin_debug_session", "session": "old", "error": None,
             "evidence": {"result": {"session_id": "r1"}}},
        ]
        found = sorted((s["kind"], s["session_id"]) for s in open_sessions(items, "s"))
        self.assertEqual(found, [("cutter", "c2"), ("gdb", "g1")])

    def test_report_leaves_out_base64(self):
        from app.server import _elide
        blob = "QUJD" * 1000
        self.assertIn("left out", _elide({"image": blob})["image"])
        self.assertEqual(_elide({"text": "short"}), {"text": "short"})


if __name__ == "__main__":
    unittest.main()
