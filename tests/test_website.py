"""Website subjects: a URL subject, its observed browser (a fake CDP server), and web tools
whose cdp_endpoint / target_id / allowed_origins the studio fills in and pins to that browser,
for the Tools page and for Claude alike."""
import json
import tempfile
import unittest
from pathlib import Path

import support


class WebsiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.httpd, self.client = support.start_server(self.root)
        self.dump = self.root / "claude-saw.json"

    def tearDown(self):
        support.stop_server(self.httpd)
        self.tmp.cleanup()

    def make(self, url="https://example.com"):
        _, reply = self.client.post("/api/subjects/new", {"name": "Example site", "url": url, "kind": "website"})
        self.assertTrue(reply.get("ok"), reply)
        return reply["slug"]

    def open(self, slug):
        _, reply = self.client.post("/api/subject/open", {"slug": slug})
        self.assertTrue(reply.get("ok"), reply)
        return support.wait_for(lambda: (self.client.brief(slug)["web"] or {}).get("state") == "running"
                                and self.client.brief(slug)["web"], 15, 0.2, "the observed browser did not come up")

    def ask(self, slug, tool, arguments):
        with support.env(FAKE_CLAUDE_DUMP=self.dump, FAKE_CLAUDE_TOOL=tool, FAKE_CLAUDE_ARGS=json.dumps(arguments)):
            _, reply = self.client.post("/api/ask", {"slug": slug, "text": "look at the page"})
            self.assertTrue(reply.get("ok"), reply)
            support.wait_for(lambda: (self.client.state()["job"] or {}).get("status") not in (None, "running"),
                             30, 0.2, "the run did not finish")
        return json.loads(self.dump.read_text(encoding="utf-8"))

    def test_website_subject_created(self):
        slug = self.make()
        brief = self.client.brief(slug)
        self.assertEqual(brief["kind"], "website")
        self.assertEqual(brief["url"], "https://example.com")
        self.assertEqual(brief["web"]["state"], "stopped")

    def test_kind_is_guessed_from_an_address(self):
        _, reply = self.client.post("/api/subjects/new", {"name": "Guessed", "path": "https://example.org"})
        self.assertTrue(reply.get("ok"), reply)
        self.assertEqual(self.client.brief(reply["slug"])["kind"], "website")

    def test_bad_url_refused(self):
        _, reply = self.client.post("/api/subjects/new", {"name": "x", "url": "not a url", "kind": "website"})
        self.assertIn("error", reply)

    def test_open_starts_observed_browser(self):
        slug = self.make()
        web = self.open(slug)
        self.assertTrue(web["endpoint"].startswith("http://127.0.0.1:"))
        self.assertEqual(web["target_id"], "PAGE-TARGET-1")
        self.assertEqual(web["allowed_origins"], ["https://example.com"])

    def test_web_tool_is_filled_and_pinned_on_the_tools_page(self):
        slug = self.make()
        web = self.open(slug)
        _, run = self.client.post("/api/tool/run", {"slug": slug, "name": "inspect_web_page", "arguments": {
            "cdp_endpoint": "http://127.0.0.1:1", "target_id": "SOMETHING-ELSE",
            "allowed_origins": ["https://example.com", "https://other.example"]}})
        self.assertTrue(run.get("ok"), run)
        params = run["result"]["structuredContent"]["parameters"]
        self.assertEqual(params["cdp_endpoint"], web["endpoint"])
        self.assertEqual(params["target_id"], "PAGE-TARGET-1")
        self.assertEqual(params["allowed_origins"], ["https://example.com"])

    def test_tool_defaults_name_the_browser(self):
        slug = self.make()
        _, before = self.client.get(f"/api/tool/defaults?slug={slug}&name=inspect_web_page")
        self.assertEqual(before["arguments"], {})
        self.assertIn("Open the subject", before["notes"][0])
        web = self.open(slug)
        _, after = self.client.get(f"/api/tool/defaults?slug={slug}&name=inspect_web_page")
        self.assertEqual(after["arguments"], {"cdp_endpoint": web["endpoint"], "target_id": "PAGE-TARGET-1",
                                              "allowed_origins": ["https://example.com"]})

    def test_claude_web_call_is_pinned_to_the_subjects_browser(self):
        slug = self.make()
        web = self.open(slug)
        saw = self.ask(slug, "inspect_web_page", {"cdp_endpoint": "http://127.0.0.1:9222", "target_id": "OTHER",
                                                  "allowed_origins": ["https://bank.example"]})
        params = saw["replies"][0]["result"]["structuredContent"]["parameters"]
        self.assertEqual(params["cdp_endpoint"], web["endpoint"])
        self.assertEqual(params["target_id"], "PAGE-TARGET-1")
        self.assertEqual(params["allowed_origins"], ["https://example.com"])
        self.assertIn("live website", saw["argv"][saw["argv"].index("--append-system-prompt") + 1])
        self.assertNotIn("--add-dir", saw["argv"], "a website subject hands Claude no target folder")

    def test_claude_cannot_use_web_tools_on_a_file_subject(self):
        target = self.root / "app.bin"
        target.write_bytes(b"MZ")
        _, made = self.client.post("/api/subjects/new", {"name": "File", "path": str(target), "kind": "executable"})
        saw = self.ask(made["slug"], "inspect_web_page", {"cdp_endpoint": "http://127.0.0.1:9222", "target_id": "X"})
        result = saw["replies"][0]["result"]
        self.assertTrue(result["isError"])
        self.assertIn("website subject", result["content"][0]["text"])

    def test_close_stops_browser(self):
        slug = self.make()
        self.open(slug)
        _, reply = self.client.post("/api/subject/close", {"slug": slug, "what": "browser"})
        self.assertTrue(reply.get("ok"), reply)
        brief = self.client.brief(slug)
        self.assertEqual(brief["web"]["state"], "stopped")
        self.assertEqual(brief["engine"]["state"], "running", "closing the browser leaves the engine")
        self.client.post("/api/subject/close", {"slug": slug, "what": "all"})
        self.assertEqual(self.client.brief(slug)["engine"]["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
