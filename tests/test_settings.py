import json
import pathlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {"changes": {"type": "array", "items": {"anyOf": [
        {"type": "object", "properties": {
            "setting": {"type": "string", "const": "ghidra.decompile_timeout_seconds"},
            "action": {"type": "string", "const": "set"},
            "value": {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 2147483}, {"type": "null"}]}}},
        {"type": "object", "properties": {
            "setting": {"type": "string", "const": "javascript.max_heap_mb"},
            "action": {"type": "string", "const": "set"},
            "value": {"type": "integer", "minimum": 64, "maximum": 65536}}},
    ]}}},
}


def no_filesystem(*_args, **_kwargs):
    raise AssertionError("the filesystem was asked about a path that should have been refused as text")


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = support.configure(self.root)
        from app import catalog, checks, settings
        self.settings = settings
        self.checks = checks
        self.catalog = catalog
        self.rea = self.root / "rea"

    def tearDown(self):
        self.tmp.cleanup()

    def write_settings(self, values):
        (self.config.DATA_DIR / "settings.json").write_text(json.dumps(values), encoding="utf-8")
        return self.config.load()

    def write_catalog(self, **changes):
        raw = {**support.CATALOG, **changes}
        (self.rea / "dist" / "mcp-catalog.json").write_text(json.dumps(raw), encoding="utf-8")

    # ---- what the page sees ----

    def test_public_has_every_editable_key_and_where_things_resolved(self):
        (self.rea / "package.json").write_text(json.dumps({"name": "rea-agents", "version": "6.8.0"}), encoding="utf-8")
        with mock.patch.object(self.config, "node_command", return_value=(["node"], "node on PATH")):
            out = self.settings.public()
        for key in self.settings.EDITABLE:
            self.assertIn(key, out)
        self.assertIs(out["claude_destructive"], False)
        self.assertIs(out["notice_seen"], False)
        self.assertEqual(out["analysis_settings"], {})
        self.assertEqual(out["rea_version"], "6.8.0")
        self.assertEqual(out["node_requirement"], self.config.NODE_REQUIREMENT)
        self.assertEqual(out["rea_dir_used"], str(self.rea))
        for key in ("node_used", "node_how", "claude_used"):
            self.assertIn(key, out)

    # ---- switches ----

    def test_switches_save_as_booleans(self):
        changed, problems = self.settings.save({"claude_destructive": True, "notice_seen": True})
        self.assertEqual(problems, [])
        self.assertEqual(sorted(changed), ["claude_destructive", "notice_seen"])
        self.assertIs(self.config.CLAUDE_DESTRUCTIVE, True)
        self.assertIs(self.config.NOTICE_SEEN, True)
        saved = json.loads((self.config.DATA_DIR / "settings.json").read_text(encoding="utf-8"))
        self.assertIs(saved["claude_destructive"], True)
        changed, _ = self.settings.save({"claude_destructive": False})
        self.assertEqual(changed, ["claude_destructive"])
        saved = json.loads((self.config.DATA_DIR / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("claude_destructive", saved)   # back to the default: not written

    def test_switch_must_be_a_boolean(self):
        for value in ("yes", 1, None):
            _, problems = self.settings.save({"claude_destructive": value})
            self.assertTrue(problems, value)
        self.assertIs(self.config.CLAUDE_DESTRUCTIVE, False)

    def test_an_unreadable_file_is_never_overwritten(self):
        path = self.config.DATA_DIR / "settings.json"
        broken = '{"rea_dir": "%s", "engine_timeout": 3600, "port": 8765,}' % str(self.rea).replace("\\", "\\\\")
        path.write_text(broken, encoding="utf-8")
        changed, problems = self.settings.save({"theme": "dark"})
        self.assertEqual(changed, [])
        self.assertIn("nothing was saved", problems[0])
        self.assertEqual(path.read_text(encoding="utf-8"), broken, "the hand-set settings were lost")

    def test_uneditable_keys_ignored(self):
        changed, problems = self.settings.save({"port": 9999, "engine_timeout": 1})
        self.assertEqual((changed, problems), ([], []))
        self.assertEqual(self.config.PORT, 0)

    # ---- analysis settings ----

    def test_analysis_settings_checked_against_reas_schema(self):
        tools = [t for t in support.CATALOG["tools"] if t["name"] != "update_analysis_settings"]
        self.write_catalog(tools=tools + [{"name": "update_analysis_settings", "title": "Update Analysis Settings",
                                           "inputSchema": ANALYSIS_SCHEMA, "annotations": {"readOnlyHint": False}}])
        self.assertIn("javascript.max_heap_mb", self.catalog.analysis_setting_specs())
        good = {"ghidra.decompile_timeout_seconds": None, "javascript.max_heap_mb": 4096}
        changed, problems = self.settings.save({"analysis_settings": good})
        self.assertEqual(problems, [])
        self.assertEqual(changed, ["analysis_settings"])
        self.assertEqual(self.config.ANALYSIS_SETTINGS, good)
        for bad in ({"javascript.max_heap_mb": 1}, {"javascript.max_heap_mb": "big"}, {"javascript.max_heap_mb": True},
                    {"no.such_setting": 1}, {"Not A Name": 1}, ["javascript.max_heap_mb"]):
            _, problems = self.settings.save({"analysis_settings": bad})
            self.assertTrue(problems, bad)
        self.assertEqual(self.config.ANALYSIS_SETTINGS, good)
        _, problems = self.settings.save({"analysis_settings": {"javascript.max_heap_mb": 1}})
        self.assertIn("64 to 65536", problems[0])

    def test_analysis_settings_without_a_schema(self):
        # no update_analysis_settings schema to check against: only the shape is checked
        self.write_catalog(tools=[t for t in support.CATALOG["tools"] if t["name"] != "update_analysis_settings"])
        _, problems = self.settings.save({"analysis_settings": {"ghidra.decompile_timeout_seconds": 120}})
        self.assertEqual(problems, [])
        _, problems = self.settings.save({"analysis_settings": {"ghidra.decompile_timeout_seconds": {"deep": 1}}})
        self.assertTrue(problems)

    # ---- paths ----

    def test_network_paths_refused_before_any_stat(self):
        for key in ("rea_dir", "node_bin", "claude_bin"):
            with mock.patch("os.stat", no_filesystem), mock.patch.object(pathlib.Path, "stat", no_filesystem):
                _, problems = self.settings.check({key: r"\\host\share\thing"})
            self.assertTrue(problems, key)
            self.assertIn("network share", problems[0])
        with mock.patch("os.stat", no_filesystem), mock.patch.object(pathlib.Path, "stat", no_filesystem):
            _, problems = self.settings.check({"backends": {"adb": {"active": True, "path": "//host/share/adb.exe"}}})
        self.assertIn("network share", problems[0])

    def test_quoted_paths_accepted(self):
        node = self.root / "node.exe"
        node.write_text("", encoding="utf-8")
        keep, problems = self.settings.check({"node_bin": f'"{node}"', "rea_dir": f'"{self.rea}"'})
        self.assertEqual(problems, [])
        self.assertEqual(keep["node_bin"], str(node))
        self.assertEqual(keep["rea_dir"], str(self.rea))

    def test_rea_dir_must_be_a_checkout(self):
        _, problems = self.settings.check({"rea_dir": str(self.root)})
        self.assertIn("not a rea-agents checkout", problems[0])

    # ---- data/settings.json by hand (COR-16) ----

    def test_numbers_out_of_range_keep_the_default(self):
        problems = self.write_settings({"rea_dir": str(self.rea), "port": 70000, "max_turns": 0,
                                        "engine_timeout": 5, "run_timeout": 10 ** 9})
        self.assertEqual(len(problems), 4)
        self.assertTrue(any("0 or 1024–65535" in p for p in problems))
        self.assertEqual(self.config.PORT, 0)
        self.assertEqual(self.config.MAX_TURNS, self.config.DEFAULTS["max_turns"])
        self.assertEqual(self.config.ENGINE_TIMEOUT, self.config.DEFAULTS["engine_timeout"])
        self.assertEqual(self.config.RUN_TIMEOUT, self.config.DEFAULTS["run_timeout"])

    def test_numbers_in_range_are_used(self):
        problems = self.write_settings({"port": 8765, "max_turns": 1000, "engine_timeout": 30, "run_timeout": 86400})
        self.assertEqual(problems, [])
        self.assertEqual((self.config.PORT, self.config.MAX_TURNS, self.config.ENGINE_TIMEOUT, self.config.RUN_TIMEOUT),
                         (8765, 1000, 30, 86400))
        self.assertEqual(self.write_settings({"port": 0}), [])
        self.assertTrue(self.write_settings({"port": 80}))
        self.assertTrue(self.write_settings({"max_turns": True}))

    def test_booleans_by_hand(self):
        self.assertEqual(self.write_settings({"claude_destructive": True}), [])
        self.assertIs(self.config.CLAUDE_DESTRUCTIVE, True)
        self.assertTrue(self.write_settings({"claude_destructive": 1}))
        self.assertIs(self.config.CLAUDE_DESTRUCTIVE, False)

    def test_network_paths_by_hand_are_refused(self):
        problems = self.write_settings({"browser": r"\\host\share\chrome.exe",
                                        "backends": {"adb": {"active": True, "path": r"\\host\share\adb.exe"}}})
        self.assertEqual(len(problems), 2)
        self.assertEqual(self.config.BROWSER, "")
        self.assertEqual(self.config.BACKENDS["adb"]["path"], "")


class PathTextTest(unittest.TestCase):
    def setUp(self):
        from app import config
        self.problem = config.local_path_problem

    def test_local_paths_pass(self):
        for text in ("C:\\Tools\\app.exe", "D:/data/x.apk", "c:\\a b\\c.d.e", "C:\\console\\connect.exe"):
            self.assertEqual(self.problem(text), "", text)

    def test_others_fail(self):
        for text in ("", None, "app.exe", "\\Windows\\x", r"\\host\share", "//host/share", r"\\?\C:\x",
                     r"\\.\pipe\x", "C:\\x\\CON", "C:\\x\\aux.txt", "C:\\x\\com1", "C:\\x\\LPT9.log", "C:\\a\0b",
                     "C:relative"):
            self.assertTrue(self.problem(text), text)


class NodeTest(unittest.TestCase):
    def setUp(self):
        from app import config
        self.config = config

    def test_requirement(self):
        ok = self.config.node_ok
        for version in ("v22.23.4", "22.30.0", "v24.22.1", "24.99.0", "26.12.1", "v27.0.0", "30.1.2"):
            self.assertTrue(ok(version), version)
        for version in ("v22.23.3", "v23.9.0", "24.22.0", "v25.5.0", "26.12.0", "v20.0.0", "", "garbage"):
            self.assertFalse(ok(version), version)

    def test_one_requirement_text(self):
        for minimum in self.config.NODE_MINIMUMS:
            self.assertIn(".".join(map(str, minimum)), self.config.NODE_REQUIREMENT)

    def test_doctor_hint_names_the_checkout_command(self):
        with mock.patch.object(self.config, "node_command",
                               return_value=([r"C:\Program Files\fnm\fnm.exe", "exec", "--using=24", "node"], "fnm")):
            hint = self.config.doctor_hint()
        self.assertIn('"C:\\Program Files\\fnm\\fnm.exe" exec --using=24 node scripts/rea.mjs doctor', hint)
        self.assertIn(str(self.config.rea_dir()), hint)

    def test_fnm_without_node_24_falls_back_to_path(self):
        with mock.patch.object(self.config, "NODE_BIN", "", create=True), \
                mock.patch.object(self.config, "fnm_exe", return_value=Path(r"C:\fnm\fnm.exe")), \
                mock.patch.object(self.config, "_fnm_has_node", return_value=False), \
                mock.patch.object(self.config.shutil, "which", return_value=r"C:\node\node.exe"):
            command, how = self.config.node_command()
        self.assertEqual(command, [r"C:\node\node.exe"])
        self.assertIn("fnm has no Node 24", how)


class FakeRunner:
    def base_command(self):
        return None


class ChecksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = support.configure(self.root)
        from app import checks
        self.checks = checks

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_failing_check_never_hides_the_others(self):
        seen = []
        with mock.patch.object(self.checks, "node", side_effect=RuntimeError("boom")):
            results = self.checks.run_all(FakeRunner(), put=lambda name, result: seen.append(name))
        self.assertEqual(seen, list(self.checks.NAMES))
        self.assertEqual(results["node"], {"ok": False, "error": "The check failed: boom"})
        self.assertFalse(results["claude"]["ok"])
        self.assertTrue(results["rea"]["ok"])
        self.assertIsInstance(results["backends"], list)
        with mock.patch.object(self.checks, "backends", side_effect=OSError("nope")):
            results = self.checks.run_all(FakeRunner())
        self.assertFalse(results["backends"]["ok"])

    def test_rea_version_comes_from_package_json(self):
        rea = self.root / "rea"
        catalog = {**support.CATALOG, "version": 1, "info": {"name": "rea", "version": "6.8.0"}}
        (rea / "dist" / "mcp-catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
        (rea / "package.json").write_text(json.dumps({"version": "6.8.0"}), encoding="utf-8")
        result = self.checks.rea()
        self.assertEqual(result["version"], "6.8.0")
        self.assertNotIn("warning", result)
        (rea / "package.json").write_text(json.dumps({"version": "6.9.0"}), encoding="utf-8")
        result = self.checks.rea()
        self.assertEqual(result["version"], "6.9.0")
        self.assertIn("Rebuild", result["warning"])

    def test_missing_node_names_the_requirement(self):
        with mock.patch.object(self.config, "node_command", return_value=(None, "")), \
                mock.patch.object(self.config, "fnm_lacks_node", return_value=False):
            result = self.checks.node()
        self.assertIn(self.config.NODE_REQUIREMENT, result["error"])
        with mock.patch.object(self.config, "node_command", return_value=(None, "")), \
                mock.patch.object(self.config, "fnm_lacks_node", return_value=True):
            result = self.checks.node()
        self.assertIn("fnm install 24", result["error"])


if __name__ == "__main__":
    unittest.main()
