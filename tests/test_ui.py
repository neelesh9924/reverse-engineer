"""Headless-browser smoke test of the page, over the demo server (dev/demo_server.py) on stand-ins.

It loads the studio in headless Chrome or Edge (dev/cdp.py) and checks what a user would hit
first: the page loads without script errors, a subject shows its findings, the Tools page lists
tools and builds a form, Settings renders, the new-subject dialog opens, closes on Escape and
opens again, Ctrl+K opens the palette, every button has an accessible name, and each view has
exactly one visible h1. Past that first look it opens every finding card, shows the website
subject's screenshot, opens a subject with no findings, checks a dialog returns focus, jumps to a
page from the palette, toggles the theme, runs a tool from the Tools page, asks Claude (the
stand-in) and gets an answer, and checks a 400px-wide window never scrolls sideways.

Skipped when no Chrome or Edge is found, or when REVERSE_ENGINEER_SKIP_UI=1 (as CI sets).
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
for _path in (REPO, REPO / "dev", REPO / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import cdp  # noqa: E402  (after the path tweak)

# Accessible name of every button, as far as a smoke test can tell: its text, aria-label,
# aria-labelledby's text, or title. Returns the buttons that have none.
UNNAMED_BUTTONS = r"""
(() => {
  const named = (b) => {
    if ((b.textContent || "").trim()) return true;
    if ((b.getAttribute("aria-label") || "").trim()) return true;
    const ids = (b.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    if (ids.some((id) => ((document.getElementById(id) || {}).textContent || "").trim())) return true;
    if ((b.getAttribute("title") || "").trim()) return true;
    return false;
  };
  return [...document.querySelectorAll("button")].filter((b) => !named(b))
    .map((b) => b.outerHTML.slice(0, 160));
})()
"""

# The h1 elements a user can see (rendered, not inside anything hidden).
VISIBLE_H1 = r"""
(() => [...document.querySelectorAll("h1")].filter((el) =>
  el.checkVisibility ? el.checkVisibility() : el.getClientRects().length > 0)
  .map((el) => (el.id || "") + ": " + (el.textContent || "").trim().slice(0, 60)))()
"""

OPEN_DIALOG = "document.querySelector('dialog[open]')"


def _problem_events(events):
    """Script exceptions and console or log errors, as short readable lines."""
    found = []
    for event in events:
        method, params = event.get("method"), event.get("params") or {}
        if method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            text = (details.get("exception") or {}).get("description") or details.get("text") or "an exception"
            found.append(f"exception: {text.splitlines()[0]} ({details.get('url', '')}:{details.get('lineNumber', '')})")
        elif method == "Runtime.consoleAPICalled" and params.get("type") in ("error", "assert"):
            parts = [str(arg.get("value", arg.get("description", ""))) for arg in params.get("args") or []]
            found.append("console." + params.get("type") + ": " + " ".join(parts)[:300])
        elif method == "Log.entryAdded" and (params.get("entry") or {}).get("level") == "error":
            entry = params["entry"]
            found.append(f"log ({entry.get('source')}): {entry.get('text', '')[:300]} {entry.get('url', '')}")
    return found


def _browser():
    if os.environ.get("REVERSE_ENGINEER_SKIP_UI") == "1":
        return None
    from app import config
    return config.browser_path()


class PageSmokeTest(unittest.TestCase):
    """One demo server and one headless page, shared by the checks below (which run in order)."""

    maxDiff = None   # show every page error, not the first few

    @classmethod
    def setUpClass(cls):
        chrome = _browser()
        if not chrome:
            raise unittest.SkipTest("no Chrome or Edge found (or REVERSE_ENGINEER_SKIP_UI=1)")
        import demo_server

        cls.demo_server = demo_server
        cls.tmp = Path(tempfile.mkdtemp(prefix="reverse-engineer-ui-"))
        cls.httpd = demo_server.build(cls.tmp)
        cls.browser = cdp.Browser(chrome).start()
        try:
            cls.page = cls.browser.page()
            for domain in ("Page", "Runtime", "Log"):
                cls.page.call(f"{domain}.enable")
            base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
            cls.page.navigate(f"{base}/auth?token={demo_server.TOKEN}")
            cls.loaded = cls.page.wait("document.readyState === 'complete' && window.RE && "
                                       "document.querySelectorAll('#list li').length >= 3", timeout=30, after=1.5)
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        browser = getattr(cls, "browser", None)
        if browser is not None:
            browser.close()
        httpd = getattr(cls, "httpd", None)
        if httpd is not None:
            cls.demo_server.stop(httpd)
        if getattr(cls, "tmp", None):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---- helpers ----

    def go(self, hash_):
        self.page.evaluate(f"location.hash = {json.dumps(hash_)}")

    def until(self, condition, what, timeout=20):
        self.assertTrue(self.page.wait(condition, timeout=timeout, after=0.3), f"{what} (waited for: {condition})")

    def escape(self):
        self.page.key("Escape", "Escape", 27)

    def assert_view_is_sound(self, view):
        unnamed = self.page.evaluate(UNNAMED_BUTTONS)
        self.assertEqual(unnamed, [], f"{view}: buttons without an accessible name")
        h1s = self.page.evaluate(VISIBLE_H1)
        self.assertEqual(len(h1s), 1, f"{view}: expected exactly one visible h1, found {h1s}")

    def assert_no_page_errors(self, when):
        self.page.pump(0.5)
        problems = _problem_events(self.page.events)
        self.assertEqual(problems, [], f"script or console errors {when}")

    # ---- the checks ----

    def test_01_loads_without_errors(self):
        self.assertTrue(self.loaded, "the studio did not finish loading with the demo's three subjects")
        self.assert_no_page_errors("on load")

    def test_02_subject_page_shows_findings(self):
        self.go(self.httpd.demo["app"])
        self.until("document.querySelectorAll('#subjectPage:not([hidden]) .ev').length >= 1",
                   "the subject page shows no finding")
        self.assert_view_is_sound("subject page")

    def test_03_tools_page_lists_tools_and_builds_a_form(self):
        self.go("~tools")
        self.until("document.querySelectorAll('#toolsPage:not([hidden]) .tl-tool').length > 0",
                   "the Tools page lists no tools")
        self.page.evaluate("document.querySelector('#toolsPage button[data-name][aria-expanded]').click()")
        self.until("(() => { const b = document.querySelector('#toolsPage button[data-name][aria-expanded=\"true\"]');"
                   " const body = b && document.getElementById(b.getAttribute('aria-controls'));"
                   " return body && !body.hidden && body.querySelector('form'); })()",
                   "opening a tool did not build its form")
        self.assert_view_is_sound("Tools page")

    def test_04_settings_page_renders(self):
        self.go("~settings")
        self.until("(() => { const p = document.getElementById('settingsPage');"
                   " return p && !p.hidden && p.querySelectorAll('h2, h3').length > 0; })()",
                   "the Settings page did not render")
        self.assert_view_is_sound("Settings page")

    def test_05_new_subject_dialog_closes_on_escape_and_reopens(self):
        self.go(self.httpd.demo["app"])
        self.until("!document.getElementById('subjectPage').hidden", "the subject page did not come back")
        self.go("~new")
        self.until(OPEN_DIALOG, "#~new opened no dialog")
        self.assertEqual(self.page.evaluate(UNNAMED_BUTTONS), [], "new-subject dialog: buttons without a name")
        self.escape()
        self.until(f"!{OPEN_DIALOG}", "Escape did not close the new-subject dialog")
        self.go("~new")
        self.until(OPEN_DIALOG, "a second #~new did not open the dialog again")
        self.escape()
        self.until(f"!{OPEN_DIALOG}", "Escape did not close the reopened dialog")

    def test_06_ctrl_k_opens_the_palette(self):
        self.page.evaluate("document.activeElement && document.activeElement.blur && document.activeElement.blur()")
        self.page.key("k", "KeyK", 75, modifiers=2)
        self.until(f"{OPEN_DIALOG} && {OPEN_DIALOG}.querySelector('[role=combobox]')",
                   "Ctrl+K did not open the palette")
        self.assertEqual(self.page.evaluate(UNNAMED_BUTTONS), [], "palette: buttons without a name")
        self.escape()
        self.until(f"!{OPEN_DIALOG}", "Escape did not close the palette")

    def test_07_every_finding_card_opens_and_renders(self):
        self.go(self.httpd.demo["app"])
        self.until("document.querySelectorAll('#subjectPage:not([hidden]) .ev').length >= 5",
                   "the subject page lost its findings")
        self.page.evaluate("document.querySelectorAll('#subjectPage .ev:not([open]) > summary')"
                           ".forEach((s) => s.click())")
        self.until("[...document.querySelectorAll('#subjectPage .ev')].every((d) => d.open && "
                   "d.querySelector('.ev-body') && d.querySelector('.ev-body').textContent.trim().length > 0)",
                   "a finding card opened without a rendered body")
        empty = self.page.evaluate("[...document.querySelectorAll('#subjectPage .ev')]"
                                   ".filter((d) => d.querySelector('.ev-body').getClientRects().length === 0)"
                                   ".map((d) => d.id)")
        self.assertEqual(empty, [], "finding bodies that render nothing visible")
        self.assert_view_is_sound("subject page with every finding open")
        self.assert_no_page_errors("after opening every finding")

    def test_08_website_subject_shows_its_screenshot(self):
        self.go(self.httpd.demo["website"])
        self.until("document.querySelectorAll('#subjectPage:not([hidden]) .ev').length >= 2",
                   "the website subject shows no finding")
        self.page.evaluate("document.querySelectorAll('#subjectPage .ev:not([open]) > summary')"
                           ".forEach((s) => s.click())")
        self.until("[...document.querySelectorAll('#subjectPage .ev-img')].some((img) => img.complete && "
                   "img.naturalWidth > 0)", "the captured screenshot did not render as an image")
        self.assert_view_is_sound("website subject page")

    def test_09_subject_without_findings_renders(self):
        self.go(self.httpd.demo["tool"])
        self.until("(() => { const t = document.getElementById('title');"
                   " return !document.getElementById('subjectPage').hidden && t && /Updater/.test(t.textContent); })()",
                   "the subject without findings did not open")
        self.until("document.querySelector('#panels').textContent.trim().length > 0", "its panels are empty")
        self.assert_view_is_sound("subject page without findings")
        self.assert_no_page_errors("on a subject without findings")

    def test_10_subject_settings_dialog_returns_focus(self):
        self.go(self.httpd.demo["app"])
        self.until("!document.getElementById('subjectPage').hidden && !document.getElementById('subjectSettings').hidden",
                   "the subject settings button is not shown")
        self.page.evaluate("document.getElementById('subjectSettings').focus();"
                           "document.getElementById('subjectSettings').click()")
        self.until(f"{OPEN_DIALOG} && {OPEN_DIALOG}.contains(document.activeElement)",
                   "subject settings opened no dialog with focus inside")
        self.assertEqual(self.page.evaluate(UNNAMED_BUTTONS), [], "subject settings: buttons without a name")
        self.escape()
        self.until(f"!{OPEN_DIALOG} && document.activeElement && document.activeElement.id === 'subjectSettings'",
                   "closing the dialog did not return focus to its opener")

    def test_11_palette_search_navigates(self):
        self.page.key("k", "KeyK", 75, modifiers=2)
        self.until(f"{OPEN_DIALOG} && {OPEN_DIALOG}.querySelector('[role=combobox]')", "Ctrl+K did not open the palette")
        self.page.call("Input.insertText", {"text": "Settings"})
        self.until(f"{OPEN_DIALOG}.querySelectorAll('[role=option]').length > 0", "the palette found nothing for Settings")
        self.page.key("Enter", "Enter", 13)
        self.until(f"!{OPEN_DIALOG} && location.hash === '#~settings' && !document.getElementById('settingsPage').hidden",
                   "choosing Settings in the palette did not open Settings")

    def test_12_theme_toggle_switches_and_stays_in_sync(self):
        before = self.page.evaluate("document.documentElement.dataset.theme || ''")
        self.page.evaluate("document.getElementById('themeBtn').click()")
        self.until(f"(document.documentElement.dataset.theme || '') !== {json.dumps(before)}",
                   "the theme toggle did not change the theme")
        label = self.page.evaluate("document.getElementById('themeBtn').getAttribute('aria-label')")
        now = self.page.evaluate("document.documentElement.dataset.theme")
        self.assertNotIn(now, label.lower(), "the theme toggle should name the theme it switches to")
        self.page.evaluate("document.getElementById('themeBtn').click()")
        self.until(f"(document.documentElement.dataset.theme || '') === {json.dumps(before)}",
                   "the theme toggle did not switch back")

    def test_13_tools_page_runs_a_tool(self):
        self.go(self.httpd.demo["app"])
        self.until("!document.getElementById('subjectPage').hidden", "the subject page did not open")
        self.go("~tools/binary_overview")
        self.until("(() => { const b = document.querySelector('#toolsPage button[aria-label=\"Run Binary Overview\"]');"
                   " return b && b.getClientRects().length > 0; })()", "#~tools/binary_overview did not open the tool")
        self.page.evaluate("document.querySelector('#toolsPage button[aria-label=\"Run Binary Overview\"]').click()")
        self.until("(() => { const s = document.querySelector('#toolsPage .tl-run .tl-run-status');"
                   " return s && !s.classList.contains('running'); })()", "the run never finished", timeout=40)
        status = self.page.evaluate("document.querySelector('#toolsPage .tl-run .tl-run-status').textContent")
        self.assertEqual(status.strip(), "Done", "running Binary Overview on the stand-in did not succeed")
        self.until("document.querySelector('#toolsPage .tl-run .tl-run-body').textContent.trim().length > 0",
                   "the run's result is empty")
        when = self.page.evaluate("document.querySelector('#toolsPage .tl-run time').textContent")
        self.assertRegex(when.strip(), r"^\d\d:\d\d$", "a run's time is not the studio's 24-hour clock")
        self.assert_view_is_sound("Tools page after a run")

    def test_14_asking_claude_gets_an_answer(self):
        self.go(self.httpd.demo["app"])
        self.until("!document.getElementById('subjectPage').hidden",
                   "the subject page with the Ask panel did not open")
        count = self.page.evaluate("document.querySelectorAll('#messages > *').length")
        self.page.evaluate("const t = document.getElementById('askText'); t.focus(); t.value = 'What is this?';"
                           "t.dispatchEvent(new Event('input', {bubbles: true}));"
                           "document.getElementById('sendBtn').click()")
        self.until(f"document.querySelectorAll('#messages > *').length >= {count + 2} && "
                   "/Done\\./.test(document.getElementById('messages').lastElementChild.textContent)",
                   "Claude's answer did not appear", timeout=60)
        self.assert_view_is_sound("subject page after an answer")

    def test_15_narrow_window_has_no_sideways_scroll(self):
        self.page.call("Emulation.setDeviceMetricsOverride",
                       {"width": 400, "height": 800, "deviceScaleFactor": 1, "mobile": False})
        try:
            for view in (self.httpd.demo["app"], "~tools", "~settings"):
                self.go(view)
                self.page.pump(1.0)
                wide = self.page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                self.assertLessEqual(wide, 1, f"{view}: the page scrolls sideways at 400px")
        finally:
            self.page.call("Emulation.clearDeviceMetricsOverride")

    # ---- keyboard focus, ranking and errors (each of these once lost focus or misled) ----

    def enter(self):
        self.page.key("Enter", "Enter", 13, text="\r")

    def active(self):
        return self.page.evaluate("(() => { const a = document.activeElement; return a ? "
                                  "(a.getAttribute('aria-label') || a.id || a.tagName) : ''; })()")

    def test_16_tools_search_opens_the_best_match(self):
        self.go("~tools")
        self.until("!document.getElementById('toolsPage').hidden && document.getElementById('tlSearch')",
                   "the Tools page did not open")
        self.page.evaluate("{ const s = document.getElementById('tlSearch'); s.focus(); s.value = 'decompile all';"
                           "s.dispatchEvent(new Event('input', {bubbles: true})); }")
        first = self.page.evaluate("[...document.querySelectorAll('#tlList .tl-toggle')]"
                                   ".find((b) => b.offsetParent !== null).dataset.name")
        self.assertEqual(first, "decompile_all", "the exact title match is not listed first")
        self.enter()
        self.until("location.hash === '#~tools/decompile_all'", "Enter in the search did not open Decompile All")
        self.clear_search()

    def clear_search(self):
        self.page.evaluate("{ const s = document.getElementById('tlSearch'); s.value = '';"
                           "s.dispatchEvent(new Event('input', {bubbles: true})); }")

    def test_17_cancelling_a_run_keeps_focus_in_the_form(self):
        self.go(self.httpd.demo["app"])
        self.until("!document.getElementById('subjectPage').hidden", "the subject page did not open")
        self.go("~tools/decompile_all")
        run = "document.querySelector('#toolsPage button[aria-label=\"Run Decompile All\"]')"
        cancel = "document.querySelector('#toolsPage button[aria-label=\"Cancel Decompile All\"]')"
        self.until(f"{run} && {run}.getClientRects().length > 0 && "
                   "document.querySelector('#tool-decompile_all-body input').value", "the tool did not open prefilled")
        self.page.evaluate(f"{run}.focus()")
        self.enter()
        self.until(f"document.activeElement === {cancel}", "Run did not hand focus to Cancel")
        self.page.pump(0.5)
        self.enter()
        self.page.pump(0.1)
        self.assertIn(self.active(), ("Cancel Decompile All", "Run Decompile All"), "pressing Cancel dropped focus")
        self.until("(() => { const s = document.querySelector('#tool-decompile_all .tl-run .tl-run-status');"
                   " return s && !s.classList.contains('running'); })()", "the cancelled run never ended", timeout=30)
        self.until(f"document.activeElement === {run}", "focus did not come back to Run after the cancel")

    def test_18_unstarring_in_favourites_keeps_focus(self):
        self.go("~tools")
        self.until("!document.getElementById('toolsPage').hidden", "the Tools page did not open")
        self.clear_search()
        for title in ("Binary Overview", "List Strings"):
            self.page.evaluate(f"document.querySelector('#toolsPage button[aria-label=\"Favourite: {title}\"]').click()")
        self.page.evaluate("document.querySelector('#toolsPage .tl-cat[data-view=\"fav\"]').click()")
        self.until("[...document.querySelectorAll('#tlList .tl-tool')].filter((e) => !e.hidden).length === 2",
                   "Favourites does not show the two starred tools")
        self.page.evaluate("document.querySelector('#toolsPage button[aria-label=\"Favourite: Binary Overview\"]').focus()")
        self.enter()
        self.page.pump(0.3)
        self.assertEqual(self.active(), "Favourite: List Strings", "un-starring dropped focus")
        self.enter()
        self.page.pump(0.3)
        self.assertNotIn(self.active(), ("BODY", "title", ""), "un-starring the last favourite dropped focus")
        self.page.evaluate("document.querySelector('#toolsPage .tl-cat[data-view=\"all\"]').click()")

    def test_19_declining_a_state_changing_tool_runs_nothing(self):
        self.go("~tools/export_evidence_bundle")
        run = "document.querySelector('#toolsPage button[aria-label=\"Run Export Evidence Bundle\"]')"
        self.until(f"{run} && {run}.getClientRects().length > 0 && "
                   "document.querySelector('#tool-export_evidence_bundle-body input').value",
                   "the tool did not open prefilled")
        self.page.evaluate("RE.store.set('tools.recent', [])")
        self.page.evaluate(f"{run}.click()")
        self.until(OPEN_DIALOG, "a state-changing tool ran without asking")
        message = self.page.evaluate(f"{OPEN_DIALOG}.textContent")
        self.assertEqual(message.count("change state"), 1, "the question says the same thing twice")
        self.assertEqual(self.page.evaluate("document.querySelectorAll('#tool-export_evidence_bundle .tl-run').length"), 0,
                         "the run showed as running behind the question")
        self.escape()
        self.until(f"!{OPEN_DIALOG}", "Escape did not close the question")
        self.page.pump(0.3)
        self.assertEqual(self.page.evaluate("document.querySelectorAll('#tool-export_evidence_bundle .tl-run').length"), 0)
        self.assertNotIn("export_evidence_bundle", self.page.evaluate("RE.store.get('tools.recent', [])"),
                         "a tool that never ran went into Recent")

    def test_20_new_subject_dialog_leaves_no_dead_history(self):
        self.go("~tools")
        self.until("!document.getElementById('toolsPage').hidden", "the Tools page did not open")
        self.go(self.httpd.demo["app"])
        self.until("!document.getElementById('subjectPage').hidden", "the subject page did not open")
        for _ in range(2):
            self.go("~new")
            self.until(OPEN_DIALOG, "#~new opened no dialog")
            self.escape()
            self.until(f"!{OPEN_DIALOG} && location.hash === {json.dumps('#' + self.httpd.demo['app'])}",
                       "closing the dialog did not come back to the subject")
        self.page.evaluate("history.back()")
        self.until("location.hash === '#~tools'", "Back was spent on a copy of the subject page")

    def test_21_a_server_error_in_new_subject_marks_the_path(self):
        self.go("~new")
        self.until(OPEN_DIALOG, "#~new opened no dialog")
        self.page.evaluate("{ const p = document.getElementById('ns-path'); p.focus();"
                           f"p.value = {json.dumps(str(self.tmp / 'nothing-here.exe'))};"
                           "p.dispatchEvent(new Event('input', {bubbles: true}));"
                           f"{OPEN_DIALOG}.querySelector('button[type=submit]').click(); }}")
        self.until("document.getElementById('ns-path').getAttribute('aria-invalid') === 'true'",
                   "the server's path error was not put on the path field")
        described = self.page.evaluate("document.getElementById('ns-path').getAttribute('aria-describedby')")
        self.assertIn("ns-path-err", described)
        self.assertIn("no file", self.page.evaluate("document.getElementById('ns-path-err').textContent"))
        self.assertTrue(self.page.evaluate(f"{OPEN_DIALOG}.querySelector('.form-error').hidden"))
        self.escape()
        self.until(f"!{OPEN_DIALOG}", "Escape did not close the dialog")

    def test_22_a_backend_path_error_marks_its_field(self):
        self.go("~settings")
        self.until("document.querySelector('#settingsPage [data-path]')", "Settings shows no backend path")
        field = self.page.evaluate("document.querySelector('#settingsPage [data-path]').id")
        self.page.evaluate(f"{{ const p = document.getElementById({json.dumps(field)});"
                           f"p.value = {json.dumps(str(self.tmp / 'no-such-backend'))};"
                           "[...document.querySelectorAll('#settingsPage button')]"
                           ".find((b) => b.textContent.trim() === 'Save backends').click(); }")
        self.until(f"document.getElementById({json.dumps(field)}).getAttribute('aria-invalid') === 'true' && "
                   f"document.activeElement === document.getElementById({json.dumps(field)})",
                   "the backend's path error was not put on its field")
        self.page.evaluate(f"{{ const p = document.getElementById({json.dumps(field)}); p.value = '';"
                           "p.dispatchEvent(new Event('input', {bubbles: true})); }")

    def test_23_settings_and_the_shell_share_one_checklist(self):
        self.go("~settings")
        self.until("document.querySelectorAll('#settingsPage .setup-row').length > 0", "Settings shows no checklist")
        rows = self.page.evaluate("document.querySelectorAll('#settingsPage .setup-row').length")
        items = self.page.evaluate("RE.setupItems(RE.state.checks, RE.state.setup, RE.state.catalog).length")
        self.assertEqual(rows, items)
        self.assertIn("notice", self.page.evaluate("RE.setupItems(RE.state.checks, RE.state.setup).map((i) => i.id)"))

    def test_24_analysis_settings_are_named_and_set_on_enter(self):
        self.go(self.httpd.demo["app"])
        self.until("document.querySelector('#pn-analysis .pn-toggle')", "the subject has no Analysis settings panel")
        self.page.evaluate("{ const t = document.querySelector('#pn-analysis .pn-toggle');"
                           "if (t.getAttribute('aria-expanded') !== 'true') t.click(); }")
        self.until("document.querySelector('#pn-analysis input[data-act=keep]')", "no analysis setting was listed",
                   timeout=30)
        names = self.page.evaluate("[...document.querySelectorAll('#pn-analysis .pn-setting')].map((li) => "
                                   "[li.dataset.key, li.querySelector('label.pn-check').textContent])")
        for key, label in names:
            self.assertIn(key, label, "a Keep checkbox does not say which setting it keeps")
        self.page.evaluate("{ const i = document.querySelector('#pn-analysis input[data-act=value]'); i.focus(); i.value = '120'; }")
        self.enter()
        self.until("/ set\\./.test(document.getElementById('live').textContent) || "
                   "!!document.querySelector('#pn-analysis .field-error:not([hidden])')",
                   "Enter in an analysis setting did nothing")

    def test_25_a_dropped_file_goes_with_the_question(self):
        self.go(self.httpd.demo["app"])
        self.until("!document.getElementById('subjectPage').hidden && !document.getElementById('attachBtn').hidden",
                   "the Ask panel has no paperclip")
        self.until("!(RE.state.job && RE.state.job.status === 'running')", "an earlier run did not end", timeout=60)
        count = self.page.evaluate("document.querySelectorAll('#messages > *').length")
        # Dropped on the Ask panel, as from Explorer: a chip in the composer.
        self.page.evaluate("{ const dt = new DataTransfer();"
                           "dt.items.add(new File(['started\\nfailed at 0x401000\\n'], 'run.log', { type: 'text/plain' }));"
                           "document.getElementById('assist').dispatchEvent(new DragEvent('drop', "
                           "{ dataTransfer: dt, bubbles: true, cancelable: true })); }")
        self.until("document.querySelectorAll('#askFiles .att-chip').length === 1 && "
                   "!!document.querySelector('#askFiles .att-remove[aria-label=\"Remove run.log\"]')",
                   "the dropped file has no chip in the composer")
        self.page.evaluate("{ const box = document.getElementById('askText'); box.focus(); box.value = 'Where did it fail?';"
                           "box.dispatchEvent(new Event('input', {bubbles: true}));"
                           "document.getElementById('sendBtn').click(); }")
        self.until(f"document.querySelectorAll('#messages > *').length >= {count + 2} && "
                   "/Done\\./.test(document.getElementById('messages').lastElementChild.textContent)",
                   "Claude's answer did not appear", timeout=60)
        self.assertEqual(self.page.evaluate("document.querySelectorAll('#askFiles .att-chip').length"), 0,
                         "the sent file stayed in the composer")
        # On the sent question: a chip that downloads the file, served back sandboxed.
        sent = self.page.evaluate("(async () => { const a = [...document.querySelectorAll('#messages .msg.user "
                                  ".msg-files a.att-chip')].pop(); if (!a) return null; const r = await fetch(a.href);"
                                  "return [a.getAttribute('download'), a.getAttribute('aria-label'), r.status,"
                                  "r.headers.get('content-disposition'), r.headers.get('content-security-policy'),"
                                  "await r.text()]; })()")
        self.assertIsNotNone(sent, "the sent question shows no chip for its file")
        download, label, status, disposition, policy, text = sent
        self.assertEqual((download, status, text), ("run.log", 200, "started\nfailed at 0x401000\n"))
        self.assertIn("Download run.log", label)
        self.assertTrue(disposition.startswith("attachment;") and policy.startswith("sandbox"), (disposition, policy))
        self.assert_view_is_sound("subject page after sending a file")

    def test_99_no_errors_along_the_way(self):
        self.assert_no_page_errors("while using the page")


if __name__ == "__main__":
    unittest.main()
