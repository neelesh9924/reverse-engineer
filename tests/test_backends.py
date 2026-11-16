import tempfile
import unittest
from pathlib import Path

import support


class BackendsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = support.configure(Path(self.tmp.name))
        from app import backends
        self.backends = backends

    def tearDown(self):
        self.tmp.cleanup()

    def choose(self, mapping):
        self.config.apply({**self.config.current(), "backends": mapping})

    def test_default_all_active_configurable(self):
        inv = {b["id"]: b for b in self.backends.inventory()}
        self.assertTrue(inv["ghidra"]["active"])
        self.assertTrue(inv["ghidra"]["configurable"])
        self.assertFalse(inv["frida"]["configurable"])  # frida has no env hook

    def test_engine_env_injects_active_with_path(self):
        self.choose({"adb": {"active": True, "path": r"C:\tools\adb.exe"}})
        env = self.backends.engine_env()
        self.assertEqual(env["REA_ADB_PATH"], r"C:\tools\adb.exe")

    def test_inactive_is_not_offered(self):
        self.choose({"adb": {"active": False, "path": r"C:\tools\adb.exe"}})
        self.assertNotIn("REA_ADB_PATH", self.backends.engine_env())

    def test_no_path_no_injection(self):
        # active but no explicit path: REA finds it on PATH itself; we inject nothing.
        self.choose({"ghidra": {"active": True, "path": ""}})
        self.assertNotIn("GHIDRA_INSTALL_DIR", self.backends.engine_env())

    def test_single_native_pins_provider(self):
        self.choose({"ghidra": {"active": True, "path": r"C:\ghidra"}})
        self.assertEqual(self.backends.engine_env().get("REA_ANALYSIS_PROVIDER"), "ghidra")

    def test_two_natives_do_not_pin(self):
        self.choose({"ghidra": {"active": True, "path": r"C:\ghidra"},
                     "ida": {"active": True, "path": r"C:\ida\ida.exe"}})
        self.assertNotIn("REA_ANALYSIS_PROVIDER", self.backends.engine_env())


class BackendsThroughServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.httpd, self.client = support.start_server(Path(self.tmp.name))
        self.target = Path(self.tmp.name) / "thing.bin"
        self.target.write_bytes(b"MZ")
        # a real file to use as an "adb" path (must exist to pass settings validation)
        self.adb = Path(self.tmp.name) / "adb.exe"
        self.adb.write_text("", encoding="utf-8")

    def tearDown(self):
        self.httpd.studio.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def test_saved_backend_reaches_the_engine(self):
        _, reply = self.client.post("/api/settings", {"values": {"backends": {"adb": {"active": True, "path": str(self.adb)}}}})
        self.assertTrue(reply.get("ok"), reply)
        _, r = self.client.post("/api/subjects/new", {"name": "T", "path": str(self.target), "kind": "executable"})
        slug = r["slug"]
        _, run = self.client.post("/api/tool/run", {"slug": slug, "name": "binary_overview", "arguments": {}})
        self.assertTrue(run.get("ok"), run)
        env = run["result"]["structuredContent"]["raw_result"]["env"]
        self.assertEqual(env["REA_ADB_PATH"], str(self.adb))

    def test_bad_backend_path_refused(self):
        _, reply = self.client.post("/api/settings", {"values": {"backends": {"adb": {"active": True, "path": r"C:\nope\adb.exe"}}}})
        self.assertIn("problems", reply)


if __name__ == "__main__":
    unittest.main()
