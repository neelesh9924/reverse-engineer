"""The /mcp passthrough: ReaEngine.forward (ids rewritten, cancels translated, progress tokens)
and mcp.handle (message checks, policy, recording), against tests/fake_rea.py. Also the parts of
the fake's contract other tests lean on."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import support
from test_engine import error_code, wait_for


def rpc(message_id, method, params=None):
    message = {"jsonrpc": "2.0", "id": message_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def tool_call(message_id, name, arguments=None, meta=None):
    params = {"name": name, "arguments": arguments or {}}
    if meta is not None:
        params["_meta"] = meta
    return rpc(message_id, "tools/call", params)


class ProxyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        support.configure(Path(self.tmp.name))
        from app.rea import ReaEngine
        self.events = []
        self.engine = ReaEngine(command=support.fake_rea_cmd(), on_event=lambda k, p: self.events.append((k, p)))
        self.engine.start()

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def in_thread(self, fn, *args, **kwargs):
        box = {}

        def run():
            try:
                box["value"] = fn(*args, **kwargs)
            except Exception as exc:
                box["error"] = exc

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, box

    def reached_rea(self, name):
        return wait_for(lambda: [c for c in list(self.engine._inflight.values()) if c.written and c.name == name])

    # ---- ids ----

    def test_forwarded_and_direct_calls_with_the_same_id_get_their_own_replies(self):
        direct, box = self.in_thread(self.engine.call, "decompile_all", {"output_directory": "x", "seconds": 1.2})
        calls = self.reached_rea("decompile_all")
        self.assertTrue(calls)
        studio_id = calls[0].id
        # Claude happens to use the very id the studio's own call has on the wire.
        reply = self.engine.forward(rpc(studio_id, "prompts/list"))
        self.assertEqual(reply["id"], studio_id)
        self.assertEqual([p["name"] for p in reply["result"]["prompts"]], ["investigate_feature", "trace_crash"])
        also = self.engine.forward(rpc(studio_id + 1, "tools/list"))   # and the next one it will take
        self.assertEqual(also["id"], studio_id + 1)
        self.assertIn("tools", also["result"])
        direct.join(timeout=10)
        self.assertNotIn("error", box)
        self.assertEqual(box["value"]["structuredContent"]["operation"], "decompile_all")

    def test_forwarded_tool_call_comes_back_under_the_client_id(self):
        reply = self.engine.forward(tool_call("claude-7", "binary_overview"))
        self.assertEqual(reply["id"], "claude-7")
        self.assertEqual(reply["result"]["structuredContent"]["operation"], "binary_overview")

    def test_studio_request_keeps_the_callers_id(self):
        reply = self.engine.request(rpc(0, "prompts/get", {"name": "investigate_feature",
                                                           "arguments": {"feature": "login"}}))
        self.assertEqual(reply["id"], 0)
        self.assertEqual(reply["result"]["messages"][0]["content"]["text"], "Investigate the feature: login")

    # ---- cancelling ----

    def test_claudes_cancel_reaches_rea_under_the_studio_id(self):
        thread, box = self.in_thread(self.engine.forward, tool_call(5, "decompile_all", {"output_directory": "x"}))
        self.assertTrue(self.reached_rea("decompile_all"))
        started = time.monotonic()
        self.assertIsNone(self.engine.forward({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                               "params": {"requestId": 5, "reason": "timeout"}}))
        thread.join(timeout=5)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(box["value"]["id"], 5)
        self.assertEqual(error_code(box["value"]["result"]), "cancelled")
        self.assertFalse(self.engine._call_lock.locked())

    def test_cancel_for_another_id_changes_nothing(self):
        thread, box = self.in_thread(self.engine.forward,
                                     tool_call(5, "decompile_all", {"output_directory": "x", "seconds": 0.6}))
        self.assertTrue(self.reached_rea("decompile_all"))
        self.engine.forward({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "5"}})
        self.engine.forward({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 6}})
        thread.join(timeout=5)
        self.assertIsNone(error_code(box["value"]["result"]))

    def test_studio_cancel_reaches_a_forwarded_call(self):
        thread, box = self.in_thread(self.engine.forward, tool_call(3, "decompile_all", {"output_directory": "x"}))
        self.assertTrue(self.reached_rea("decompile_all"))
        self.engine.cancel()
        thread.join(timeout=5)
        self.assertEqual(error_code(box["value"]["result"]), "cancelled")

    # ---- progress ----

    def test_claudes_own_progress_token_is_left_alone(self):
        reply = self.engine.forward(tool_call(4, "decompile_all", {"output_directory": "x", "seconds": 0.6},
                                              meta={"progressToken": "claude-token-1"}))
        meta = reply["result"]["structuredContent"]["raw_result"]["meta"]
        self.assertEqual(meta["progressToken"], "claude-token-1")
        progress = [p for k, p in self.events if k == "progress"]
        self.assertTrue(progress)
        self.assertEqual((progress[0]["tool"], progress[0]["source"]), ("decompile_all", "claude"))
        self.assertNotIn("run_id", progress[0])

    def test_forwarded_call_without_a_token_still_reports_progress(self):
        reply = self.engine.forward(tool_call(4, "decompile_all", {"output_directory": "x", "seconds": 0.6}))
        self.assertTrue(str(reply["result"]["structuredContent"]["raw_result"]["meta"]["progressToken"]).startswith("studio-"))
        self.assertTrue([p for k, p in self.events if k == "progress" and p.get("tool") == "decompile_all"])

    def test_on_call_runs_when_a_forwarded_call_starts(self):
        seen = []
        self.engine.forward(tool_call(1, "list_strings", {"detail": "summary"}), on_call=lambda n, a: seen.append((n, a)))
        self.assertEqual(seen, [("list_strings", {"detail": "summary"})])


class HandleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        support.configure(Path(self.tmp.name))
        from app.rea import ReaEngine
        self.engine = ReaEngine(command=support.fake_rea_cmd())
        self.engine.start()

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def handle(self, message, **kwargs):
        from app import mcp
        return mcp.handle(message, self.engine, **kwargs)

    def test_malformed_messages_are_refused(self):
        cases = [
            ([rpc(1, "tools/list")], None, -32600),                       # a batch
            ("tools/list", None, -32600),                                 # not an object
            ({}, None, -32600),                                           # an empty body
            ({"id": 1, "method": "tools/list"}, 1, -32600),               # no jsonrpc
            ({"jsonrpc": "1.0", "id": 1, "method": "tools/list"}, 1, -32600),
            ({"jsonrpc": "2.0", "id": True, "method": "tools/list"}, None, -32600),
            ({"jsonrpc": "2.0", "id": 1.5, "method": "tools/list"}, None, -32600),
            ({"jsonrpc": "2.0", "id": 1}, 1, -32600),                     # neither request nor response
            ({"jsonrpc": "2.0", "id": 1, "method": 7}, 1, -32600),
            ({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": [1]}, 1, -32602),
            (tool_call(1, ""), 1, -32602),
            (rpc(1, "tools/call", {"name": "binary_overview", "arguments": [1]}), 1, -32602),
        ]
        for message, reply_id, code in cases:
            reply = self.handle(message)
            self.assertEqual((reply["id"], reply["error"]["code"]), (reply_id, code), message)
        self.assertFalse(self.engine.busy())

    def test_a_response_from_the_client_is_ignored_at_once(self):
        started = time.monotonic()
        self.assertIsNone(self.handle({"jsonrpc": "2.0", "id": 9, "result": {}}))
        self.assertIsNone(self.handle({"jsonrpc": "2.0", "id": 9, "error": {"code": 1, "message": "x"}}))
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertFalse(self.engine.busy())

    def test_notifications_get_no_reply(self):
        self.assertIsNone(self.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertIsNone(self.handle({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}}))

    def test_no_engine(self):
        from app import mcp
        reply = mcp.handle(rpc(2, "tools/list"), None)
        self.assertEqual((reply["id"], reply["error"]), (2, mcp.NO_ENGINE))
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, None))

    def test_unknown_method(self):
        reply = self.handle(rpc("x", "no/such"))
        self.assertEqual((reply["id"], reply["error"]["code"]), ("x", -32601))

    def test_policy_pins_arguments_and_record_sees_them(self):
        recorded = []

        def policy(name, arguments):
            self.assertEqual(name, "inspect_web_page")
            return dict(arguments, cdp_endpoint="http://127.0.0.1:5555")

        reply = self.handle(tool_call(1, "inspect_web_page", {"cdp_endpoint": "http://127.0.0.1:9222", "target_id": "T"}),
                            policy=policy, record=lambda n, a, r: recorded.append((n, a, r)))
        self.assertEqual(reply["result"]["structuredContent"]["parameters"]["cdp_endpoint"], "http://127.0.0.1:5555")
        self.assertEqual(recorded[0][1]["cdp_endpoint"], "http://127.0.0.1:5555")
        self.assertIs(recorded[0][2], reply["result"])

    def test_policy_refusal_never_reaches_rea(self):
        from app import mcp
        recorded = []

        def policy(name, arguments):
            raise mcp.Refused("Claude may not run state-changing tools here.")

        before = self.engine._next_id
        reply = self.handle(tool_call(8, "install_adb_package", {"path": "a.apk"}), policy=policy,
                            record=lambda *a: recorded.append(a))
        self.assertEqual(reply["id"], 8)
        self.assertTrue(reply["result"]["isError"])
        error = json.loads(reply["result"]["content"][0]["text"])["error"]
        self.assertEqual(error["message"], "Claude may not run state-changing tools here.")
        self.assertEqual(recorded, [])
        self.assertEqual(self.engine._next_id, before, "the refused call was sent to REA")

    def test_record_failure_never_fails_the_call(self):
        def record(*_):
            raise RuntimeError("panel store broke")
        reply = self.handle(tool_call(1, "binary_overview"), record=record)
        self.assertIn("result", reply)

    def test_engine_error_becomes_a_json_rpc_error(self):
        reply = self.handle(tool_call(1, "decompile_all", {"output_directory": "x"}), timeout=0.5)
        self.assertEqual(reply["error"]["code"], -32000)
        self.assertIn("did not answer", reply["error"]["message"])


class FakeContractTest(unittest.TestCase):
    """What the other builders' tests rely on from tests/fake_rea.py."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        support.configure(Path(cls.tmp.name))
        from app.rea import ReaEngine
        cls.engine = ReaEngine(command=support.fake_rea_cmd())
        cls.engine.start()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()
        cls.tmp.cleanup()

    def structured(self, name, arguments=None):
        result = self.engine.call(name, arguments or {})
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        return result

    def test_untrusted_results_are_marked(self):
        for name in ("list_strings", "procedure_pseudo_code"):
            self.assertIs(self.structured(name, {"procedure": "main"})["_meta"]["provenance"]["untrusted_content"], True)
        self.assertNotIn("_meta", self.structured("binary_overview"))
        code = self.structured("procedure_pseudo_code", {"procedure": "main"})["structuredContent"]["normalized_result"]
        self.assertIn("int main(void)", code["pseudo_code"])

    def test_result_shapes(self):
        self.assertEqual(self.structured("open_binary", {"path": "C:\\x.exe"})["structuredContent"],
                         {"result": {"path": "C:\\x.exe", "kind": "executable"}})
        self.assertEqual(self.structured("binary_session")["structuredContent"]["result"]["target"], {"path": "C:\\x.exe"})
        shot = self.structured("capture_web_screenshot")["structuredContent"]["normalized_result"]
        self.assertEqual(shot["mime"], "image/png")
        self.assertTrue(shot["image"].startswith("iVBOR"))
        data = self.structured("read_bytes", {"address": "0x1000", "length": 16})["structuredContent"]["normalized_result"]
        self.assertEqual(data["bytes"], list(range(16)))
        graph = self.structured("get_call_graph", {"address": "main"})["structuredContent"]["normalized_result"]
        self.assertEqual(graph["root"], "main")
        params = self.structured("inspect_web_page", {"cdp_endpoint": "e", "target_id": "t"})["structuredContent"]["parameters"]
        self.assertEqual(params, {"cdp_endpoint": "e", "target_id": "t"})
        settings = self.structured("inspect_analysis_settings")["structuredContent"]["result"]["settings"]
        self.assertEqual(settings[0]["name"], "ghidra.decompile_timeout_seconds")
        updated = self.structured("update_analysis_settings", {"settings": [{"name": "a.b", "value": 1}]})
        self.assertEqual(updated["structuredContent"]["result"]["updated"], ["a.b"])

    def test_error_contract(self):
        result = self.engine.call("inspect_kernel_module", {"path": "x.ko"})
        error = json.loads(result["content"][0]["text"])["error"]
        self.assertTrue(result["isError"])
        self.assertEqual((error["code"], error["category"], error["remediation"]),
                         ("provider_unavailable", "provider", {"action": "Install the provider."}))

    def test_sessions_and_unknowns(self):
        session = self.structured("start_frida_session")["structuredContent"]["result"]["session_id"]
        self.assertTrue(session.startswith("frida-"))
        self.assertEqual(self.structured("close_frida_session", {"session_id": session})["structuredContent"],
                         {"result": {"closed": True}})
        made = self.structured("record_unknown", {"question": "q", "severity": "high", "domain": "d"})["structuredContent"]["result"]
        listed = self.structured("list_unknowns")["structuredContent"]["result"]
        self.assertEqual((listed["total"], listed["next_offset"]), (1, None))
        updated = self.structured("update_unknown", {"unknown_id": made["unknown_id"], "expected_revision": 1,
                                                     "status": "resolved"})
        self.assertEqual(updated["structuredContent"]["result"]["revision"], 2)
        stale = self.engine.call("update_unknown", {"unknown_id": made["unknown_id"], "expected_revision": 1, "status": "open"})
        self.assertEqual(error_code(stale), "revision_conflict")
        self.assertEqual(self.structured("verify_unknown_resolution", {"unknown_id": made["unknown_id"]})["structuredContent"],
                         {"result": {"verdict": "unresolved"}})

    def test_bundles(self):
        path = str(Path(self.tmp.name) / "bundle.json")
        self.assertEqual(self.structured("export_evidence_bundle", {"path": path})["structuredContent"], {"result": {"path": path}})
        self.assertEqual(json.loads(Path(path).read_text(encoding="utf-8")), {"bundle": True})
        self.assertEqual(self.structured("import_evidence_bundle", {"path": path})["structuredContent"],
                         {"result": {"imported": 1}})

    def test_prompts_and_completion(self):
        prompts = self.engine.request(rpc(1, "prompts/list"))["result"]["prompts"]
        self.assertEqual(prompts[1]["arguments"][0], {"name": "crash_signal", "description": "The crash signal or message.",
                                                      "required": True})
        trace = self.engine.request(rpc(2, "prompts/get", {"name": "trace_crash", "arguments": {"crash_signal": "SIGSEGV"}}))
        self.assertEqual(trace["result"]["messages"][0]["content"]["text"], "Trace the crash: SIGSEGV")
        done = self.engine.request(rpc(3, "completion/complete", {"ref": {"type": "ref/prompt", "name": "investigate_feature"},
                                                                  "argument": {"name": "feature", "value": "m"}}))
        self.assertEqual(done["result"]["completion"], {"values": ["main", "sub_401000"], "total": 2, "hasMore": False})


if __name__ == "__main__":
    unittest.main()
