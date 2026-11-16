import json
import tempfile
import unittest
from pathlib import Path

import support

# Tools with known hints, written by the tests that check hints (support's catalogue may change).
HINTED = [
    {"name": "open_binary", "title": "Open Binary",
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "binary_overview", "title": "Binary Overview",
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "start_frida_session", "title": "Start Frida Session",
     "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}},
]


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        support.configure(Path(self.tmp.name))
        from app import catalog
        self.catalog = catalog
        self.file = Path(self.tmp.name) / "rea" / "dist" / "mcp-catalog.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, raw):
        self.file.write_text(json.dumps(raw), encoding="utf-8")

    def test_loads_and_counts(self):
        parsed = self.catalog.load()
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["count"], len(support.CATALOG["tools"]))
        self.assertEqual(parsed["instructions"], support.CATALOG["instructions"])

    def test_version_is_reas_not_the_manifest_format(self):
        # REA's manifest: top-level "version" is the format (1); REA's own version is info.version.
        self.write({**support.CATALOG, "version": 1, "info": {"name": "rea", "version": "6.8.0-test"}})
        self.assertEqual(self.catalog.load()["version"], "6.8.0-test")
        self.assertEqual(self.catalog.catalogue()["version"], "6.8.0-test")

    def test_categories_are_derived(self):
        by_name = {t["name"]: t["category"] for t in self.catalog.load()["tools"]}
        self.assertEqual(by_name["open_binary"], "Session & targets")
        self.assertEqual(by_name["inspect_adb_device"], "Android & ADB")
        self.assertEqual(by_name["analyze_javascript_application"], "Web, JS & Electron")
        self.assertEqual(by_name["start_frida_session"], "Runtime & debug")
        self.assertEqual(by_name["inspect_sqlite_database"], "Files & formats")
        self.assertEqual(by_name["verify_reconstruction"], "Reconstruction & evidence")
        self.assertEqual(by_name["procedure_pseudo_code"], "Functions & disassembly")
        self.assertEqual(by_name["update_analysis_settings"], "Settings")

    def test_catalogue_groups_and_prompts(self):
        cat = self.catalog.catalogue()
        self.assertTrue(cat["ready"])
        names = [c["name"] for c in cat["categories"]]
        self.assertEqual(names, list(dict.fromkeys(names)))  # no duplicate groups
        self.assertTrue(all(c["tools"] for c in cat["categories"]))
        self.assertEqual(cat["prompts"][0]["name"], "investigate_feature")
        tool = cat["categories"][0]["tools"][0]
        for key in ("name", "title", "description", "category", "inputSchema", "annotations",
                    "read_only", "destructive", "open_world"):
            self.assertIn(key, tool)

    def test_tool_and_hints(self):
        self.write({**support.CATALOG, "tools": HINTED})
        self.assertEqual(self.catalog.tool("binary_overview")["title"], "Binary Overview")
        self.assertIsNone(self.catalog.tool("rm_minus_rf"))
        self.assertIsNone(self.catalog.tool(None))
        overview = self.catalog.tool("binary_overview")
        self.assertEqual((overview["read_only"], overview["destructive"], overview["open_world"]), (True, False, False))
        self.assertTrue(self.catalog.is_destructive("start_frida_session"))
        self.assertTrue(self.catalog.tool("start_frida_session")["open_world"])
        self.assertFalse(self.catalog.is_destructive("open_binary"))
        self.assertFalse(self.catalog.is_destructive("binary_overview"))
        self.assertFalse(self.catalog.is_destructive("no_such_tool"))

    def test_missing_hints_follow_the_mcp_defaults(self):
        self.write({**support.CATALOG, "tools": [
            {"name": "mystery_tool", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "reader", "annotations": {"readOnlyHint": True}},
        ]})
        mystery = self.catalog.tool("mystery_tool")
        self.assertEqual((mystery["read_only"], mystery["destructive"], mystery["open_world"]), (False, True, True))
        self.assertFalse(self.catalog.is_destructive("reader"))
        self.assertEqual(self.catalog.tool("reader")["inputSchema"], {"type": "object", "properties": {}})

    def test_prompt(self):
        self.write({**support.CATALOG, "prompts": [
            {"name": "investigate_feature", "title": "Investigate a feature", "description": "Trace it.",
             "arguments": [{"name": "feature", "description": "What to trace", "required": True},
                           {"name": "procedure"}, {"bad": "argument"}]}]})
        prompt = self.catalog.prompt("investigate_feature")
        self.assertEqual(prompt["arguments"], [{"name": "feature", "description": "What to trace", "required": True},
                                               {"name": "procedure", "description": "", "required": False}])
        self.assertIsNone(self.catalog.prompt("nope"))

    def test_analysis_setting_specs(self):
        self.write({**support.CATALOG, "tools": HINTED})
        self.assertEqual(self.catalog.analysis_setting_specs(), {})   # no update_analysis_settings
        schema = {"type": "object", "properties": {"changes": {"type": "array", "items": {"anyOf": [
            {"type": "object", "properties": {"setting": {"const": "jadx.operation_timeout_ms"},
                                              "value": {"type": "integer", "minimum": 1}}}]}}}}
        self.write({**support.CATALOG, "tools": [{"name": "update_analysis_settings", "inputSchema": schema}]})
        self.assertEqual(self.catalog.analysis_setting_specs(),
                         {"jadx.operation_timeout_ms": {"type": "integer", "minimum": 1}})

    def test_has_tool(self):
        self.assertTrue(self.catalog.has_tool("binary_overview"))
        self.assertFalse(self.catalog.has_tool("rm_minus_rf"))

    def test_unreadable_catalogue(self):
        self.file.write_text("[1, 2", encoding="utf-8")
        self.assertIsNone(self.catalog.load())
        self.write(["not", "an", "object"])
        self.assertIsNone(self.catalog.load())

    def test_absent_when_not_built(self):
        from app import config
        # point at a dir with no dist
        empty = Path(self.tmp.name) / "empty"
        (empty / "scripts").mkdir(parents=True)
        (empty / "scripts" / "rea.mjs").write_text("", encoding="utf-8")
        config.apply({**config.current(), "rea_dir": str(empty)})
        self.assertIsNone(self.catalog.load())
        self.assertFalse(self.catalog.catalogue()["ready"])
        self.assertEqual(self.catalog.catalogue()["version"], "")


if __name__ == "__main__":
    unittest.main()
