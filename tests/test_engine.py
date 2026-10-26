"""The REA engine wrapper (app/rea.py) and the observed browser (app/webtarget.py), against the
stand-ins tests/fake_rea.py and tests/fake_cdp.py."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import support


def wait_for(condition, timeout=5.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = condition()
        if value:
            return value
        time.sleep(step)
    return condition()


def error_code(result):
    """The `code` of a result in REA's error contract, or None."""
    if not result.get("isError"):
        return None
    return json.loads(result["content"][0]["text"])["error"]["code"]


def slow_start_cmd(seconds=1.5):
    """The fake engine, but only after `seconds` (a slow handshake)."""
    script = str(support.TESTS / "fake_rea.py")
    return [sys.executable, "-I", "-c",
            f"import runpy, sys, time; time.sleep({seconds}); sys.argv = [{script!r}]; "
            f"runpy.run_path({script!r}, run_name='__main__')"]


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        support.configure(Path(self.tmp.name))
        from app.rea import ReaEngine
        self.events = []
        self.engine = ReaEngine(command=support.fake_rea_cmd(), on_event=lambda k, p: self.events.append((k, p)))

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

    def running_tool(self, name="decompile_all"):
        """Wait until a call to `name` has reached REA."""
        return wait_for(lambda: any(c.written and c.name == name for c in list(self.engine._inflight.values())))

    def test_handshake_and_tools(self):
        result = self.engine.start()
        self.assertIn("serverInfo", result)
        self.assertTrue(self.engine.alive())
        reply = self.engine.request({"jsonrpc": "2.0", "id": 99, "method": "tools/list"})
        self.assertEqual(reply["id"], 99)   # the caller's id comes back, whatever went on the wire
        names = [t["name"] for t in reply["result"]["tools"]]
        self.assertIn("binary_overview", names)

    def test_session_is_new_per_start(self):
        from app.rea import ReaEngine
        self.engine.start()
        first = self.engine.session
        other = ReaEngine(command=support.fake_rea_cmd())
        try:
            other.start()
            self.assertTrue(first and other.session and first != other.session)
        finally:
            other.close()

    def test_call_returns_evidence(self):
        self.engine.start()
        result = self.engine.call("binary_overview", {})
        self.assertIn("structuredContent", result)
        self.assertEqual(result["structuredContent"]["operation"], "binary_overview")
        self.assertIn("limitations", result["structuredContent"])
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])

    def test_forward_initialize_is_cached(self):
        self.engine.start()
        reply = self.engine.forward({"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": {}})
        self.assertEqual(reply["id"], 7)
        self.assertIn("serverInfo", reply["result"])

    def test_forward_notification_returns_none(self):
        self.engine.start()
        self.assertIsNone(self.engine.forward({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_calls_are_serialised(self):
        self.engine.start(env_extra={"FAKE_REA_BEHAVIOUR": "slow"})
        results = []
        lock = threading.Lock()
        active = []
        peak = []

        def run():
            r = self.engine.call("list_strings", {})
            with lock:
                results.append(r["structuredContent"]["operation"])

        def watch():
            while len(results) < 3:
                active.append(sum(1 for c in self.engine._inflight.values() if c.written and c.reply is None))
                peak.append(max(active))
                time.sleep(0.05)

        threads = [threading.Thread(target=run) for _ in range(3)]
        watcher = threading.Thread(target=watch, daemon=True)
        for t in threads:
            t.start()
        watcher.start()
        for t in threads:
            t.join(timeout=20)
        self.assertEqual(results, ["list_strings"] * 3)
        self.assertLessEqual(max(peak or [0]), 1, "more than one tool call reached REA at once")

    def test_close_releases(self):
        self.engine.start()
        self.engine.close()
        self.assertFalse(self.engine.alive())
        from app.rea import EngineError
        with self.assertRaises(EngineError):
            self.engine.call("binary_overview", {})

    # ---- cancelling ----

    def test_cancel_slow_call_frees_the_lock(self):
        self.engine.start()
        thread, box = self.in_thread(self.engine.call, "decompile_all", {"output_directory": "x"}, run_id="r1")
        self.assertTrue(self.running_tool())
        started = time.monotonic()
        self.engine.cancel("r1")
        thread.join(timeout=5)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(error_code(box["value"]), "cancelled")
        self.assertTrue(self.engine._call_lock.acquire(timeout=0.5), "the call lock stayed held")
        self.engine._call_lock.release()
        self.assertEqual(self.engine.call("binary_overview", {})["structuredContent"]["operation"], "binary_overview")
        self.assertFalse(self.engine.busy())

    def test_cancel_when_rea_sends_no_reply(self):
        # REA's SDK sends nothing for an aborted request; the studio answers for it after a short grace.
        self.engine.start(env_extra={"FAKE_REA_BEHAVIOUR": "silent-cancel"})
        thread, box = self.in_thread(self.engine.call, "decompile_all", {"output_directory": "x"}, run_id="r1")
        self.assertTrue(self.running_tool())
        started = time.monotonic()
        self.engine.cancel()   # every call in flight
        thread.join(timeout=5)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(error_code(box["value"]), "cancelled")
        self.assertFalse(self.engine._call_lock.locked())

    def test_cancel_a_queued_call(self):
        self.engine.start()
        first, first_box = self.in_thread(self.engine.call, "decompile_all", {"output_directory": "x"}, run_id="r1")
        self.assertTrue(self.running_tool())
        second, second_box = self.in_thread(self.engine.call, "binary_overview", {}, run_id="r2")
        self.assertTrue(wait_for(lambda: len(self.engine.running()) == 2))
        self.engine.cancel("r2")
        second.join(timeout=2)
        self.assertEqual(error_code(second_box["value"]), "cancelled")
        self.assertTrue(first.is_alive(), "cancelling the queued call stopped the running one")
        self.engine.cancel("r1")
        first.join(timeout=5)
        self.assertEqual(error_code(first_box["value"]), "cancelled")

    def test_timeout_cancels_and_frees_the_lock(self):
        from app.rea import EngineError
        self.engine.start()
        started = time.monotonic()
        with self.assertRaises(EngineError) as caught:
            self.engine.call("decompile_all", {"output_directory": "x"}, timeout=0.6)
        self.assertIn("did not answer", str(caught.exception))
        self.assertLess(time.monotonic() - started, 2)
        self.assertFalse(self.engine._call_lock.locked())
        self.assertEqual(self.engine.call("binary_overview", {})["structuredContent"]["operation"], "binary_overview")

    def test_one_deadline_covers_the_queue_and_the_call(self):
        # A call that first waits for the lock gets only what is left of its timeout, so the
        # whole call never takes longer than one engine timeout (Claude's MCP deadline is that
        # plus headroom).
        from app.rea import EngineError
        self.engine.start()
        holder, _ = self.in_thread(self.engine.call, "decompile_all", {"output_directory": "x"}, run_id="r1")
        self.assertTrue(self.running_tool())
        threading.Timer(0.8, lambda: self.engine.cancel("r1")).start()
        started = time.monotonic()
        with self.assertRaises(EngineError):
            self.engine.call("decompile_all", {"output_directory": "y"}, timeout=1.5)
        self.assertLess(time.monotonic() - started, 2.1, "the queued call got a second full timeout")
        holder.join(timeout=5)

    def test_cancel_forwarded_leaves_direct_runs_alone(self):
        self.engine.start()
        direct, direct_box = self.in_thread(self.engine.call, "decompile_all", {"output_directory": "x"}, run_id="r1")
        self.assertTrue(self.running_tool())
        forwarded, forwarded_box = self.in_thread(self.engine.forward, {
            "jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "binary_overview", "arguments": {}}})
        self.assertTrue(wait_for(lambda: len(self.engine.running()) == 2))
        self.engine.cancel_forwarded()
        forwarded.join(timeout=3)
        self.assertEqual(error_code(forwarded_box["value"]["result"]), "cancelled")
        self.assertTrue(direct.is_alive(), "Stop cancelled the user's own run")
        self.engine.cancel("r1")
        direct.join(timeout=5)
        self.assertEqual(error_code(direct_box["value"]), "cancelled")

    # ---- progress ----

    def test_progress_reaches_callback_and_on_event(self):
        self.engine.start()
        seen = []
        result = self.engine.call("decompile_all", {"output_directory": "x", "seconds": 0.8},
                                  progress=seen.append, run_id="p1")
        self.assertIsNone(error_code(result))
        self.assertGreaterEqual(len(seen), 2)
        self.assertTrue(all(p["message"].startswith("decompile:") for p in seen))
        self.assertEqual(seen[0]["run_id"], "p1")
        self.assertEqual(seen[0]["tool"], "decompile_all")
        events = [p for k, p in self.events if k == "progress"]
        self.assertTrue(events and events[0]["run_id"] == "p1", self.events)
        token = result["structuredContent"]["raw_result"]["meta"]["progressToken"]
        self.assertTrue(str(token).startswith("studio-"))

    # ---- diagnosis ----

    def test_stderr_tail_is_the_end_of_the_output(self):
        from app.rea import STDERR_KEEP, EngineError
        self.engine.start(env_extra={"FAKE_REA_BEHAVIOUR": "noisy-crash"})
        with self.assertRaises(EngineError) as caught:
            self.engine.call("binary_overview", {})
        self.assertIn("the real reason REA stopped", str(caught.exception))
        self.assertIn("exit code 3", str(caught.exception))
        kept = self.engine.stderr_text()
        self.assertTrue(kept.endswith("FATAL: the real reason REA stopped"), kept[-200:])
        self.assertLessEqual(len(kept), STDERR_KEEP)
        self.assertNotIn("startup noise 000", kept)
        crashed = [p for k, p in self.events if k == "engine" and p.get("state") == "crashed"]
        self.assertTrue(crashed and "the real reason" in crashed[0]["error"])

    def test_engine_environment_is_scrubbed(self):
        planted = {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_BASE_URL": "http://x", "CLAUDE_CONFIG_DIR": "c",
                   "CLAUDECODE": "1", "MCP_TOOL_TIMEOUT": "1", "GITHUB_TOKEN": "g", "AWS_SECRET_ACCESS_KEY": "a",
                   "MY_SERVICE_PASSWORD": "p", "KEEP_THIS_ONE": "k", "REA_SOME_TOKEN": "r"}
        with mock.patch.dict(os.environ, planted):
            self.engine.start(env_extra={"GHIDRA_INSTALL_DIR": "C:\\ghidra"})
        names = {n.upper() for n in self.engine.call("fake_environment", {})["structuredContent"]["result"]["names"]}
        for gone in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR", "CLAUDECODE", "MCP_TOOL_TIMEOUT",
                     "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "MY_SERVICE_PASSWORD"):
            self.assertNotIn(gone, names)
        for kept in ("KEEP_THIS_ONE", "REA_SOME_TOKEN", "GHIDRA_INSTALL_DIR", "PATH"):
            self.assertIn(kept, names)

    def test_scrubbed_env_keeps_the_studio_extras(self):
        from app.rea import scrubbed_env
        env = scrubbed_env({"PATH": "p", "OPENAI_API_KEY": "o", "Some_Token": "t"}, {"REA_ADB_PATH": "adb"})
        self.assertEqual(env, {"PATH": "p", "REA_ADB_PATH": "adb"})


class EnginesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        support.configure(Path(self.tmp.name))
        self.events = []
        self.env = {}
        self.busy = set()
        self.engines = None

    def make(self, command=None):
        from app.rea import Engines
        self.engines = Engines(on_event=lambda s, k, p: self.events.append((s, k, p)),
                               command=command or support.fake_rea_cmd(),
                               env_provider=lambda: dict(self.env), busy=lambda slug: slug in self.busy)
        return self.engines

    def tearDown(self):
        if self.engines:
            self.engines.shutdown()
        self.tmp.cleanup()

    def test_crash_state_and_recovery(self):
        from app.rea import EngineError
        engines = self.make()
        self.env["FAKE_REA_BEHAVIOUR"] = "crash"
        engine = engines.open("a")
        self.assertEqual(engines.state_of("a")["state"], "running")
        with self.assertRaises(EngineError):
            engine.call("binary_overview", {})
        state = wait_for(lambda: engines.state_of("a")["state"] == "crashed" and engines.state_of("a"))
        self.assertTrue(state, engines.state_of("a"))
        self.assertIn("exit code 1", state["error"])
        self.assertIsNone(engines.engine_for("a"))
        self.assertTrue(any(s == "a" and k == "engine" and p.get("state") == "crashed" for s, k, p in self.events))
        status = engines.status()
        self.assertEqual((status["slug"], status["state"], status["running"]), ("a", "crashed", False))
        # Starting again clears the error.
        self.env.clear()
        engines.open("a").call("binary_overview", {})
        self.assertEqual(engines.state_of("a"), {"state": "running", "error": "", "session": engines.engine_for("a").session})

    def test_failed_start_reports_why(self):
        from app.rea import EngineError
        engines = self.make([sys.executable, "-I", "-c", "import sys; sys.stderr.write('boom: no target\\n'); sys.exit(2)"])
        with self.assertRaises(EngineError):
            engines.open("a")
        state = engines.state_of("a")
        self.assertEqual(state["state"], "stopped")
        self.assertTrue(state["error"])
        self.assertIsNone(engines.engine_for("a"))

    def test_status_never_blocks_while_starting(self):
        engines = self.make(slow_start_cmd(1.5))
        opener = threading.Thread(target=engines.open, args=("a",), daemon=True)
        opener.start()
        self.assertTrue(wait_for(lambda: engines.state_of("a")["state"] == "starting"))
        started = time.monotonic()
        status = engines.status()
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertEqual((status["slug"], status["state"]), ("a", "starting"))
        self.assertIsNone(engines.engine_for("a"))
        opener.join(timeout=20)
        self.assertEqual(engines.state_of("a")["state"], "running")

    def test_concurrent_opens_share_one_start(self):
        engines = self.make(slow_start_cmd(0.8))
        got = []
        threads = [threading.Thread(target=lambda: got.append(engines.open("a"))) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        self.assertEqual(len(got), 3)
        self.assertTrue(got[0] is got[1] is got[2])

    def test_refuses_to_swap_away_from_a_busy_subject(self):
        from app.rea import EngineBusy
        engines = self.make()
        engine_a = engines.open("a")
        self.busy.add("a")
        with self.assertRaises(EngineBusy):
            engines.open("b")
        self.assertIs(engines.engine_for("a"), engine_a)
        self.busy.clear()
        engines.open("b")
        self.assertEqual(engines.state_of("a")["state"], "stopped")
        self.assertEqual(engines.state_of("b")["state"], "running")
        self.assertFalse(engine_a.alive())

    def test_refuses_to_swap_while_a_call_is_in_flight(self):
        from app.rea import EngineBusy
        engines = self.make()
        engine_a = engines.open("a")
        thread = threading.Thread(target=engine_a.call, args=("decompile_all", {"output_directory": "x"}),
                                  kwargs={"run_id": "r1"}, daemon=True)
        thread.start()
        self.assertTrue(wait_for(engine_a.busy))
        with self.assertRaises(EngineBusy):
            engines.open("b")
        self.assertTrue(engines.running("a"))
        engines.cancel("a", "r1")
        thread.join(timeout=5)
        engines.open("b")
        self.assertEqual(engines.state_of("b")["state"], "running")

    def test_refuses_to_swap_away_from_a_starting_engine(self):
        from app.rea import EngineBusy
        engines = self.make(slow_start_cmd(1.0))
        opener = threading.Thread(target=engines.open, args=("a",), daemon=True)
        opener.start()
        self.assertTrue(wait_for(lambda: engines.state_of("a")["state"] == "starting"))
        with self.assertRaises(EngineBusy):
            engines.open("b")
        opener.join(timeout=20)
        self.assertEqual(engines.state_of("a")["state"], "running")

    def test_close_while_starting(self):
        from app.rea import EngineError
        engines = self.make(slow_start_cmd(1.0))
        box = {}

        def run():
            try:
                engines.open("a")
            except EngineError as exc:
                box["error"] = exc

        opener = threading.Thread(target=run, daemon=True)
        opener.start()
        self.assertTrue(wait_for(lambda: engines.state_of("a")["state"] == "starting"))
        engines.close("a")
        opener.join(timeout=20)
        self.assertIn("error", box)
        self.assertEqual(engines.state_of("a")["state"], "stopped")
        self.assertIsNone(engines.engine_for("a"))


def fake_browser(behaviour="", write_port=None, started=None):
    """A launch hook for WebTargets: the fake CDP server, with FAKE_CDP_BEHAVIOUR set."""
    def launch(browser, port, profile, url):
        if write_port is not None:
            Path(profile, "DevToolsActivePort").write_text(f"{write_port}\n/devtools/browser/x\n", encoding="utf-8")
        args = [sys.executable, "-I", str(support.TESTS / "fake_cdp.py"), str(port), url]
        env = dict(os.environ, FAKE_CDP_BEHAVIOUR=behaviour() if callable(behaviour) else behaviour)
        proc = subprocess.Popen(args, env=env)
        if started is not None:
            started.append(proc)
        return proc
    return launch


class WebTargetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        support.configure(Path(self.tmp.name))
        self.webs = []

    def tearDown(self):
        for web in self.webs:
            web.shutdown()
        self.tmp.cleanup()

    def make(self, launch):
        from app.webtarget import WebTargets
        web = WebTargets(launch=launch)
        self.webs.append(web)
        return web

    def test_open_finds_the_page(self):
        web = self.make(fake_browser())
        info = web.open("site", "https://Example.com/")
        self.assertTrue(info["running"])
        self.assertTrue(info["endpoint"].startswith("http://127.0.0.1:"))
        self.assertEqual(info["target_id"], "PAGE-TARGET-1")
        self.assertEqual(info["allowed_origins"], ["https://example.com"])
        state = web.state_of("site")
        self.assertEqual((state["state"], state["target_id"]), ("running", "PAGE-TARGET-1"))

    def test_failed_start_kills_the_browser_and_a_retry_works(self):
        from app.webtarget import WebTargetError
        mode = ["silent"]
        procs = []
        web = self.make(fake_browser(lambda: mode[0], started=procs))
        box = {}

        def run():
            try:
                web.open("site", "https://example.com", timeout=1.5)
            except WebTargetError as exc:
                box["error"] = exc

        opener = threading.Thread(target=run, daemon=True)
        opener.start()
        self.assertTrue(wait_for(lambda: web.state_of("site")["state"] == "starting"))
        started = time.monotonic()
        self.assertIsNone(web.info("site"))
        self.assertLess(time.monotonic() - started, 0.2)
        opener.join(timeout=10)
        self.assertIn("DevTools did not come up", str(box.get("error")))
        self.assertIsNotNone(procs[0].poll(), "the browser was left running after a failed start")
        state = web.state_of("site")
        self.assertEqual(state["state"], "stopped")
        self.assertTrue(state["error"])
        mode[0] = ""
        self.assertTrue(web.open("site", "https://example.com")["running"])

    def test_non_json_reply_is_handled(self):
        from app.webtarget import WebTargetError
        procs = []
        web = self.make(fake_browser("garbage", started=procs))
        with self.assertRaises(WebTargetError):
            web.open("site", "https://example.com", timeout=1.2)
        self.assertIsNotNone(procs[0].poll())

    def test_something_else_on_the_port_is_refused(self):
        from app.webtarget import WebTargetError
        procs = []
        web = self.make(fake_browser("imposter", started=procs))
        with self.assertRaises(WebTargetError) as caught:
            web.open("site", "https://example.com", timeout=5)
        self.assertIn("other than a browser", str(caught.exception))
        self.assertIsNotNone(procs[0].poll())

    def test_devtools_port_file_must_name_our_port(self):
        from app.webtarget import WebTargetError
        procs = []
        web = self.make(fake_browser(write_port=1, started=procs))
        with self.assertRaises(WebTargetError) as caught:
            web.open("site", "https://example.com", timeout=5)
        self.assertIn("Another program", str(caught.exception))
        self.assertIsNotNone(procs[0].poll())

    def test_new_tab_uses_put_with_the_url_encoded(self):
        import urllib.request
        url = "https://example.com/a?x=1&y=2#part"
        web = self.make(fake_browser("nopage"))
        info = web.open("site", url)
        self.assertEqual(info["target_id"], "PAGE-NEW-1")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(info["endpoint"] + "/json", timeout=5) as response:
            pages = json.loads(response.read())
        self.assertEqual(pages[0]["url"], url)

    def test_unsafe_url_is_refused(self):
        from app.webtarget import WebTargetError
        procs = []
        web = self.make(fake_browser(started=procs))
        with self.assertRaises(WebTargetError):
            web.open("site", "https://user:pass@example.com/")
        self.assertEqual(procs, [])

    def test_engine_and_browser_close_independently(self):
        from app.rea import Engines
        engines = Engines(command=support.fake_rea_cmd())
        try:
            web = self.make(fake_browser())
            engines.open("site")
            web.open("site", "https://example.com")
            web.close("site")
            self.assertIsNone(web.info("site"))
            self.assertEqual(engines.state_of("site")["state"], "running")
            web.open("site", "https://example.com")
            engines.close("site")
            self.assertIsNotNone(web.info("site"))
        finally:
            engines.shutdown()

    def test_a_busy_subjects_browser_is_not_swapped_out(self):
        from app.webtarget import WebTargetError, WebTargets
        web = WebTargets(launch=fake_browser(), busy=lambda slug: slug == "site")
        self.webs.append(web)
        web.open("site", "https://example.com")
        with self.assertRaises(WebTargetError):
            web.open("other", "https://example.org")
        self.assertTrue(web.info("site"), "the busy subject's browser was closed")


class UrlTest(unittest.TestCase):
    def test_safe_url(self):
        from app.webtarget import safe_url
        self.assertEqual(safe_url("https://example.com"), "https://example.com")
        self.assertEqual(safe_url("  HTTPS://Example.COM:443/Path?q=1#f "), "https://example.com/Path?q=1#f")
        self.assertEqual(safe_url("http://example.com:8080/x"), "http://example.com:8080/x")
        self.assertEqual(safe_url("http://[::1]:9000/"), "http://[::1]:9000/")
        for bad in ("https://user:pass@site.example/", "https://@site.example", "ftp://example.com",
                    "javascript:alert(1)", "https://", "https://exa mple.com", "https://example.com/\tx",
                    "https://example.com\\@evil.example", "https://example.com:99999", "https://example.com:0",
                    "not a url", "", None, 42, "http://[zz]/", "https://exa%6dple.com", "https://ex\x00ample.com"):
            self.assertIsNone(safe_url(bad), bad)

    def test_allowed_origins(self):
        from app.webtarget import allowed_origins
        self.assertEqual(allowed_origins("https://Site.Example:443/x?y"), ["https://site.example"])
        self.assertEqual(allowed_origins("http://localhost:8080/"), ["http://localhost:8080"])
        self.assertEqual(allowed_origins("http://example.com:80"), ["http://example.com"])
        self.assertEqual(allowed_origins("https://user:pass@site.example/"), [])
        self.assertEqual(allowed_origins("file:///c:/x"), [])

    def test_pin_web_arguments(self):
        from app.webtarget import WebTargetError, pin_web_arguments
        props = {"cdp_endpoint": {}, "target_id": {}, "allowed_origins": {}, "selector": {}}
        info = {"endpoint": "http://127.0.0.1:5555", "target_id": "PAGE-1", "allowed_origins": ["https://example.com"]}
        asked = {"cdp_endpoint": "http://127.0.0.1:9222", "target_id": "OTHER", "selector": "a",
                 "allowed_origins": ["https://evil.example", "https://EXAMPLE.com:443"]}
        pinned = pin_web_arguments(props, asked, info)
        self.assertEqual(pinned, {"cdp_endpoint": "http://127.0.0.1:5555", "target_id": "PAGE-1", "selector": "a",
                                  "allowed_origins": ["https://example.com"]})
        self.assertEqual(asked["target_id"], "OTHER", "the caller's dict was changed")
        # Nothing in common, or nothing asked: the subject's origin, never wider.
        self.assertEqual(pin_web_arguments(props, {"allowed_origins": ["https://evil.example"]}, info)["allowed_origins"],
                         ["https://example.com"])
        self.assertEqual(pin_web_arguments(props, {}, info)["allowed_origins"], ["https://example.com"])
        # Only the tool's own inputs are set.
        listing = pin_web_arguments({"cdp_endpoint": {}}, {}, info)
        self.assertEqual(listing, {"cdp_endpoint": "http://127.0.0.1:5555"})
        # Tools without web inputs pass unchanged, even with no browser.
        self.assertEqual(pin_web_arguments({"path": {}}, {"path": "p"}, None), {"path": "p"})
        with self.assertRaises(WebTargetError):
            pin_web_arguments(props, {}, None)
        # A V8 inspector or an Electron app is not the observed browser: its own target_id stays.
        inspector = {"inspector_endpoint": {}, "target_id": {}}
        asked = {"inspector_endpoint": "http://127.0.0.1:9229", "target_id": "node-uuid"}
        self.assertEqual(pin_web_arguments(inspector, asked, info, "observe_javascript_runtime"), asked)
        electron = {"cdp_endpoint": {}, "target_id": {}}
        asked = {"cdp_endpoint": "http://127.0.0.1:9333", "target_id": "electron-page"}
        self.assertEqual(pin_web_arguments(electron, asked, None, "inspect_electron_page"), asked)

    def test_browser_tools_are_told_apart(self):
        from app.webtarget import is_browser_tool, local_endpoints
        web = {"cdp_endpoint": {}, "target_id": {}, "allowed_origins": {}}
        self.assertTrue(is_browser_tool("inspect_web_page", web))
        self.assertTrue(is_browser_tool("list_browser_targets", {"cdp_endpoint": {}, "allowed_origins": {}}))
        self.assertFalse(is_browser_tool("inspect_electron_page", {"cdp_endpoint": {}, "target_id": {}}))
        self.assertFalse(is_browser_tool("list_electron_targets", {"cdp_endpoint": {}}))
        self.assertFalse(is_browser_tool("observe_javascript_runtime", {"inspector_endpoint": {}, "target_id": {}}))
        self.assertEqual(local_endpoints({"inspector_endpoint": {}, "target_id": {}}), ["inspector_endpoint"])


if __name__ == "__main__":
    unittest.main()
