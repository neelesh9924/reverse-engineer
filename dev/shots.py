"""Take the README's screenshots of the studio on stand-ins (dev/demo_server.py).

Headless Chrome or Edge with a throwaway profile is driven over DevTools (dev/cdp.py). Nothing
real runs: the demo uses the stand-in engine, Claude and browser. Pictures go to docs/img/
(or the folder given).

Before every capture, the page's text, field values, placeholders and tooltips are scrubbed of
this PC's details: the Windows user name, the home folder, this project's folder and the demo's
temporary folder are swapped for neutral stand-ins (C:\\Users\\you, C:\\src\\reverse-engineer,
C:\\demo). A capture is refused while any of them is still on the page.

Run from the project root:  py -3 dev/shots.py [out-folder]
"""
import getpass
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _path in (ROOT, ROOT / "dev"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import cdp  # noqa: E402  (after the path tweak)
import demo_server  # noqa: E402

NEUTRAL_HOME = "C:\\Users\\you"
NEUTRAL_USER = "you"


def _variants(path):
    """The spellings a path can take on the page: as is, resolved, with forward slashes, and
    JSON-escaped (doubled backslashes) as it appears inside a raw result."""
    found = set()
    for base in {str(path), os.path.realpath(str(path))}:
        base = base.rstrip("\\/")
        if len(base) < 4:
            continue
        found.update({base, base.replace("\\", "/"), base.replace("\\", "\\\\")})
    return found


def user_names():
    names = set()
    for get in (getpass.getuser, os.getlogin, lambda: Path.home().name, lambda: os.environ.get("USERNAME", "")):
        try:
            name = get()
        except Exception:
            continue
        if name and len(name) >= 2:
            names.add(name)
    return sorted(names)


def replacements(demo_tmp):
    """(private text, neutral text) pairs, longest first so a folder inside another is matched
    before it. Each private path is also given as a forward-slash and a JSON-escaped spelling."""
    pairs = []
    for path, neutral in ((demo_tmp, "C:\\demo"), (ROOT, "C:\\src\\reverse-engineer"),
                          (ROOT.parent, "C:\\src"), (Path.home(), NEUTRAL_HOME),
                          (Path(tempfile.gettempdir()), NEUTRAL_HOME + "\\AppData\\Local\\Temp")):
        for spelling in _variants(path):
            if "/" in spelling:
                target = neutral.replace("\\", "/")
            elif "\\\\" in spelling:
                target = neutral.replace("\\", "\\\\")
            else:
                target = neutral
            pairs.append((spelling, target))
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    return pairs


# Installed in the page before its own scripts run, and called again before every capture. A
# MutationObserver keeps re-rendered text scrubbed too; it changes only what it must, so it
# never loops.
SCRUB_JS = r"""
(() => {
  const CONFIG = __CONFIG__;
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // The user name wherever it stands as a word of its own (in a path, an email, a sentence).
  const word = (name, flags) => new RegExp("(?<![A-Za-z0-9_])" + esc(name) + "(?![A-Za-z0-9_])", flags);
  const rules = CONFIG.pairs.map(([from, to]) => [new RegExp(esc(from), "gi"), to]);
  for (const name of CONFIG.users) rules.push([word(name, "gi"), CONFIG.neutralUser]);
  const fix = (text) => {
    if (!text) return text;
    let out = text;
    for (const [re, to] of rules) out = out.replace(re, () => to);
    return out;
  };
  const ATTRS = ["value", "placeholder", "title", "aria-label", "href", "data-path"];
  const fixNode = (node) => {
    if (node.nodeType === 3) {
      const next = fix(node.nodeValue);
      if (next !== node.nodeValue) node.nodeValue = next;
      return;
    }
    if (node.nodeType !== 1) return;
    for (const attr of ATTRS) {
      if (!node.hasAttribute(attr)) continue;
      const was = node.getAttribute(attr), next = fix(was);
      if (next !== was) node.setAttribute(attr, next);
    }
    if ((node.tagName === "INPUT" || node.tagName === "TEXTAREA") && node.value) {
      const next = fix(node.value);
      if (next !== node.value) node.value = next;
    }
  };
  const walk = (root) => {
    fixNode(root);
    const it = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    for (let n = it.nextNode(); n; n = it.nextNode()) fixNode(n);
  };
  window.__scrub = () => { if (document.documentElement) walk(document.documentElement); };
  window.__leaks = () => {
    const seen = [];
    const texts = [document.body ? document.body.innerText : ""];
    for (const el of document.querySelectorAll("input, textarea")) texts.push(el.value || "", el.placeholder || "");
    for (const el of document.querySelectorAll("[title]")) texts.push(el.title);
    const all = texts.join("\n").toLowerCase();
    for (const needle of CONFIG.needles) if (all.includes(needle.toLowerCase())) seen.push(needle);
    for (const name of CONFIG.users) if (word(name, "i").test(all)) seen.push("the user name");
    return seen;
  };
  if (!window.__scrubObserver) {
    window.__scrubObserver = new MutationObserver((records) => {
      for (const r of records) {
        if (r.type === "characterData" || r.type === "attributes") fixNode(r.target);
        else for (const n of r.addedNodes) walk(n);
      }
    });
    window.__scrubObserver.observe(document, { subtree: true, childList: true, characterData: true,
      attributes: true, attributeFilter: ATTRS });
  }
  window.__scrub();
})();
"""


def scrub_script(demo_tmp):
    pairs = replacements(demo_tmp)
    needles = sorted({spelling for spelling, _ in pairs}, key=len, reverse=True)
    config = {"pairs": pairs, "users": user_names(), "neutralUser": NEUTRAL_USER, "needles": needles}
    return SCRUB_JS.replace("__CONFIG__", json.dumps(config))


def shot(page, path, attempts=3):
    """Scrub, check nothing private is left, capture, and check again (a re-render between the
    check and the capture would otherwise slip through). Refuses rather than write a leak."""
    for _ in range(attempts):
        page.evaluate("window.__scrub && window.__scrub()")
        page.pump(0.3)
        leaks = page.evaluate("window.__leaks ? window.__leaks() : ['the scrubber is not installed']")
        if leaks:
            continue
        data = page.screenshot()
        if page.evaluate("window.__leaks ? window.__leaks() : ['the scrubber is not installed']"):
            continue
        path.write_bytes(data)
        print(f"  wrote {path}")
        return
    raise SystemExit(f"Refused to write {path.name}: the page still shows private details ({len(leaks or [])} kinds).")


def wait(page, condition, timeout=15, after=0.8):
    if not page.wait(condition, timeout=timeout, after=after):
        print(f"  (gave up waiting for: {condition})")


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "img"
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="reverse-engineer-shots-"))
    httpd = demo_server.build(tmp)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    from app import config

    chrome = config.browser_path()
    if not chrome:
        demo_server.stop(httpd)
        raise SystemExit("Chrome or Edge is needed for screenshots.")
    try:
        with cdp.Browser(chrome) as browser:
            page = browser.page()
            page.call("Page.enable")
            page.call("Runtime.enable")
            script = scrub_script(tmp)
            page.call("Page.addScriptToEvaluateOnNewDocument", {"source": script})
            page.navigate(f"{base}/auth?token={demo_server.TOKEN}")
            wait(page, "document.querySelectorAll('#list .nav-item, #list li').length >= 3")
            page.evaluate(script)   # in case the page loaded before the script was registered
            page.evaluate(f"location.hash = {json.dumps(httpd.demo['app'])}")
            wait(page, "document.querySelector('#subjectPage:not([hidden]) .ev')")
            page.evaluate("window.studioTheme.set('light')")   # headless reports a dark system theme
            wait(page, "document.documentElement.dataset.theme === 'light'")
            shot(page, out / "subject-light.png")

            page.evaluate("window.studioTheme.set('dark')")
            wait(page, "document.documentElement.dataset.theme === 'dark'")
            shot(page, out / "subject-dark.png")
            page.evaluate("window.studioTheme.set('light')")
            wait(page, "document.documentElement.dataset.theme === 'light'")

            # The findings list, with the newest finding open.
            page.evaluate("(() => { const ev = document.querySelector('#subjectPage .ev');"
                          " if (!ev) return; ev.open = true; ev.scrollIntoView({ block: 'start' }); })()")
            wait(page, "document.querySelector('#subjectPage .ev[open]')")
            shot(page, out / "findings.png")

            page.evaluate("location.hash = '~tools'")
            wait(page, "document.querySelectorAll('#toolsPage .tl-tool').length > 0")
            shot(page, out / "tools.png")
            page.evaluate("location.hash = '~tools/open_binary'")
            wait(page, "document.querySelector('#toolsPage .tl-tool.open form')")
            shot(page, out / "tool-form.png")

            page.evaluate("location.hash = '~settings'")
            wait(page, "document.querySelectorAll('#settingsPage .backend').length > 0", timeout=25)
            shot(page, out / "settings.png")

            page.evaluate("location.hash = '~new'")
            wait(page, "document.querySelector('dialog[open]')")
            shot(page, out / "new-subject.png")
    finally:
        demo_server.stop(httpd)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
