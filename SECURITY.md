# Security policy

## Reporting a vulnerability

Please report security problems privately, through **Report a vulnerability** in this
repository's **Security** tab (GitHub private vulnerability reporting). Do not open a public
issue for them. If that button is missing, open an issue that only asks for a private way to
send a report, with no details of the problem, and wait for a reply.

Say which version (commit) you used, your Windows version, what an attacker could do, and how
to reproduce it. Leave out private binaries, captured traffic, credentials, session tokens and
paths or names from your own PC.

A problem in REA itself (its tools, its providers, its native code) belongs to the REA project:
see its [security policy](https://github.com/morluto/rea/blob/main/SECURITY.md). A problem in
Claude Code belongs to Anthropic.

## Supported versions

Only the latest commit on the default branch is supported.

## Security model

The studio is a local web server and a page in an app window. What it defends against is
**other web pages and programs on the same PC talking to it**, and **content from the analysed
target steering Claude**.

- **Loopback only.** The server listens on `127.0.0.1`, on a random free port unless you set one.
  The `Host` header must be exactly `127.0.0.1:<port>`, which blocks DNS-rebinding.
- **A per-launch session.** The launcher opens the window on a one-time link carrying a random
  token; `/auth` turns it into an `HttpOnly`, `SameSite=Strict` cookie. Every request except
  `/health`, `/auth` and the static files needs that cookie.
- **No cross-site changes.** Every request that changes something must come from the studio's
  own origin (`Origin` or `Referer`) and carry a JSON body.
- **Strict page policy.** Every reply carries a Content-Security-Policy that allows scripts,
  styles, images and connections from the studio's own origin only and forbids framing, plus
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and `X-Content-Type-Options: nosniff`.
  The page loads nothing from the internet.
- **Claude's bridge is per run, and checks every call.** Claude reaches REA through the
  studio's `/mcp`, which accepts only the bearer token of the Claude run in progress. Each tool
  call is checked against REA's tool catalogue and the running engine's own tool list; a call
  the studio cannot check (the catalogue cannot be read, say while REA is being rebuilt, or the
  tool is not in it) is refused. `/mcp` refuses REA tools that change state (MCP
  `destructiveHint`), and tools that start or attach to a program (Frida), unless you turned on
  "Let Claude run state-changing tools" in Settings.
- **Claude's REA calls are held to the subject.** Every path in a call must be a full local
  path: the target itself, the folder that holds it (as below), or the subject's work folder;
  anything REA writes must go in the work folder. Network shares and device paths are refused as
  text. Browser tools are pinned to the subject's own browser and origin, whatever arguments
  Claude sends; a V8-inspector or Electron endpoint must be one you already used for that
  subject from the Tools page.
- **Claude has no shell.** Its built-in tools are Read, Glob and Grep only, in the subject's work
  folder and the folder that holds the target. When that folder is a general one (the home
  folder, Downloads, Desktop, Documents and the like), Claude gets only the target file itself;
  a folder subject gives that folder, never its parent. Bash, Write, Edit, WebFetch and
  WebSearch are denied, and credential stores in your home folder (`.ssh`, `.aws`, `AppData`,
  `.git-credentials`, `.npmrc`, `.docker`, `.kube`, `.azure`, `.config`, and others) are denied
  by rule. Results from the target are marked as untrusted data in Claude's instructions, so
  text inside a binary or a web page is not treated as a request from you.
- **Paths are checked as text first.** A path that names a network share (`\\host\share`), a
  device (`\\.\`, `\\?\`) or a reserved device name is refused before Windows is asked about it,
  in the studio's own settings and subjects and in Claude's REA calls. A finding cannot name a
  file for the studio to read: the store's own references to long strings are kept apart from
  the data in a finding.
- **A website subject's browser is separate.** It runs on its own profile (never your everyday
  browser profile), with DevTools on a private loopback port and without
  `--remote-allow-origins`, so web pages cannot drive it.
- **Secrets stay out of REA.** REA's process does not inherit `ANTHROPIC_*`, `CLAUDE*` and
  similar API-key environment variables.

## What it does not protect against

- **It is not a sandbox.** REA and its analysis backends run as you, with your permissions.
  Opening a hostile file hands it to those tools, and some tools run or attach to programs.
  Analyse untrusted targets in a VM or on a separate machine.
- **Malware already running as you.** A program running under your account can read the
  studio's files, including the session file in `data/`, and act as you.
- **What you send to Anthropic.** Asking Claude sends your question, the tool results it reads
  and the files it opens to Anthropic. That is by design; see
  [RESPONSIBLE_USE.md](RESPONSIBLE_USE.md).
- **REA tools you run yourself.** The checks above are for Claude. From the Tools page you can
  run any REA tool with any arguments, as you could from REA's own command line.
