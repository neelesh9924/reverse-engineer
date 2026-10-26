"""The server's guards and its basic API: host, cookie, origin, the page's security headers,
/mcp's token, subjects, settings and the catalogue."""
import tempfile
import unittest
from pathlib import Path

import support


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.httpd, self.client = support.start_server(Path(self.tmp.name))
        self.target = Path(self.tmp.name) / "thing.bin"
        self.target.write_bytes(b"MZ binary")

    def tearDown(self):
        support.stop_server(self.httpd)
        self.tmp.cleanup()

    def new_subject(self, name="Thing"):
        status, reply = self.client.post("/api/subjects/new", {"name": name, "path": str(self.target), "kind": "executable"})
        self.assertEqual(status, 200, reply)
        self.assertNotIn("error", reply)
        return reply["slug"]

    def test_health_and_state(self):
        _, health = self.client.get("/health")
        self.assertEqual(health["app"], "reverse-engineer")
        state = self.client.state()
        self.assertEqual(state["app"], "Reverse Engineer")
        self.assertTrue(state["catalog"]["ready"])
        self.assertEqual(state["catalog"]["count"], len(support.CATALOG["tools"]))
        for key in ("rev", "subjects", "job", "runs", "checks", "setup", "permissions"):
            self.assertIn(key, state)
        self.assertEqual(set(state["setup"]), {"rea_ready", "claude_ready", "node_ready", "notice_seen"})
        self.assertFalse(state["permissions"]["claude_destructive"])

    def test_state_is_the_heartbeat(self):
        before = self.httpd.studio.last_seen
        self.client.state()
        self.assertGreater(self.httpd.studio.last_seen, before)

    def test_subject_brief(self):
        slug = self.new_subject("My App")
        brief = self.client.brief(slug)
        self.assertEqual(brief["name"], "My App")
        self.assertEqual(brief["kind"], "executable")
        self.assertEqual(brief["path"], str(self.target))
        self.assertTrue(brief["exists"])
        self.assertIsNone(brief["web"])
        self.assertEqual(brief["engine"]["state"], "stopped")
        for key in ("hashing", "thread_rev", "evidence_rev", "evidence_count", "url"):
            self.assertIn(key, brief)
        self.target.unlink()
        self.assertFalse(self.client.brief(slug)["exists"], "exists is checked again on every poll")

    def test_run_tool_records_a_finding(self):
        slug = self.new_subject()
        status, reply = self.client.post("/api/tool/run", {"slug": slug, "name": "binary_overview", "arguments": {}})
        self.assertEqual(status, 200, reply)
        self.assertTrue(reply.get("ok"), reply)
        self.assertEqual(reply["result"]["structuredContent"]["operation"], "binary_overview")
        self.assertEqual(reply["entry"]["tool"], "binary_overview")
        self.assertEqual(reply["entry"]["source"], "tools")
        _, found = self.client.get(f"/api/evidence?slug={slug}")
        self.assertEqual(found["total"], 1)
        self.assertEqual(found["items"][0]["id"], reply["entry"]["id"])

    def test_unknown_tool_refused(self):
        slug = self.new_subject()
        _, reply = self.client.post("/api/tool/run", {"slug": slug, "name": "rm_rf", "arguments": {}})
        self.assertIn("error", reply)

    def test_arguments_must_be_an_object(self):
        slug = self.new_subject()
        _, reply = self.client.post("/api/tool/run", {"slug": slug, "name": "binary_overview", "arguments": [1]})
        self.assertIn("error", reply)

    def test_catalog_endpoint(self):
        _, cat = self.client.get("/api/catalog")
        self.assertTrue(cat["ready"])
        self.assertEqual(cat["version"], support.REA_VERSION)
        tools = {t["name"]: t for c in cat["categories"] for t in c["tools"]}
        self.assertTrue(tools["install_adb_package"]["destructive"])
        self.assertFalse(tools["binary_overview"]["destructive"])
        self.assertTrue(tools["list_unknowns"]["read_only"])
        self.assertTrue(tools["inspect_web_page"]["open_world"])
        prompt = next(p for p in cat["prompts"] if p["name"] == "investigate_feature")
        self.assertTrue(next(a for a in prompt["arguments"] if a["name"] == "feature")["required"])

    def test_security_headers(self):
        for path in ("/", "/api/state", "/static/index.html", "/nope"):
            _, headers, _ = self.client.fetch(path)
            csp = headers.get("Content-Security-Policy", "")
            self.assertIn("frame-ancestors 'none'", csp, path)
            self.assertIn("script-src 'self'", csp, path)
            self.assertEqual(headers.get("X-Frame-Options"), "DENY", path)
            self.assertEqual(headers.get("Referrer-Policy"), "no-referrer", path)
            self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff", path)

    def test_host_guard(self):
        code, _ = self.client.raw("/health", headers={"Host": "evil.example"})
        self.assertEqual(code, 421)

    def test_cookie_required(self):
        code, _ = self.client.raw("/api/state")  # no cookie
        self.assertEqual(code, 401)

    def test_origin_required_on_post(self):
        code, _ = self.client.raw("/api/subjects/new", method="POST",
                                  headers={"Cookie": self.client.cookie, "Content-Type": "application/json"},
                                  body={"name": "x", "path": str(self.target)})
        self.assertEqual(code, 403)
        code, _ = self.client.raw("/api/subjects/new", method="POST",
                                  headers={"Cookie": self.client.cookie, "Content-Type": "application/json",
                                           "Origin": "http://127.0.0.1:1"},
                                  body={"name": "x", "path": str(self.target)})
        self.assertEqual(code, 403)

    def test_mcp_requires_token(self):
        code, _ = self.client.raw("/mcp", method="POST",
                                  headers={"Content-Type": "application/json", "Authorization": "Bearer nope"},
                                  body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(code, 401)

    def test_mcp_get_and_delete_are_405(self):
        self.assertEqual(self.client.raw("/mcp")[0], 405)
        self.assertEqual(self.client.raw("/mcp", method="DELETE")[0], 405)

    def test_settings_roundtrip(self):
        _, reply = self.client.post("/api/settings", {"values": {"model": "sonnet"}})
        self.assertTrue(reply.get("ok"), reply)
        self.assertEqual(reply["settings"]["model"], "sonnet")
        _, bad = self.client.post("/api/settings", {"values": {"model": "has spaces!"}})
        self.assertIn("problems", bad)

    def test_claude_permission_setting_reaches_state(self):
        _, reply = self.client.post("/api/settings", {"values": {"claude_destructive": True}})
        self.assertTrue(reply.get("ok"), reply)
        self.assertTrue(self.client.state()["permissions"]["claude_destructive"])

    def test_rename_and_remove_subject(self):
        slug = self.new_subject()
        self.client.post("/api/tool/run", {"slug": slug, "name": "binary_overview", "arguments": {}})
        _, reply = self.client.post("/api/rename", {"slug": slug, "name": "Renamed"})
        self.assertTrue(reply.get("ok"), reply)
        self.assertEqual(self.client.brief(slug)["name"], "Renamed")
        _, reply = self.client.post("/api/remove", {"slug": slug})
        self.assertTrue(reply.get("ok"), reply)
        self.assertNotIn(slug, [s["slug"] for s in self.client.state()["subjects"]])
        _, found = self.client.get(f"/api/evidence?slug={slug}")
        self.assertEqual(found["items"], [])


if __name__ == "__main__":
    unittest.main()
