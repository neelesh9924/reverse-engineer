// The Settings page. Loaded before app.js, which calls studioSettings.render with its own
// helpers each time the page is opened, and studioSettings.refresh with the checks each time it
// polls the studio. refresh updates the page in place and never rebuilds a control.
window.studioSettings = (() => {
  "use strict";

  let ui = null;      // { h, icon, get, post, put, checks, setTheme }
  let saved = {};
  let boxes = null;
  let shown = "";
  let backendRows = null;   // id -> { dot, found, chip } for status-only updates
  let setupRows = null;     // Map of id -> the row RE.drawSetup keeps
  const RE = () => window.RE || {};
  const announce = (text, how) => { if (RE().announce) RE().announce(text, how); };

  // ---------- small pieces ----------
  function setText(el, text) { const t = String(text ?? ""); if (el.textContent !== t) el.textContent = t; }
  // A field: <label for>, the hint and any error tied to the control by aria-describedby.
  function field(label, input, hint) {
    const id = input.id || `set-in-${input.name || Math.random().toString(36).slice(2)}`;
    input.id = id;
    const hintEl = hint ? (hint instanceof Node ? hint : ui.h("span", { class: "field-hint" }, hint)) : null;
    if (hintEl && !hintEl.id) hintEl.id = `${id}-hint`;
    const err = ui.h("span", { class: "field-error", id: `${id}-err`, hidden: true });
    const describe = () => {
      const ids = [err.hidden ? null : err.id, hintEl ? hintEl.id : null].filter(Boolean).join(" ");
      if (ids) input.setAttribute("aria-describedby", ids); else input.removeAttribute("aria-describedby");
    };
    input._fieldError = (text) => {
      err.textContent = text || ""; err.hidden = !text;
      if (text) input.setAttribute("aria-invalid", "true"); else input.removeAttribute("aria-invalid");
      describe();
    };
    describe();
    return ui.h("div", { class: "field" }, ui.h("label", { class: "field-label", for: id }, label), input, hintEl, err);
  }
  function text(name, attrs) {
    return ui.h("input", { class: "input", name, id: `set-in-${name}`, value: saved[name] ?? "", autocomplete: "off", spellcheck: "false", ...attrs });
  }
  function values(form) {
    const out = {};
    for (const el of form.querySelectorAll("input[name]")) out[el.name] = el.value.trim();
    return out;
  }
  function note(kind, ...lines) {
    const mark = kind === "ok" ? "check" : kind === "soft" ? "refresh" : kind === "info" ? "info" : "alert";
    return ui.h("div", { class: `result ${kind === "info" ? "soft" : kind}` }, ui.icon(mark, 16),
      ui.h("div", null, lines.flat().filter(Boolean).map((l) => (l instanceof Node ? l : ui.h("div", null, l)))));
  }
  function section(id, iconName, title, lede, body) {
    const titleId = `set-${id}-title`;
    return ui.h("section", { class: "panel", id: `set-${id}`, "aria-labelledby": titleId },
      ui.h("header", { class: "panel-head" }, ui.h("span", { class: "panel-mark", "aria-hidden": "true" }, ui.icon(iconName, 17)),
        ui.h("div", null, ui.h("h2", { id: titleId }, title), lede ? ui.h("p", null, lede) : null)), body);
  }
  // Busy state through aria-disabled, so the pressed button keeps its focus.
  async function act(button, work) {
    if (button.getAttribute("aria-disabled") === "true") return;
    const label = button.textContent;
    button.setAttribute("aria-disabled", "true");
    button.textContent = "Working…";
    try { await work(); } finally { button.removeAttribute("aria-disabled"); button.textContent = label; }
  }
  // Which field a problem from the server is about, by the words it uses.
  const PROBLEM_FIELDS = [["model", /\bmodel\b/i], ["claude_bin", /Claude Code/], ["node_bin", /\bNode\b/], ["rea_dir", /REA folder|rea-agents|checkout/i]];
  function markProblems(form, problems) {
    const inputs = [...form.querySelectorAll("input[name]")];
    for (const input of inputs) if (input._fieldError) input._fieldError("");
    let first = null;
    for (const problem of problems) {
      const hit = PROBLEM_FIELDS.find(([, re]) => re.test(problem));
      const input = hit ? inputs.find((i) => i.name === hit[0]) : inputs.length === 1 ? inputs[0] : null;
      if (input && input._fieldError) { input._fieldError(problem); first = first || input; }
    }
    return first;
  }
  async function save(form, out) {
    const reply = await ui.post("/api/settings", { values: values(form) });
    if (reply.problems || reply.error) {
      const problems = reply.problems || [reply.error];
      ui.put(out, note("bad", problems));
      const first = markProblems(form, problems);
      announce(problems.join(" "), "assertive");
      if (first) first.focus();
      return false;
    }
    markProblems(form, []);
    saved = reply.settings;
    return true;
  }
  function saveButton(form, out, done) {
    const keep = ui.h("button", { class: "btn primary", type: "submit" }, "Save");
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      act(keep, async () => { if (await save(form, out)) { done(); announce("Saved."); } });
    });
    return keep;
  }
  function checkButton(label, title) {
    const b = ui.h("button", { class: "btn", type: "button", title }, label);
    b.addEventListener("click", () => act(b, async () => { shown = ""; await ui.post("/api/check", {}); announce("Checking again."); }));
    return b;
  }
  function copyBlock(lines, label) {
    const textValue = lines.join("\n");
    const copy = RE().copyButton ? RE().copyButton(textValue, label) : null;
    return ui.h("div", { class: "set-code" }, ui.h("pre", { class: "code", tabindex: "0", "aria-label": label.replace(/^Copy /, "") }, ui.h("code", null, textValue)), copy);
  }
  function goTo(id) {
    const el = document.getElementById(`set-${id}`);
    if (!el) return;
    el.scrollIntoView({ block: "start" });
    const heading = el.querySelector("h2") || el;
    heading.setAttribute("tabindex", "-1");
    heading.focus({ preventScroll: true });
  }

  // ---------- before you start (the setup checklist) ----------
  function setupSection() {
    boxes.setup = ui.h("ul", { class: "setup-list set-setup" });
    setupRows = new Map();
    return section("setup", "check", "Before you start", "What the studio needs, checked on this PC. Each line says what is missing and where to fix it.",
      ui.h("div", { class: "form" }, boxes.setup));
  }
  // The checklist is RE.setupItems, the same one the empty page draws; Settings passes its own
  // freshly saved notice_seen and REA version.
  function drawSetup(checks) {
    if (!boxes || !boxes.setup) return;
    const st = RE().state || {};
    const setup = { ...(st.setup || {}), notice_seen: !!saved.notice_seen };
    const catalog = { ...(st.catalog || {}), version: (st.catalog && st.catalog.version) || saved.rea_version || "" };
    RE().drawSetup(boxes.setup, setupRows, RE().setupItems(checks, setup, catalog), goTo);
  }

  // ---------- responsible use ----------
  function noticeSection() {
    boxes.notice = ui.h("div", { class: "results" });
    const disclaimer = `${saved.rea_dir_used || "..\\rea"}\\DISCLAIMER.md`;
    const body = ui.h("div", { class: "form set-notice" },
      ui.h("p", null, "Reverse Engineer and REA are for lawful analysis: security research, malware and incident analysis, vulnerability research and coordinated disclosure, CTFs and education, interoperability, software preservation, and debugging or auditing software you own or are authorised to examine."),
      ui.h("p", null, "Neither helps with circumventing DRM, licence checks, anti-cheat or other protection measures; game cheats or tampering with online services; bypassing authentication or access controls on systems you are not authorised to test; or copying proprietary software or its protected code and assets."),
      ui.h("p", null, "Permission to inspect a file does not by itself cover running it, observing a live website or changing a connected device. A website subject loads that site in a browser the studio controls; observing a site you don’t run needs permission from whoever does."),
      ui.h("p", null, ui.h("b", null, "What leaves this PC. "), "REA runs here. Ask Claude uses Claude Code with your sign-in: your questions, the conversation and the tool results Claude reads are sent to Anthropic, under your account and its data settings. Claude can also open files in the subject’s work folder and in the folder that holds the target (only the target itself when that folder is Downloads, Desktop, Documents or your home folder), and what it opens is sent too: keep a target in a folder of its own. Tools marked “may reach outside” can talk to a connected device, a browser or a program they start. A website subject loads the site over the network."),
      ui.h("p", null, "Decompiled code, heuristics and Claude’s answers can be incomplete or wrong. Check the evidence before relying on a conclusion."),
      ui.h("p", { class: "field-hint" }, "REA’s own terms are in its checkout: ", ui.h("code", null, disclaimer)),
      boxes.notice);
    drawNotice();
    return section("notice", "info", "Responsible use", "What the studio is for, and what it sends where.", body);
  }
  function drawNotice() {
    if (!boxes || !boxes.notice) return;
    if (saved.notice_seen) { ui.put(boxes.notice, note("ok", "You marked this as read.")); return; }
    const read = ui.h("button", { class: "btn primary", type: "button" }, "I have read this");
    read.addEventListener("click", () => act(read, async () => {
      const reply = await ui.post("/api/settings", { values: { notice_seen: true } });
      if (reply.problems || reply.error) {
        const msg = (reply.problems || [reply.error]).join(" ");
        ui.put(boxes.notice, note("bad", msg), read);
        announce(msg, "assertive");
        return;
      }
      saved = reply.settings;
      drawNotice();
      drawSetup(ui.checks());
      const heading = document.getElementById("set-notice-title");
      if (heading) { heading.setAttribute("tabindex", "-1"); heading.focus({ preventScroll: true }); }
      announce("Marked as read.");
    }));
    ui.put(boxes.notice, read);
  }

  // ---------- REA engine ----------
  function reaSection() {
    boxes.rea = ui.h("div", { class: "results" });
    const out = ui.h("div", { class: "results" });
    const dir = saved.rea_dir_used || "..\\rea";
    const form = ui.h("form", { class: "form", novalidate: true }, boxes.rea,
      ui.h("div", { class: "fields" },
        field("REA checkout folder", text("rea_dir", { class: "input mono", placeholder: dir }),
          "The cloned github.com/morluto/rea. Empty uses the sibling “rea” folder.")),
      ui.h("div", { class: "set-build" },
        ui.h("p", { class: "field-hint" }, `If REA is cloned but not built, run these once in a terminal, with Node ${saved.node_requirement || "24"}. On Windows, REA also needs its prebuilt native file; its checkout’s native\\windows\\README.md says how to restore it.`),
        copyBlock([`cd "${dir}"`, "npm ci", "npm run build:cached"], "Copy the build commands")),
      out);
    const keep = saveButton(form, out, () => ui.put(out, note("ok", "Saved.")));
    form.append(ui.h("div", { class: "form-foot" }, ui.h("span", { class: "grow" }), checkButton("Check", "Reads REA’s state again. Runs no analysis."), keep));
    return section("rea", "beaker", "Engine (REA)", "Reverse Engineer drives the rea-agents engine on this PC. Only website tools load the site they observe.", form);
  }

  // ---------- Claude ----------
  function claudeSection() {
    boxes.claude = ui.h("div", { class: "results" });
    const out = ui.h("div", { class: "results" });
    const form = ui.h("form", { class: "form", novalidate: true }, boxes.claude,
      ui.h("div", { class: "fields" },
        field("Model", text("model", { placeholder: "Account default", list: "re-models" }), "Empty uses the account’s default. “sonnet” uses less of the limit."),
        field("Path to Claude Code", text("claude_bin", { class: "input mono", placeholder: "Found by itself" }), foundHint("claude"))),
      ui.h("datalist", { id: "re-models" }, ["sonnet", "opus", "haiku"].map((m) => ui.h("option", { value: m }))),
      out);
    const look = checkButton("Check the sign-in", "Asks Claude Code who is signed in. Sends Claude no question.");
    const keep = saveButton(form, out, () => ui.put(out, note("ok", "Saved.")));
    form.append(ui.h("div", { class: "form-foot" }, ui.h("span", { class: "grow" }), look, keep));
    return section("claude", "sparkle", "Claude",
      "Ask Claude runs Claude Code on this PC with the sign-in you use in a terminal. Your questions, the conversation, the tool results Claude reads and the files it opens (in the subject’s work folder and the target’s folder) are sent to Anthropic.", form);
  }

  // ---------- what Claude may run ----------
  function permissionsSection() {
    const box = ui.h("input", { type: "checkbox", id: "set-claude-destructive" });
    box.checked = !!saved.claude_destructive;
    const warnId = "set-claude-destructive-warning";
    box.setAttribute("aria-describedby", warnId);
    const status = ui.h("div", { class: "results" });
    const drawStatus = () => ui.put(status, saved.claude_destructive
      ? note("bad", "On: Claude may run REA’s state-changing tools without asking you.")
      : note("ok", "Off: Claude is offered only tools that read and analyse."));
    drawStatus();
    box.addEventListener("change", async () => {
      const want = box.checked;
      if (want && RE().confirm) {
        const ok = await RE().confirm({ title: "Let Claude run state-changing tools?", confirmLabel: "Let Claude run them", danger: true,
          message: "Claude could then run code from the target, use debugger consoles, load Frida scripts, change a connected device or write files, without asking you first. Text inside a target can try to steer Claude. Turn this on only for targets you trust, and only while you need it." });
        if (!ok) { box.checked = false; box.focus(); return; }
      }
      box.setAttribute("aria-disabled", "true");
      const reply = await ui.post("/api/settings", { values: { claude_destructive: want } });
      box.removeAttribute("aria-disabled");
      if (reply.problems || reply.error) {
        box.checked = !want;
        const msg = (reply.problems || [reply.error]).join(" ");
        ui.put(status, note("bad", `Not saved: ${msg}`));
        announce(`Not saved: ${msg}`, "assertive");
        return;
      }
      saved = reply.settings;
      box.checked = !!saved.claude_destructive;
      drawStatus();
      box.focus();
      announce(saved.claude_destructive ? "Claude may now run state-changing tools, from its next question." : "Claude is offered only tools that read and analyse, from its next question.");
    });
    const body = ui.h("div", { class: "form" },
      ui.h("div", { class: "set-switch" }, box,
        ui.h("label", { for: box.id }, ui.h("b", null, "Let Claude run state-changing REA tools"))),
      ui.h("div", { class: "notice set-warning", id: warnId }, ui.icon("alert", 16),
        ui.h("div", null,
          ui.h("p", null, ui.h("b", null, "Off (recommended): "), "Claude can use REA’s tools that read and analyse, but not the ones REA marks as changing state: running code from the target, debugger and Rizin consoles, Frida scripts, changing a connected device, or writing files. You can still run those yourself from the Tools page, which asks you first."),
          ui.h("p", null, ui.h("b", null, "On: "), "Claude may run them without asking you. Strings, page text and logs inside a target can try to steer Claude, so turn this on only for targets you trust. It applies from Claude’s next question."))),
      status);
    return section("permissions", "lock", "What Claude may run", "Whether Claude, when you ask it something, may use REA’s tools that change state.", body);
  }

  // ---------- Node ----------
  function nodeSection() {
    boxes.node = ui.h("div", { class: "results" });
    const out = ui.h("div", { class: "results" });
    const form = ui.h("form", { class: "form", novalidate: true }, boxes.node,
      ui.h("div", { class: "fields" },
        field("Path to Node", text("node_bin", { class: "input mono", placeholder: saved.node_used || "Found by itself" }), foundHint("node"))),
      out);
    const keep = saveButton(form, out, () => ui.put(out, note("ok", "Saved.")));
    form.append(ui.h("div", { class: "form-foot" }, ui.h("span", { class: "grow" }), keep));
    return section("node", "chip", "Node",
      `REA needs Node ${saved.node_requirement}. Empty finds Node 24 through fnm, then a plain node on PATH.`, form);
  }

  function foundHint(name) {
    boxes[`${name}Found`] = ui.h("span", { class: "field-hint found", "data-found": name }, "Only if it is not found by itself.");
    return boxes[`${name}Found`];
  }
  function showFound(name, check, setting) {
    const box = boxes[`${name}Found`];
    if (!box) return;
    let t;
    if (saved[setting]) t = check && check.path ? `In use: ${check.path}` : "As named here.";
    else if (check && check.path) t = `Found by itself: ${check.path}${check.how ? ` (${check.how})` : ""}`;
    else t = check ? "Not found by itself: give its path." : "Only if it is not found by itself.";
    setText(box, t);
  }

  // ---------- backends ----------
  const statusOf = (b) => (b.in_use ? "in use" : b.found ? "found" : "absent");
  const kindOf = (b) => (b.in_use ? "ok" : b.found ? "warn" : "");
  function backendsSection() {
    boxes.backends = ui.h("div", { class: "backends" }, note("soft", "Checking…"));
    boxes.backendsOut = ui.h("div", { class: "results" });
    const saveB = ui.h("button", { class: "btn primary", type: "button" }, "Save backends");
    saveB.addEventListener("click", () => act(saveB, saveBackends));
    const body = ui.h("div", { class: "form" }, boxes.backends, boxes.backendsOut,
      ui.h("div", { class: "form-foot" },
        ui.h("span", { class: "field-hint grow" }, "Turned-on backends are handed to REA the next time a subject’s engine starts. A tool already on PATH can’t be hidden from REA by turning it off here."),
        saveB));
    return section("backends", "tools", "Analysis backends",
      "REA works without these; each one you add unlocks its family of tools. Point an absent one at its path, or turn one off so the studio doesn’t hand it to REA.", body);
  }

  function drawBackends(list) {
    if (!boxes || !boxes.backends) return;
    if (!list) { if (!backendRows) ui.put(boxes.backends, note("soft", "Checking…")); return; }
    if (backendRows) {   // already built: update status only, keep what was typed
      for (const b of list) {
        const row = backendRows[b.id];
        if (!row) continue;
        row.dot.className = `dot ${kindOf(b)}`;
        row.chip.className = `chip ${kindOf(b)}`;
        setText(row.chip, statusOf(b));
        setText(row.found, b.found_path ? `Found: ${b.found_path}` : `Not found. ${b.unlocks || ""}`.trim());
      }
      return;
    }
    backendRows = {};
    ui.put(boxes.backends, list.map((b) => {
      const id = String(b.id).replace(/[^a-z0-9_-]/gi, "-");
      const nameId = `bk-${id}`;
      const foundId = `bk-${id}-found`;
      const dot = ui.h("i", { class: `dot ${kindOf(b)}`, "aria-hidden": "true" });
      const chip = ui.h("span", { class: `chip ${kindOf(b)}` }, statusOf(b));
      const found = ui.h("small", { id: foundId }, b.found_path ? `Found: ${b.found_path}` : `Not found. ${b.unlocks || ""}`.trim());
      backendRows[b.id] = { dot, found, chip };
      const controls = [];
      if (b.configurable) {
        const active = ui.h("input", { type: "checkbox", id: `bk-${id}-use`, "data-active": b.id, "aria-describedby": foundId });
        if (b.active) active.checked = true;
        const what = b.kind === "dir" ? "folder" : "program";
        const path = ui.h("input", { class: "input mono", id: `bk-${id}-path`, "data-path": b.id, value: b.path || "",
          placeholder: `Only if it is not found by itself`, autocomplete: "off", spellcheck: "false", "aria-describedby": foundId });
        const err = ui.h("span", { class: "field-error", id: `bk-${id}-path-err`, hidden: true });
        backendRows[b.id].path = path;
        backendRows[b.id].label = b.label;
        backendRows[b.id].error = (message) => {
          err.textContent = message || ""; err.hidden = !message;
          if (message) path.setAttribute("aria-invalid", "true"); else path.removeAttribute("aria-invalid");
          path.setAttribute("aria-describedby", message ? `${err.id} ${foundId}` : foundId);
        };
        path.addEventListener("input", () => backendRows[b.id].error(""));
        controls.push(
          ui.h("div", { class: "set-switch" }, active, ui.h("label", { for: active.id }, `Use ${b.label}`)),
          ui.h("div", { class: "field set-bk-path" }, ui.h("label", { class: "field-label", for: path.id }, `${b.label} ${what} path`), path, err));
      } else {
        controls.push(ui.h("span", { class: "field-hint" }, "Detected by itself; nothing to set."));
      }
      return ui.h("div", { class: "backend", role: "group", "aria-labelledby": nameId }, dot,
        ui.h("div", { class: "b-text" }, ui.h("b", { id: nameId }, b.label), found), chip,
        ui.h("div", { class: "b-controls" }, controls));
    }));
  }

  async function saveBackends() {
    const map = {};
    for (const cb of boxes.backends.querySelectorAll("[data-active]")) {
      const id = cb.getAttribute("data-active");
      const path = boxes.backends.querySelector(`[data-path="${CSS.escape(id)}"]`);
      map[id] = { active: cb.checked, path: path ? path.value.trim() : "" };
    }
    const reply = await ui.post("/api/settings", { values: { backends: map } });
    const rows = Object.values(backendRows || {}).filter((row) => row.error);
    for (const row of rows) row.error("");
    if (reply.problems || reply.error) {
      const problems = reply.problems || [reply.error];
      ui.put(boxes.backendsOut, note("bad", problems));
      // The server names the backend first ("Ghidra: that is not a folder…"): mark that field.
      let first = null;
      for (const problem of problems) {
        const row = rows.find((r) => String(problem).startsWith(`${r.label}:`));
        if (row) { row.error(problem); first = first || row.path; }
      }
      announce(problems.join(" "), "assertive");
      if (first) first.focus();
      return;
    }
    saved = reply.settings;
    shown = "";   // let the next refresh redraw the status
    ui.put(boxes.backendsOut, note("ok", "Saved. Turned-on backends apply the next time a subject’s engine starts."));
    announce("Backends saved.");
    await ui.post("/api/check", {});
  }

  // ---------- analysis settings kept for every engine start ----------
  function analysisSection() {
    boxes.analysis = ui.h("div", { class: "form" });
    drawAnalysis();
    return section("analysis", "settings", "Analysis limits kept",
      "REA forgets its analysis settings when it exits. Settings you keep from a subject’s Analysis settings panel are set again after every engine start.", boxes.analysis);
  }
  function drawAnalysis(focusAfter) {
    if (!boxes || !boxes.analysis) return;
    const kept = saved.analysis_settings && typeof saved.analysis_settings === "object" ? saved.analysis_settings : {};
    const names = Object.keys(kept).sort();
    if (!names.length) {
      ui.put(boxes.analysis, ui.h("p", { class: "field-hint" }, "None kept. Open a subject, start its engine, and use its Analysis settings panel to change REA’s limits and keep them."));
      if (focusAfter) { const t = document.getElementById("set-analysis-title"); if (t) { t.setAttribute("tabindex", "-1"); t.focus({ preventScroll: true }); } }
      return;
    }
    const out = ui.h("div", { class: "results" });
    const list = ui.h("ul", { class: "set-kept" }, names.map((name) => {
      const value = kept[name];
      const drop = ui.h("button", { class: "btn small", type: "button", "data-name": name, "aria-label": `Stop keeping ${name}` }, "Stop keeping");
      drop.addEventListener("click", () => act(drop, async () => {
        const next = { ...kept };
        delete next[name];
        const reply = await ui.post("/api/settings", { values: { analysis_settings: next } });
        if (reply.problems || reply.error) {
          const msg = (reply.problems || [reply.error]).join(" ");
          ui.put(out, note("bad", msg));
          announce(msg, "assertive");
          return;
        }
        saved = reply.settings;
        announce(`${name} is no longer kept. REA keeps its current value until the engine restarts.`);
        const at = names.indexOf(name);
        drawAnalysis(true);
        const buttons = boxes.analysis.querySelectorAll("button[data-name]");
        const target = buttons[Math.min(at, buttons.length - 1)];
        if (target) target.focus();
      }));
      return ui.h("li", null, ui.h("code", null, name), ui.h("span", null, " = ", ui.h("b", null, value === null ? "REA’s default" : String(value))), ui.h("span", { class: "grow" }), drop);
    }));
    ui.put(boxes.analysis, list, out);
  }

  // ---------- the checks ----------
  function refresh(checks) {
    if (!boxes || !checks) return;
    drawSetup(checks);
    const now = JSON.stringify(checks);
    if (now === shown) return;
    shown = now;
    const waiting = note("soft", "Checking…");
    const rea = checks.rea;
    const version = (rea && rea.version) || saved.rea_version || "";
    ui.put(boxes.rea, checks.checking ? note("soft", "Checking again…") : null, !rea ? waiting : rea.ok
      ? note("ok", `REA${version ? " " + version : ""} is built: ${rea.tools} tools, ${rea.prompts} workflows.`, `Checkout: ${rea.dir}`)
      : note("bad", rea.error, rea.dir ? `Looked for the checkout at: ${rea.dir}` : null),
      rea && rea.warning ? note("warn", rea.warning) : null);
    const claude = checks.claude;
    ui.put(boxes.claude, !claude ? waiting : claude.ok
      ? note("ok", `Claude Code ${(claude.version || "").replace(/\s*\(Claude Code\)/, "")}, signed in as ${claude.account || "your account"}${claude.plan ? ` (${claude.plan})` : ""}.`)
      : note("bad", claude.error));
    const node = checks.node;
    ui.put(boxes.node, !node ? waiting : node.ok
      ? note("ok", `Node ${node.version}${node.how ? ` (${node.how})` : ""}.`)
      : note("bad", node.error, node.how ? `Tried: ${node.how}.` : null));
    showFound("claude", claude, "claude_bin");
    showFound("node", node, "node_bin");
    drawBackends(checks.backends);
  }

  async function render(helpers, container, extra) {
    ui = helpers; boxes = {}; shown = ""; backendRows = null; setupRows = null;
    ui.put(container, ui.h("p", { class: "lede" }, "Loading…"));
    const info = await ui.get("/api/settings");
    if (!info || info.error || !info.settings) {
      ui.put(container, note("bad", (info && info.error) || "The settings could not be read.", "Reload the window to try again."));
      return;
    }
    saved = info.settings;
    ui.put(container, ui.h("div", { class: "settings" },
      setupSection(), noticeSection(), reaSection(), claudeSection(), permissionsSection(), nodeSection(), backendsSection(),
      analysisSection(), extra || null,
      ui.h("p", { class: "field-hint" }, "Settings are kept in data/settings.json beside the app. Subjects, their conversations, findings and work files are in data/ too, on this PC.")));
    refresh(info.checks || ui.checks());
    drawSetup(info.checks || ui.checks());
  }

  return { render, refresh };
})();
