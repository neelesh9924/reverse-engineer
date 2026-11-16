"""Run the studio on stand-ins with sample data, for screenshots, the UI smoke test, and trying the UI.

Nothing real runs: the engine is tests/fake_rea.py, Claude is tests/fake_claude.py and a
website subject's browser is tests/fake_cdp.py. The Tools page reads the real REA catalogue
when a built checkout sits beside this project (it reads one JSON file; REA itself is never
started), so it lists every real tool; otherwise it uses the stand-in catalogue from
tests/support.py. Data goes to a temporary folder, never to data/.

The sample findings come from real calls to the stand-in engine, recorded the way the studio
records any tool result, so they have the same shape as real ones (canned content).

Run from the project root:  py -3 dev/demo_server.py [--port N]
It prints the link to open; Ctrl+C stops it.
"""
import argparse
import json
import secrets
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
for _path in (ROOT, TESTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

TOKEN = secrets.token_urlsafe(24)   # this run's sign-in link; a new one each time


def real_or_fake_rea(tmp):
    """The real built checkout beside this project (its catalogue file only), else a stand-in."""
    real = ROOT.parent / "rea"
    if (real / "dist" / "mcp-catalog.json").is_file():
        return real
    import support

    return support.fake_rea_checkout(tmp)


def _say(message):
    print(f"  demo: {message}", file=sys.stderr, flush=True)


def _record(studio, slug, engine, name, arguments, source):
    """Run one tool on the stand-in engine and keep its result as a finding."""
    try:
        result = engine.call(name, arguments)
        studio.assistant.record(slug, name, arguments, result, source=source)
    except Exception as exc:
        _say(f"{name} on {slug} was not recorded: {exc}")


def _website_findings(studio, slug, url):
    from app import catalog, webtarget

    try:
        engine = studio.open_engine(slug)
        info = studio.web.open(slug, url)
    except Exception as exc:
        _say(f"the website sample was not opened: {exc}")
        return
    for name in ("inspect_web_page", "capture_web_screenshot"):
        tool = catalog.tool(name)
        props = ((tool or {}).get("inputSchema") or {}).get("properties") or {}
        try:
            arguments = webtarget.pin_web_arguments(props, {}, info, name)
        except Exception as exc:
            _say(f"{name} was not pinned to the sample browser: {exc}")
            continue
        _record(studio, slug, engine, name, arguments, "tools")


def build(tmp, port=0):
    """Configure the studio on stand-ins with sample subjects. Returns the running httpd, with
    httpd.demo = {"app", "tool", "website"}: the sample subjects' slugs."""
    from app import config, server, subjects
    from app.claude import ClaudeRunner
    import support

    tmp = Path(tmp)
    data = tmp / "data"
    data.mkdir(parents=True, exist_ok=True)
    config.DATA_DIR = data
    # The responsible-use notice counts as read, so the pictures show a finished setup.
    (data / "settings.json").write_text(json.dumps({"rea_dir": str(real_or_fake_rea(tmp)), "notice_seen": True}),
                                        encoding="utf-8")
    config.load()

    httpd = server.make_server(TOKEN, port=port,
                               runner=ClaudeRunner(prefix=support.fake_claude_prefix()),
                               engine_command=support.fake_rea_cmd(), web_launch=support.fake_web_launch)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    studio = httpd.studio

    samples = tmp / "samples"
    samples.mkdir(exist_ok=True)
    apk = samples / "notes-app-2.3.0.apk"
    apk.write_bytes(b"PK\x03\x04 sample bytes for the demo")
    tool = samples / "updater.exe"
    tool.write_bytes(b"MZ\x90\x00 sample bytes for the demo")

    app_slug = subjects.create("Notes app 2.3.0", str(apk), "archive")
    tool_slug = subjects.create("Updater utility", str(tool), "executable")
    web_slug = subjects.create("Example website", "https://example.com", "website")
    httpd.demo = {"app": app_slug, "tool": tool_slug, "website": web_slug}

    # The website first: one engine runs at a time, and the APK's should be the running one.
    _website_findings(studio, web_slug, "https://example.com")

    try:
        engine = studio.open_engine(app_slug)
    except Exception as exc:
        _say(f"the stand-in engine did not start: {exc}")
        return httpd
    _record(studio, app_slug, engine, "open_binary", subjects.open_arguments(app_slug), "claude")
    for name, arguments in (("binary_overview", {}), ("list_strings", {"detail": "summary"}),
                            ("procedure_pseudo_code", {"procedure": "main"}),
                            ("get_call_graph", {"address": "main"})):
        _record(studio, app_slug, engine, name, arguments, "claude")
    _record(studio, app_slug, engine, "read_bytes", {"address": "0x401000", "length": 64}, "tools")

    subjects.add_message(app_slug, "user", "Give me an overview of this APK and anything that stands out.")
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    steps = [{"at": now, "text": text, "detail": detail} for text, detail in (
        ("Open Binary", "path: the subject's target"), ("Binary Overview", ""),
        ("List Strings", "detail: summary"), ("Procedure Pseudo Code", "procedure: main"),
        ("Get Call Graph", "address: main"))]
    subjects.add_message(
        app_slug, "claude",
        "**Overview.** This demo runs on a stand-in engine, so these results are canned examples.\n\n"
        "- `list_strings` found two strings, `hello` and `world`. REA marks them as untrusted content "
        "from the target.\n"
        "- `main` decompiles to a function that returns 0. The call graph shows it calls `init` and `run`.\n\n"
        "Each result is in **Findings** with its confidence and limitations.",
        meta="5 steps · 21 s · demo", steps=steps)
    studio.assistant.bump()
    return httpd


def stop(httpd):
    httpd.studio.close()
    httpd.shutdown()
    httpd.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the studio on stand-ins with sample data.")
    parser.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free port)")
    args = parser.parse_args(argv)
    tmp = Path(tempfile.mkdtemp(prefix="reverse-engineer-demo-"))
    httpd = build(tmp, args.port)
    port = httpd.server_address[1]
    print(f"Reverse Engineer demo on stand-ins (data in {tmp})")
    print(f"Open: http://127.0.0.1:{port}/auth?token={TOKEN}", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop(httpd)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
