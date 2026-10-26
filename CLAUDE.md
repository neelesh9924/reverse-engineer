# Reverse Engineer: notes for Claude

A local desktop studio that drives [REA](https://github.com/morluto/rea) (`rea-agents`) with
Claude, on the user's own Windows PC. Read README.md first: it says what the studio does, what
leaves the PC, and how the pieces fit. CONTRIBUTING.md has the rules for changes; SECURITY.md
has the security model.

## Standing rules

- **Keep it small.** Python standard library only, no build step; the page is plain HTML, CSS
  and JavaScript with no framework and nothing loaded from outside `127.0.0.1`. Remove dead code
  rather than keeping fallbacks.
- **Local, except a website subject.** REA runs locally and the studio opens no connection of its
  own. The one exception is a website subject (`subjects.is_website`): the studio opens that URL
  in a browser it controls (`app/webtarget.py`) so REA's web tools have a `cdp_endpoint`. Keep
  this boundary; don't add network reach elsewhere.
- **Be honest about Claude.** Claude is Anthropic's model: the user's question, the conversation,
  every tool result Claude reads and every file it opens go to Anthropic. Never claim in code,
  docs or UI text that no data leaves the PC.
- **Claude gets no shell.** Its runs get REA's tools through `/mcp` plus Read, Glob and Grep in the
  subject's work folder and the folder that holds the target (`claude.readable_places`: only the
  target itself when that folder is a general one, never a folder subject's parent). `Bash`,
  `Write`, `Edit`, `WebFetch` and `WebSearch` are denied in `app/claude.py`.
- **`/mcp` fails closed.** `Studio.claude_policy` refuses any call it cannot check against REA's
  catalogue and the running engine's tool list; state-changing tools (`destructiveHint`) and
  `claude.NEEDS_PERMISSION` unless the user turned on `claude_destructive`; paths outside the
  target, its folder and the work folder (outputs: the work folder only; `claude_path_problem`);
  and a local inspector or Electron endpoint the user has not used. Browser tools
  (`webtarget.is_browser_tool`) are pinned to the subject's browser.
- **Within REA's limits.** Improve how the studio uses what REA already does. Never add
  capabilities beyond REA's own, nor anything for circumventing protections or reaching other
  people's accounts or data (RESPONSIBLE_USE.md, REA's DISCLAIMER.md).
- **The user's targets.** The studio only analyses a target the user pointed it at. The target
  is referenced where it lives, never copied or modified.
- **REA is the engine, not ours.** Never vendor or fork REA here. The studio drives the user's
  separate checkout (default `../rea`, or the path in Settings), built there once.
- **One engine, one session.** The studio owns one `rea mcp` stdio subprocess at a time and is its
  only MCP client (`app/rea.py`). It serialises tool calls, so Claude (via `/mcp`) and the Tools
  page share the same opened target. Forwarded JSON-RPC ids are rewritten into the studio's own
  id space. REA's own `rea mcp --http` takes one client at a time; don't rely on it.
- **Backends are the user's to manage.** `app/backends.py` is the one source of truth: how each
  backend is detected and the environment variable that hands it to REA. A tool already on PATH
  can't be hidden from REA by a toggle; the UI says so.
- **Claude uses its default login.** `app/claude.py` passes no `CLAUDE_CONFIG_DIR` and scrubs
  `CLAUDE*`, `MCP_*` and the Anthropic API-key variables from the run's environment. No
  CLAUDE.md is read and nothing is remembered between runs.
- **Accessibility is part of done.** Accessible names, `<label for>`, hints through
  `aria-describedby`, errors through `aria-invalid`, focus never lost on re-render, dialogs that
  trap and return focus.

## How it fits together

- `launch.py`, `Launch Reverse Engineer.vbs`: the venv, the app window (brought to the front), one
  running studio (starting it again opens another window on it), stop when the window is gone.
- `app/server.py`: the API, the page and `/mcp`. Host check, per-launch cookie, Origin + JSON on
  every change, CSP on every reply, `/mcp` on the running Claude request's own bearer token.
  `parts_of` checks a path as text before the filesystem is touched.
- `app/rea.py`: `ReaEngine` (one subprocess and an MCP stdio client: handshake, `forward`,
  `request`, `call`, `cancel`, `close`) and `Engines` (the active subject's engine, swapped when
  another subject is opened, never away from a busy one). `command=` lets tests inject a stand-in.
- `app/mcp.py`: forwards one JSON-RPC message from Claude to the engine and records tool results.
- `app/claude.py`: `ClaudeRunner` (`prefix=` lets tests inject a stand-in), the run command and its
  permissions, the system prompt (studio preamble + REA's instructions), stream-json parsing.
- `app/assistant.py`: one Claude job at a time, the conversation, and turning tool results into
  findings (`entry_for`). `app/evidence.py`: findings on disk, per subject.
- `app/catalog.py`: reads REA's built `dist/mcp-catalog.json` and groups the tools (`_RULES`).
- `app/subjects.py`, `app/settings.py`, `app/checks.py`, `app/config.py`, `app/webtarget.py`,
  `app/winjob.py`: subjects on disk, Settings, readiness checks, configuration, a website
  subject's browser, Windows job objects.
- `static/`: `index.html`, `app.js` (the shell), `settings.js`, `theme.js`, and `js/` modules that
  share `window.RE` (`core.js`, `markdown.js`, `evidence.js`, `forms.js`, `tools.js`,
  `panels.js`, `palette.js`), styles in `app.css` and `css/`.
- `dev/`: `demo_server.py` (the studio on stand-ins with sample data), `shots.py` (README
  screenshots, scrubbed of the user's name and folders), `cdp.py` (a small DevTools client),
  `make_icon.py`.

## Working here (Windows)

- Node for REA: `config.node_command()` uses a `node_bin` from Settings, else fnm's Node 24 if fnm
  is installed and has it (so the machine's default Node is left alone), else `node` on PATH.
- Tests: `py -3 -m unittest discover -s tests -t tests`. They never start real Node, REA or
  Claude: `tests/fake_rea.py` is an MCP-over-stdio stand-in, `tests/fake_claude.py` drives the
  studio's `/mcp` as Claude would, `tests/fake_cdp.py` stands in for DevTools. Everything else is
  the real code; keep it so (no test-only branches in `app/` or `static/`).
  `tests/test_ui.py` needs Chrome or Edge and is skipped without one.
- **Never run REA from here** while developing or testing: use the stand-ins. The studio runs
  REA only when the user launches it and opens a subject.
- **A check isn't evidence until it has failed on purpose:** break the code, see the test fail,
  put it back.
- Write files with the Write/Edit tools, not shell heredocs (they mangle backslashes). Files are
  UTF-8 with LF line endings.
- Screenshots: only from `dev/shots.py` over the demo, never from real subjects.
