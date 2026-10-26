import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support

import launch


class Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeWindow:
    def __init__(self, exits_at=None, clock=None):
        self.exits_at = exits_at
        self.clock = clock

    def poll(self):
        if self.exits_at is not None and self.clock() >= self.exits_at:
            return 0
        return None


class FakeThread:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive


class FakeStudio:
    last_seen = None


class LaunchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_windows_only(self):
        self.assertTrue(launch.supported("win32"))
        self.assertFalse(launch.supported("linux"))
        self.assertFalse(launch.supported("darwin"))

    def test_port_free_never_raises(self):
        for port in (70000, -1, "x"):
            self.assertFalse(launch.port_free(port), port)

    # ---- one instance ----

    def test_lock_is_held_once(self):
        path = self.root / "data" / "studio.lock"
        first, second = launch.InstanceLock(path), launch.InstanceLock(path)
        self.assertTrue(first.acquire())
        try:
            self.assertFalse(second.acquire())
        finally:
            first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_lock_is_held_against_another_process(self):
        path = self.root / "studio.lock"
        held = launch.InstanceLock(path)
        self.assertTrue(held.acquire())
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import launch; "
                "print(launch.InstanceLock(sys.argv[2]).acquire())")
        try:
            out = subprocess.run([sys.executable, "-c", code, str(support.REPO), str(path)],
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(out.stdout.strip(), "False", out.stderr)
        finally:
            held.release()
        out = subprocess.run([sys.executable, "-c", code, str(support.REPO), str(path)],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "True", out.stderr)

    def test_running_session(self):
        session = {"port": 5555, "token": "t", "instance": "abc"}
        good = lambda url: {"app": launch.APP, "instance": "abc"}  # noqa: E731
        self.assertEqual(launch.running_session(read=lambda: json.dumps(session), probe=good), session)
        self.assertIsNone(launch.running_session(read=lambda: json.dumps(session),
                                                 probe=lambda url: {"app": launch.APP, "instance": "other"}))
        self.assertIsNone(launch.running_session(read=lambda: json.dumps(session), probe=lambda url: None))
        self.assertIsNone(launch.running_session(read=lambda: "not json", probe=good))
        self.assertIsNone(launch.running_session(read=lambda: "[]", probe=good))

    def test_wait_for_running(self):
        clock = Clock()
        answers = iter([None, None, {"port": 1}])
        found = launch.wait_for_running(timeout=5, find=lambda: next(answers), sleep=clock.sleep, now=clock)
        self.assertEqual(found, {"port": 1})
        clock = Clock()
        self.assertIsNone(launch.wait_for_running(timeout=2, find=lambda: None, sleep=clock.sleep, now=clock))
        self.assertGreaterEqual(clock.now, 1002)

    # ---- when to stop (COR-12) ----

    def test_page_gone_rules(self):
        never = lambda: False  # noqa: E731
        gone = launch.page_gone
        self.assertFalse(gone(True, None, 0, 10_000, never))           # window process still running
        self.assertFalse(gone(False, 100, 0, 110, never))              # polled 10 s ago
        self.assertTrue(gone(False, 100, 0, 116, never))               # quiet for 16 s
        self.assertFalse(gone(False, 100, 0, 116, lambda: True))      # a window with the title is open
        self.assertFalse(gone(False, None, 0, 20, never))              # not polled yet: startup grace
        self.assertTrue(gone(False, None, 0, 31, never))
        self.assertFalse(gone(False, 5, 10, 25, never))                # a poll before the window opened is not one

    def test_watch_keeps_serving_while_the_page_polls_after_a_handoff(self):
        clock = Clock()
        studio = FakeStudio()
        window = FakeWindow(exits_at=clock.now + 1, clock=clock)    # Chrome handed the window over and exited
        opened = clock.now
        polls_until = clock.now + 120

        def sleep(seconds):
            clock.sleep(seconds)
            if clock.now < polls_until:
                studio.last_seen = clock.now

        why = launch.watch(window, FakeThread(), studio, opened, lambda: False, now=clock, sleep=sleep)
        self.assertEqual(why, "window")
        last_poll = polls_until - 0.5
        self.assertGreaterEqual(clock.now, last_poll + launch.QUIET_SECONDS)
        self.assertLess(clock.now, last_poll + launch.QUIET_SECONDS + 1)

    def test_watch_stops_when_the_server_stops(self):
        clock = Clock()
        why = launch.watch(FakeWindow(clock=clock), FakeThread(alive=False), FakeStudio(), clock.now,
                           lambda: False, now=clock, sleep=clock.sleep)
        self.assertEqual(why, "server")

    def test_watch_without_last_seen_uses_the_startup_grace(self):
        clock = Clock()
        start = clock.now
        why = launch.watch(FakeWindow(exits_at=start + 1, clock=clock), FakeThread(), object(), start,
                           lambda: False, now=clock, sleep=clock.sleep)
        self.assertEqual(why, "window")
        self.assertGreaterEqual(clock.now - start, launch.STARTUP_GRACE)


if __name__ == "__main__":
    unittest.main()
