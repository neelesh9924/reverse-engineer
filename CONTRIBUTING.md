# Contributing

Thanks for helping. Issues and pull requests are welcome. Some rules keep the studio small,
honest and safe.

## Ground rules

- **Standard library only, no build step.** The server is plain Python 3.11+ with no
  dependencies; the page is plain HTML, CSS and JavaScript in `static/`, with no framework, no
  npm and nothing loaded from the internet (the Content-Security-Policy forbids it).
- **Stay within what REA does.** The studio is a front end for REA. New features must use what
  REA already offers and stay within its
  [disclaimer](https://github.com/morluto/rea/blob/main/DISCLAIMER.md) and
  [RESPONSIBLE_USE.md](RESPONSIBLE_USE.md). Nothing for circumventing protections or reaching
  other people's accounts or data.
- **Keep the network boundary.** REA runs locally; the only network the studio itself causes is
  a website subject's browser loading that site. Do not add other network access. Claude gets no
  shell, no file writes and no web tools.
- **Never vendor or fork REA here.** The studio drives the user's own REA checkout.
- **Honest copy.** UI text must say what really happens: in particular, asking Claude sends the
  question, the tool results Claude reads and the files it opens to Anthropic.
- **`/mcp` fails closed.** A check on Claude's REA calls that cannot be made (no catalogue, an
  unknown tool, a path it cannot place) refuses the call; never let one through instead.
- **Accessibility is part of done.** Every control has an accessible name, fields have labels,
  errors are announced, focus is never lost, dialogs trap and return focus.
- **Remove dead code** rather than keeping fallbacks for it.

## Running the tests

From the repository root:

```powershell
py -3 -m unittest discover -s tests -t tests
```

(or `python -m unittest discover -s tests -t tests` with any Python 3.11+). The tests never run
Node, REA or Claude: `tests/fake_rea.py` stands in for REA's MCP server, `tests/fake_claude.py`
for Claude Code, and `tests/fake_cdp.py` for a browser's DevTools. Everything else is the real
code, so keep it that way: no test-only branches in `app/` or `static/`.

`tests/test_ui.py` loads the page in headless Chrome or Edge over the demo server. It is skipped
when no browser is found, or when `REVERSE_ENGINEER_SKIP_UI=1`.

A check is only evidence once it has failed on purpose: break the code, see the test fail, put
it back.

## Trying the UI and taking screenshots

```powershell
py -3 dev/demo_server.py      # the studio on stand-ins with sample subjects; prints a link
py -3 dev/shots.py            # regenerates docs/img/*.png from the demo
```

`dev/shots.py` swaps your user name, home folder and project folder for neutral text before
every capture, and refuses to write a picture while any of them is still on the page. Never
commit screenshots made from your own subjects.

## Pull requests

- One change per pull request, with a short description of what and why.
- Run the tests first. Add a test for a bug fix or a new behaviour.
- Files are UTF-8 with LF line endings (`.gitattributes` enforces it).
- By contributing, you agree that your contribution is licensed under the MIT License.
