// The subject page: RE.panels.render(container, brief), called by app.js for the open subject on
// every state change. The first call builds the panels; later calls update them in place, so a
// finding's open state and keyboard focus survive every poll. Findings are fetched again only
// when the subject's evidence_rev changes. Calls the panels make for their own upkeep (session,
// unknowns, analysis settings) go through RE.runTool with record:false and start:false, so they
// never start an engine and never land in Findings.
(() => {
  "use strict";

  const RE = window.RE;
  const { h, icon, put } = RE;
  const enc = encodeURIComponent;
  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  const PAGE = 100;        // findings per fetch
  const KEPT = 500;        // the store keeps the newest 500 per subject (app/evidence.py MAX_KEPT)

  RE.addIcons({
    download: ["M12 4v11", "M7 10l5 5 5-5", "M5 20h14"],
    upload: ["M12 20V9", "M7 14l5-5 5 5", "M5 4h14"],
    question: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M9.6 9.3a2.5 2.5 0 1 1 3.6 2.3c-.8.4-1.2 1-1.2 1.8v.4", "M12 17v.3"],
    bug: ["M9 7a3 3 0 0 1 6 0", "M7 10h10v5a5 5 0 0 1-10 0z", "M12 10v10", "M4 13h3M17 13h3M5 8l2 2M19 8l-2 2M5 19l2-2M19 19l-2-2"],
    sliders: ["M5 4v16", "M12 4v16", "M19 4v16", "M3 9h4", "M10 15h4", "M17 7h4"],
    pulseLine: ["M3 12h4l3-7 4 14 3-7h4"],
  });

  // ---------- small DOM helpers ----------
  function setText(el, text) { const t = String(text ?? ""); if (el.textContent !== t) el.textContent = t; }
  function setAttr(el, name, value) {
    if (value === null || value === undefined || value === false) { if (el.hasAttribute(name)) el.removeAttribute(name); }
    else if (el.getAttribute(name) !== String(value)) el.setAttribute(name, value);
  }
  function show(el, on) { if (el.hidden === !!on) el.hidden = !on; }
  const plural = (n, word, many) => `${Number(n).toLocaleString()} ${n === 1 ? word : many || word + "s"}`;
  const shortId = (id) => {
    const s = String(id || "");
    const m = s.match(/^([a-z]+_)?([0-9a-f]{64})$/i);
    if (m) return `${m[1] || ""}${m[2].slice(0, 10)}…`;
    return s.length > 18 ? `${s.slice(0, 16)}…` : s;
  };
  const absolutePath = (p) => /^[a-zA-Z]:[\\/]/.test(p) || (p.startsWith("/") && !p.startsWith("//"));
  const networkPath = (p) => /^(\\\\|\/\/)/.test(p);

  // A button: icon + a label span (the label is what screen readers and the busy state change).
  function button(label, { iconName, cls = "btn small", title, aria, act, onclick, type = "button" } = {}) {
    const b = h("button", { class: cls, type, title: title || null, "aria-label": aria || null, "data-act": act || null },
      iconName ? icon(iconName, 14) : null, h("span", { class: "pn-label" }, label));
    b.dataset.title = title || "";
    if (onclick) b.addEventListener("click", (e) => {
      if (b.getAttribute("aria-disabled") === "true") {
        e.preventDefault();
        RE.announce(b.dataset.why || "Not now.", "assertive");
        return;
      }
      onclick(e);
    });
    return b;
  }
  function setLabel(b, label) { const span = b.querySelector(".pn-label"); if (span) setText(span, label); }
  // Turned off with aria-disabled (never `disabled`), so a focused button keeps its focus.
  function block(b, why) {
    setAttr(b, "aria-disabled", why ? "true" : null);
    if (why) b.dataset.why = why; else delete b.dataset.why;
    if (why) setAttr(b, "title", why);
    else if (b.dataset.title !== undefined) setAttr(b, "title", b.dataset.title || null);
  }
  async function busy(b, label, work) {
    if (b && b.getAttribute("aria-disabled") === "true") return undefined;
    const span = b ? b.querySelector(".pn-label") : null;
    const before = span ? span.textContent : "";
    if (b) { b.setAttribute("aria-disabled", "true"); b.classList.add("busy"); }
    if (span && label) span.textContent = label;
    try { return await work(); }
    finally {
      if (b) { b.removeAttribute("aria-disabled"); b.classList.remove("busy"); }
      if (span) span.textContent = before;
    }
  }
  function note(kind, ...lines) {
    const mark = kind === "ok" ? "check" : kind === "bad" ? "alert" : kind === "warn" ? "alert" : "info";
    return h("div", { class: `result ${kind}` }, icon(mark, 16),
      h("div", null, lines.flat().filter((l) => l !== null && l !== undefined && l !== "").map((l) => (l instanceof Node ? l : h("div", null, l)))));
  }
  function chip(text, kind = "", title = "") { return h("span", { class: `chip${kind ? " " + kind : ""}`, title: title || null }, text); }
  function timeEl(at) { return at ? h("time", { datetime: at, title: RE.fmt.full ? RE.fmt.full(at) : at }, RE.fmt.time(at)) : null; }

  // Rebuild `box` with `build()`, putting focus back on the same control when it was inside.
  function keepFocus(box, build, fallback) {
    const active = document.activeElement;
    const inside = !!active && box.contains(active);
    let selector = "";
    if (inside) {
      const act = active.closest("[data-act]");
      const row = active.closest("[data-key]");
      const parts = [];
      if (row && box.contains(row)) parts.push(`[data-key="${CSS.escape(row.dataset.key)}"]`);
      if (act) parts.push(`[data-act="${CSS.escape(act.dataset.act)}"]`);
      selector = parts.join(" ");
    }
    build();
    if (inside && !box.contains(active)) {
      const again = selector ? box.querySelector(selector) : null;
      const target = again || (typeof fallback === "function" ? fallback() : fallback);
      if (target) target.focus({ preventScroll: true });
    }
  }
  // Replace a message box's content, keeping focus when a button in it goes away.
  function fill(box, fallback, ...pieces) { keepFocus(box, () => put(box, ...pieces), fallback); }

  // A labelled field for the panels' own small forms: <label for>, hint and error by aria-describedby.
  let fieldCount = 0;
  function field(label, control, { hint = "", optional = false, wide = true } = {}) {
    const id = control.id || `pn-f${++fieldCount}`;
    control.id = id;
    const hintEl = h("span", { class: "field-hint", id: `${id}-hint`, hidden: !hint }, hint || "");
    const err = h("span", { class: "field-error", id: `${id}-err`, hidden: true });
    const describe = () => setAttr(control, "aria-describedby",
      [err.hidden ? null : err.id, hintEl.hidden ? null : hintEl.id].filter(Boolean).join(" ") || null);
    describe();
    const el = h("div", { class: `field${wide ? " wide" : ""}` },
      h("label", { class: "field-label", for: id }, label, optional ? h("span", { class: "opt" }, " (optional)") : null),
      control, hintEl, err);
    return {
      el, control,
      hint(text) { hintEl.textContent = text || ""; hintEl.hidden = !text; describe(); },
      error(text) { err.textContent = text || ""; err.hidden = !text; setAttr(control, "aria-invalid", text ? "true" : null); describe(); },
    };
  }

  // ---------- one panel: a section with a heading, optionally collapsible (remembered) ----------
  let panelCount = 0;
  function panel(key, { title, iconName, lede, collapsible = false, open = true, onOpen } = {}) {
    const n = ++panelCount;
    const id = `pn-${key}`;
    const titleId = `${id}-title-${n}`;
    const bodyId = `${id}-body-${n}`;
    let isOpen = collapsible ? RE.store.get(`panel.${key}`, open) !== false : true;
    let toggle = null;
    let heading;
    if (collapsible) {
      toggle = h("button", { class: "pn-toggle", type: "button", "aria-expanded": String(isOpen), "aria-controls": bodyId },
        icon("caret", 15), h("span", null, title));
      heading = h("h2", { id: titleId, class: "pn-title" }, toggle);
    } else heading = h("h2", { id: titleId, class: "pn-title", tabindex: "-1" }, title);
    const count = h("span", { class: "pn-count-badge", hidden: true });
    const ledeEl = lede ? h("p", { class: "pn-lede" }, lede) : null;
    const actions = h("div", { class: "pn-actions" });
    const body = h("div", { class: "pn-body", id: bodyId });
    const el = h("section", { class: "panel pn", id, "aria-labelledby": titleId },
      h("header", { class: "panel-head" },
        h("span", { class: "panel-mark", "aria-hidden": "true" }, icon(iconName, 17)),
        h("div", { class: "pn-heading" }, h("div", { class: "pn-title-row" }, heading, count), ledeEl),
        actions),
      body);
    const p = {
      el, body, actions, heading, count,
      get open() { return isOpen; },
      focusTarget: () => toggle || heading,
      setCount(text) { setText(count, text || ""); show(count, !!text); },
      apply() {
        el.classList.toggle("collapsed", !isOpen);
        show(body, isOpen);
        show(actions, isOpen && actions.childElementCount > 0);
        if (toggle) setAttr(toggle, "aria-expanded", String(isOpen));
      },
    };
    if (toggle) {
      toggle.addEventListener("click", () => {
        isOpen = !isOpen;
        RE.store.set(`panel.${key}`, isOpen);
        p.apply();
        if (isOpen && onOpen) onOpen();
      });
    }
    p.apply();
    return p;
  }

  // ---------- reading tool results defensively ----------
  // The useful value inside an MCP result: an Evidence envelope's normalized_result, or a
  // {"result": …} wrapper's inside, or the structured content as it is.
  function payloadOf(entry) {
    let v = entry ? entry.evidence : null;
    if ((v === null || v === undefined) && entry && typeof entry.text === "string") {
      try { v = JSON.parse(entry.text); } catch { v = entry.text; }
    }
    if (isObj(v) && "normalized_result" in v && ("evidence_id" in v || "operation" in v)) v = v.normalized_result;
    if (isObj(v) && "result" in v && Object.keys(v).filter((k) => k !== "_meta").length === 1) v = v.result;
    return v;
  }
  function envelopeOf(entry) {
    const ev = entry && entry.evidence;
    return isObj(ev) && ("normalized_result" in ev || "confidence" in ev || "limitations" in ev) ? ev : null;
  }

  async function call(v, name, args, { record = false, start = false, confirmed = false } = {}) {
    const reply = await RE.runTool(v.slug, name, args || {}, { record, start, confirmed, source: "panel" });
    if (!reply || reply.error) {
      return { ok: false, notRunning: !!(reply && reply.not_running), cancelled: !!(reply && reply.cancelled),
        message: (reply && reply.error) || "The studio did not answer.", reply };
    }
    const entry = reply.entry || RE.evidence.fromResult(name, args, reply.result);
    if (entry.error) return { ok: false, toolError: entry.error, message: entry.error.message || entry.error.code || "REA reported an error.", entry, reply };
    return { ok: true, value: payloadOf(entry), entry, reply };
  }
  function failNote(r, what) {
    if (r.notRunning) return note("soft", `The engine isn’t running, so REA can’t ${what}. Start it from Overview.`);
    if (r.toolError && RE.evidence && RE.evidence.errorView) {
      try { return RE.evidence.errorView(r.toolError, { slug: RE.state.slug }); } catch {}
    }
    return note("bad", r.message);
  }

  // ---------- shared state checks ----------
  const engineOf = (b) => (b && b.engine && b.engine.state) || "stopped";
  const running = (v) => engineOf(v.brief) === "running";
  const isWeb = (b) => !!b && b.kind === "website";
  function claudeHere(v) { const j = RE.state.job; return !!j && j.status === "running" && j.slug === v.slug; }
  function claudeAnywhere() { const j = RE.state.job; return !!j && j.status === "running"; }
  function runsHere(v) { return Object.values(RE.state.runs || {}).some((r) => r && r.slug === v.slug && r.status === "running"); }
  // Automatic upkeep waits while Claude or a long Tools-page call holds this subject's engine.
  const engineBusy = (v) => claudeHere(v) || runsHere(v);
  function askBlocked() {
    const st = RE.state.setup || {};
    const c = RE.state.checks || {};
    if (RE.state.offline) return "Not connected to the studio.";
    if (st.rea_ready === false) return "REA isn’t built yet, so Claude has no tools to use. See Settings.";
    if (st.claude_ready === false && c.claude && !c.checking) return "Claude Code isn’t signed in. See Settings.";
    if (claudeAnywhere()) return "Claude is still working. Wait for the answer, or press Stop.";
    return "";
  }

  // A REA workflow (MCP prompt): its arguments in a dialog, then an ask with the prompt attached.
  async function startWorkflow(v, prompt, prefill = {}) {
    if (RE.shell && typeof RE.shell.runWorkflow === "function") return RE.shell.runWorkflow(prompt, prefill);
    const why = askBlocked();
    if (why) { RE.toast(why, { kind: "info" }); return; }
    if (!RE.forms || !RE.forms.promptDialog) { RE.toast("Workflows could not load. Reload the window to try again.", { kind: "bad" }); return; }
    let picked = null;   // the dialog fills target_path from the subject itself
    try { picked = await RE.forms.promptDialog(prompt, { slug: v.slug, prefill: { ...prefill } }); } catch (err) { console.error(err); }
    if (!picked || v.disposed) return;
    const again = askBlocked();
    if (again) { RE.toast(again, { kind: "info" }); return; }
    const reply = await RE.post("/api/ask", { slug: v.slug, text: `Follow the REA workflow “${prompt.title || prompt.name}”.`, prompt: picked });
    if (reply.error) { RE.toast(reply.error, { kind: "bad" }); return; }
    RE.announce("Sent. Claude is working on it.");
    if (RE.shell && RE.shell.assistOpen && !RE.shell.assistOpen() && RE.shell.toggleAssist) RE.shell.toggleAssist();
    await RE.refresh();
  }

  // ======================================================================
  // Overview: the target, its engine (and browser), quick actions, REA workflows, at a glance
  // ======================================================================
  const KIND_LABEL = { executable: "Executable or library", archive: "Archive or app bundle", database: "Analysis database",
    artifact: "Other artifact", website: "Website" };
  const KIND_ICON = { executable: "chip", archive: "archive", database: "database", artifact: "file", website: "globe" };
  // Read-only first steps per kind; each shows only when REA lists the tool.
  const QUICK = {
    executable: [["open_binary", "Open in REA", "Opens the target so every tool can use it"],
      ["binary_overview", "Overview", "Segments, and how many procedures and strings"],
      ["list_strings", "Strings", "Every string REA finds"],
      ["list_procedures", "Procedures", "Every analysed procedure"]],
    archive: [["open_binary", "Open in REA", "Opens the bundle so every tool can use it"],
      ["inspect_artifact", "Inspect the package", "Entries, manifests and signatures, once it is open"],
      ["binary_overview", "Overview", "What REA bound to, with counts"]],
    database: [["open_binary", "Open in REA", "Opens the saved analysis"],
      ["binary_overview", "Overview", "Segments, and how many procedures and strings"],
      ["list_procedures", "Procedures", "Every analysed procedure"]],
    artifact: [["open_binary", "Open in REA", "Opens the file, when REA can read its format"],
      ["inspect_byte_entropy", "Byte entropy", "Where the file looks packed or compressed"],
      ["binary_overview", "Overview", "What REA bound to, with counts"]],
    website: [["inspect_web_page", "Inspect the page", "Read passively: no clicks, no scripts run"],
      ["capture_web_screenshot", "Screenshot", "The visible part of the page"],
      ["analyze_web_bundle", "Scripts", "Chunks, routes and endpoints from the page’s JavaScript"]],
  };
  const CONF = [["observed", "Observed", "ok"], ["derived", "Derived", "accent"], ["inferred", "Inferred", "warn"]];

  function overviewSection(v) {
    const p = panel("overview", { title: "Overview", iconName: KIND_ICON[v.brief.kind] || "target" });

    // the target
    const facts = h("dl", { class: "pn-facts" });
    let record = null, factsSig = "", recordSeq = 0;
    async function loadRecord() {
      const seq = ++recordSeq;
      const reply = await RE.api(`/api/subject?slug=${enc(v.slug)}`);
      if (seq !== recordSeq || v.disposed) return;
      record = reply && isObj(reply.subject) ? reply.subject : null;
      drawFacts();
    }
    function drawFacts() {
      const b = v.brief;
      const t = (record && record.target) || {};
      const rows = [];
      rows.push(["Kind", KIND_LABEL[b.kind] || b.kind || "—"]);
      if (isWeb(b)) rows.push(["Website", { code: b.url || t.url || "", copy: "Copy the address" }]);
      else {
        rows.push(["Target", { code: b.path || t.path || "", copy: "Copy the path" }]);
        rows.push(["On disk", b.exists === false ? { chip: ["Missing: the file is not at its path now", "bad"] }
          : b.hashing ? { chip: ["Reading the file…", "busy"] } : { chip: ["Present", "ok"] }]);
      }
      if (t.format) rows.push(["Format", t.format]);
      if (t.architecture) rows.push(["Architecture", t.architecture]);
      if (t.entry_point) rows.push(["Entry point", { code: t.entry_point }]);
      if (typeof t.size === "number") rows.push(["Size", `${RE.fmt.bytes(t.size)} (${t.size.toLocaleString()} bytes)`]);
      if (t.sha256) rows.push(["SHA-256", { code: t.sha256, copy: "Copy the SHA-256" }]);
      if (record && record.created) rows.push(["Added", { time: record.created }]);
      const sig = JSON.stringify(rows);
      if (sig === factsSig) return;
      factsSig = sig;
      keepFocus(facts, () => put(facts, rows.flatMap(([k, val]) => {
        let dd;
        if (isObj(val) && "code" in val) dd = h("dd", { class: "pn-code-row" }, h("code", null, val.code || "—"),
          val.copy && val.code ? (() => { const c = RE.copyButton(val.code, val.copy); c.dataset.act = `copy-${k}`; return c; })() : null);
        else if (isObj(val) && val.chip) dd = h("dd", null, chip(val.chip[0], val.chip[1]));
        else if (isObj(val) && val.time) dd = h("dd", null, timeEl(val.time));
        else dd = h("dd", null, String(val));
        return [h("dt", null, k), dd];
      })), () => p.focusTarget());
    }

    // engine and browser
    const engineChip = h("span", { class: "chip" });
    const engineText = h("span", { class: "pn-engine-text" });
    const engineErr = h("div", { class: "notice bad pn-engine-error", hidden: true });
    const engineBtn = button("Start engine", { iconName: "play", act: "engine" });
    const browserRow = h("div", { class: "pn-engine-row", hidden: true });
    const browserChip = h("span", { class: "chip" });
    const browserBtn = button("Close browser", { iconName: "power", act: "browser" });
    let starting = false;
    engineBtn.dataset.title = "";
    engineBtn.addEventListener("click", async () => {
      if (engineBtn.getAttribute("aria-disabled") === "true") { RE.announce(engineBtn.dataset.why || "Not now.", "assertive"); return; }
      if (engineBtn.dataset.action === "close") {
        if (RE.shell && RE.shell.closeEngine) await RE.shell.closeEngine(v.slug, "engine");
        else { const r = await RE.post("/api/subject/close", { slug: v.slug, what: "engine" }); if (r.error) RE.toast(r.error, { kind: "bad" }); await RE.refresh(); }
        return;
      }
      starting = true;
      drawEngine();
      try {
        if (RE.shell && RE.shell.openEngine) await RE.shell.openEngine(v.slug);
        else { const r = await RE.post("/api/subject/open", { slug: v.slug }); if (r.error) RE.toast(r.error, { kind: "bad" }); await RE.refresh(); }
      } finally { starting = false; if (!v.disposed) drawEngine(); }
    });
    browserBtn.addEventListener("click", async () => {
      if (browserBtn.getAttribute("aria-disabled") === "true") { RE.announce(browserBtn.dataset.why || "Not now.", "assertive"); return; }
      if (browserBtn.dataset.action === "open") { engineBtn.click(); return; }
      if (RE.shell && RE.shell.closeEngine) await RE.shell.closeEngine(v.slug, "browser");
      else { const r = await RE.post("/api/subject/close", { slug: v.slug, what: "browser" }); if (r.error) RE.toast(r.error, { kind: "bad" }); await RE.refresh(); }
    });
    const retry = button("Start again", { iconName: "refresh", act: "retry", onclick: () => engineBtn.click() });
    const errText = h("div");
    engineErr.append(icon("alert", 16), errText, retry);
    const ENGINE = { stopped: ["Stopped", "", "REA starts when you ask Claude something or run a tool."],
      starting: ["Starting…", "busy", "REA is starting for this subject."],
      running: ["Running", "ok", "REA is running for this subject. Findings are kept when it stops."],
      crashed: ["Stopped unexpectedly", "bad", ""] };
    const WEB = { stopped: ["Closed", ""], starting: ["Starting…", "busy"], running: ["Running", "ok"], crashed: ["Stopped unexpectedly", "bad"] };
    function drawEngine() {
      const b = v.brief;
      const state = starting && engineOf(b) !== "running" ? "starting" : engineOf(b);
      const [label, kind, text] = ENGINE[state] || ENGINE.stopped;
      setText(engineChip, `Engine: ${label}`);
      engineChip.className = `chip${kind ? " " + kind : ""}`;
      setText(engineText, text);
      const web = isWeb(b) ? ((b.web && b.web.state) || "stopped") : null;
      const err = state === "crashed" ? (b.engine && b.engine.error) || "REA exited without saying why."
        : web === "crashed" ? `The browser stopped: ${(b.web && b.web.error) || "no reason given."}` : "";
      setText(errText, err);
      show(engineErr, !!err);
      const live = state === "running";
      engineBtn.dataset.action = live ? "close" : "open";
      setLabel(engineBtn, live ? "Close engine" : state === "starting" ? "Starting…" : isWeb(b) ? "Start browser and engine" : "Start engine");
      const ic = engineBtn.querySelector("svg");
      const want = live ? "power" : state === "starting" ? "refresh" : "play";
      if (ic && engineBtn.dataset.icon !== want) { engineBtn.dataset.icon = want; ic.replaceWith(icon(want, 14)); }
      engineBtn.classList.toggle("busy", state === "starting");
      let why = "";
      if (state === "starting") why = "REA is starting.";
      else if (live && claudeHere(v)) why = "Claude is using the engine. Stop Claude first.";
      else if (live && runsHere(v)) why = "A tool is running here. Cancel it on the Tools page first.";
      else if (!live && claudeAnywhere() && !claudeHere(v)) why = "Claude is working on another subject. Wait for it, or press Stop.";
      block(engineBtn, why);
      if (!why) setAttr(engineBtn, "title", live ? "Stops REA for this subject. Findings are kept." : isWeb(b)
        ? "Opens the site in a browser the studio controls, then starts REA. Observing a site loads it over the network."
        : "Starts REA for this subject.");
      show(browserRow, !!web);
      if (web) {
        const [wl, wk] = WEB[web] || WEB.stopped;
        setText(browserChip, `Browser: ${wl}`);
        browserChip.className = `chip${wk ? " " + wk : ""}`;
        const wlive = web === "running";
        browserBtn.dataset.action = wlive ? "close" : "open";
        setLabel(browserBtn, wlive ? "Close browser" : "Start browser and engine");
        block(browserBtn, web === "starting" ? "The browser is starting." : "");
        show(browserBtn, wlive || state !== "running");
      }
    }
    browserRow.append(browserChip, h("span", { class: "grow" }), browserBtn);

    // quick actions
    const quickList = h("ul", { class: "pn-quick" });
    const quickBox = h("div", { class: "pn-sub", hidden: true }, h("h3", { class: "pn-h3" }, "Quick actions"), quickList);
    async function runQuick(name, label, b) {
      if (claudeAnywhere() && !claudeHere(v)) { RE.toast("Claude is working on another subject. Wait for it, or press Stop.", { kind: "info" }); return; }
      await busy(b, "Running…", async () => {
        const d = await RE.api(`/api/tool/defaults?slug=${enc(v.slug)}&name=${enc(name)}`);
        const args = d && isObj(d.arguments) ? { ...d.arguments } : {};
        const tool = v.tools.get(name);
        const required = (tool && tool.inputSchema && Array.isArray(tool.inputSchema.required)) ? tool.inputSchema.required : [];
        const missing = required.filter((k) => args[k] === undefined || args[k] === "");
        if (missing.length) {
          RE.toast(`${label} needs ${missing.join(", ")}. Fill it in on the Tools page.`, { kind: "info" });
          RE.go(`~tools/${enc(name)}`);
          return;
        }
        RE.announce(`Running ${label}…`);
        const reply = await RE.runTool(v.slug, name, args, { source: "panel" });
        if (v.disposed || (reply && reply.cancelled)) return;
        if (!reply || reply.error) { RE.toast(reply ? reply.error : "The studio did not answer.", { kind: "bad" }); return; }
        const entry = reply.entry;
        if (entry && entry.error) RE.toast(`${label}: ${entry.error.message || entry.error.code || "REA reported an error."}`, { kind: "bad" });
        else RE.announce(`${label} finished. The result is the newest finding.`);
        if (entry && entry.id) v.findings.revealWhenLoaded(entry.id);
        await RE.refresh();
        if (name === "open_binary" || name === "close_binary") v.session.reload(true);
      });
    }
    function drawQuick() {
      const list = (QUICK[v.brief.kind] || QUICK.executable).filter(([name]) => v.tools.has(name));
      put(quickList, list.map(([name, label, sub]) => {
        const b = h("button", { class: "pn-quick-btn", type: "button", "data-act": `quick-${name}`, title: `${name}: ${sub}` },
          h("span", { class: "pn-quick-title" }, icon(name === "open_binary" ? "play" : "beaker", 14), h("span", { class: "pn-label" }, label)),
          h("small", null, sub));
        b.addEventListener("click", () => runQuick(name, label, b));
        return h("li", null, b);
      }));
      show(quickBox, list.length > 0);
    }

    // REA workflows
    const flowList = h("ul", { class: "pn-quick pn-flows" });
    const flowBox = h("div", { class: "pn-sub", hidden: true },
      h("h3", { class: "pn-h3" }, "REA workflows"),
      h("p", { class: "pn-hint" }, "One of REA’s own step-by-step investigations. You fill in what it asks; Claude follows it on this subject."),
      flowList);
    function drawFlows() {
      const prompts = v.prompts || [];
      put(flowList, prompts.map((pr) => {
        const b = h("button", { class: "pn-quick-btn", type: "button", "data-act": `flow-${pr.name}` },
          h("span", { class: "pn-quick-title" }, icon("flow", 14), h("span", null, pr.title || pr.name)),
          pr.description ? h("small", null, pr.description) : null);
        b.addEventListener("click", () => startWorkflow(v, pr));
        return h("li", null, b);
      }));
      show(flowBox, prompts.length > 0);
    }

    // at a glance (from the findings loaded)
    const stats = h("ul", { class: "stats pn-stats", "aria-label": "Findings at a glance" });
    const statsNote = h("p", { class: "pn-hint" });
    const glance = h("div", { class: "pn-sub", hidden: true }, h("h3", { class: "pn-h3" }, "At a glance"), stats, statsNote);
    let statsSig = "";
    function drawStats(items, total) {
      if (!Array.isArray(items) || !items.length) { show(glance, false); statsSig = ""; return; }
      const count = { observed: 0, derived: 0, inferred: 0 };
      let limits = 0, failed = 0, untrusted = 0;
      const tools = new Map();
      for (const e of items) {
        const ev = envelopeOf(e);
        const c = ev && typeof ev.confidence === "string" ? ev.confidence : isObj(ev && ev.confidence) ? ev.confidence.level : null;
        if (c && c in count) count[c]++;
        if (ev && Array.isArray(ev.limitations)) limits += ev.limitations.length;
        if (e.error) failed++;
        if (e.untrusted) untrusted++;
        if (e.tool) tools.set(e.tool, (tools.get(e.tool) || 0) + 1);
      }
      const tiles = [[total, total === 1 ? "finding" : "findings", ""],
        ...CONF.map(([k, label, kind]) => [count[k], label.toLowerCase(), count[k] ? kind : ""]),
        [limits, limits === 1 ? "limitation" : "limitations", limits ? "warn" : ""],
        [failed, failed === 1 ? "failed run" : "failed runs", failed ? "bad" : ""],
        [tools.size, tools.size === 1 ? "tool used" : "tools used", ""]];
      const top = [...tools.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5).map(([t, n]) => `${t} (${n})`).join(", ");
      const newest = items[0] && items[0].at;
      const sig = JSON.stringify([tiles, top, newest, items.length, untrusted]);
      if (sig === statsSig) return;
      statsSig = sig;
      put(stats, tiles.map(([n, label, kind]) => h("li", { class: `stat${kind ? " " + kind : ""}` }, h("b", null, Number(n).toLocaleString()), h("span", null, label))));
      const parts = [];
      if (newest) parts.push(`Newest finding ${RE.fmt.time(newest)}.`);
      if (top) parts.push(`Most used: ${top}.`);
      if (untrusted) parts.push(`${plural(untrusted, "finding")} hold text from the target: read it as data, not instructions.`);
      if (items.length < total) parts.push(`Counted over the newest ${items.length.toLocaleString()} of ${total.toLocaleString()}.`);
      setText(statsNote, parts.join(" "));
      show(glance, true);
    }

    const engineBox = h("div", { class: "pn-sub" },
      h("h3", { class: "pn-h3" }, isWeb(v.brief) ? "Engine and browser" : "Engine"),
      h("div", { class: "pn-engine-row" }, engineChip, engineText, h("span", { class: "grow" }), engineBtn),
      browserRow, engineErr);
    p.body.append(h("div", { class: "pn-overview" },
      h("div", { class: "pn-sub" }, h("h3", { class: "pn-h3" }, isWeb(v.brief) ? "Website" : "Target"), facts),
      engineBox, glance, quickBox, flowBox));

    loadRecord();
    return {
      el: p.el, panel: p, drawStats,
      catalog() { drawQuick(); drawFlows(); },
      update(b, prev) {
        if (prev && (prev.hashing !== b.hashing || prev.exists !== b.exists)) loadRecord();   // size and SHA-256 are known now
        drawFacts();
        drawEngine();
      },
      state() { drawEngine(); },
    };
  }

  // ======================================================================
  // Session: what REA has open now (binary_session)
  // ======================================================================
  function sessionSection(v) {
    const p = panel("session", { title: "REA session", iconName: "pulseLine", collapsible: true, open: !isWeb(v.brief),
      lede: "What REA has open now: the target, the analysis provider, and which tools can run.",
      onOpen: () => reload(false) });
    const out = h("div", { class: "pn-session" });
    const msg = h("div", { class: "results" });
    const refresh = button("Check again", { iconName: "refresh", act: "session-refresh", title: "Asks REA for its session. Starts nothing.",
      onclick: () => busy(refresh, "Checking…", () => reload(true, true)) });
    const openBtn = button("Open target in REA", { iconName: "play", act: "session-open", onclick: () => openTarget() });
    const closeBtn = button("Close target", { iconName: "power", act: "session-close", onclick: () => closeTarget() });
    const provider = h("select", { class: "input pn-provider", "data-act": "provider" }, h("option", { value: "" }, "Choose the provider for me"));
    const providerF = field("Analysis provider", provider, { hint: "Which deep-analysis provider REA binds to. It stays bound until the target is closed.", wide: false });
    show(providerF.el, false);
    const controls = h("div", { class: "pn-row-actions" }, openBtn, closeBtn);
    if (isWeb(v.brief)) { show(openBtn, false); show(closeBtn, false); }
    p.actions.append(refresh);
    p.apply();
    p.body.append(msg, out, h("div", { class: "pn-form-row" }, isWeb(v.brief) ? null : providerF.el, controls));
    let loadedKey = null, seq = 0, value = null, stale = false;

    const back = () => p.focusTarget();
    let idleShown = false;
    async function reload(force = false, user = false) {
      if (v.disposed || !v.tools.has("binary_session")) return;
      if (!p.open && !user) { stale = true; return; }
      if (!running(v)) {
        value = null;
        loadedKey = null;
        v.sessionOpen = false;
        if (!idleShown) {
          idleShown = true;
          fill(out, back);
          fill(msg, back, note("soft", "The engine isn’t running. Start it from Overview, and REA reports what it has open here."));
        }
        drawButtons();
        return;
      }
      idleShown = false;
      if (!force && loadedKey === v.runKey) return;
      if (!user && engineBusy(v)) { stale = true; return; }
      stale = false;
      loadedKey = v.runKey;
      const my = ++seq;
      if (!value) fill(msg, back, note("soft", "Asking REA…"));
      const r = await call(v, "binary_session", {});
      if (my !== seq || v.disposed) return;
      if (!r.ok) { fill(msg, back, failNote(r, "report its session")); fill(out, back); value = null; drawButtons(); return; }
      fill(msg, back);
      value = r.value;
      draw();
      if (user) RE.announce("REA’s session is up to date.");
    }

    function providerName(pv) {
      if (!pv) return "";
      if (typeof pv === "string") return pv;
      return [pv.name || pv.id, pv.version].filter(Boolean).join(" ");
    }
    function draw() {
      const s = isObj(value) ? value : {};
      const rows = [];
      const target = isObj(s.target) ? s.target : s.open ? s : null;
      const open = s.open !== undefined ? !!s.open : !!target;
      rows.push(["Target", open ? chip(s.session_kind === "bundle_resources" ? "App resources open" : "Open", "ok") : chip("Nothing open", "")]);
      if (target) {
        if (target.path || s.resource_root) rows.push(["Path", h("code", null, target.path || (isObj(s.resource_root) ? JSON.stringify(s.resource_root) : s.resource_root))]);
        const kind = [target.kind, target.format, target.architecture].filter(Boolean).join(" · ");
        if (kind) rows.push(["Kind", kind]);
        if (target.entry_point) rows.push(["Entry point", h("code", null, target.entry_point)]);
        if (target.sha256) rows.push(["SHA-256", h("span", { class: "pn-code-row" }, h("code", null, target.sha256), RE.copyButton(target.sha256, "Copy the SHA-256"))]);
      }
      const binding = isObj(s.analysis_provider_binding) ? s.analysis_provider_binding : null;
      const prov = binding ? binding.provider : s.provider;
      if (prov) rows.push(["Provider", [providerName(prov), binding && binding.selection_source ? ` (chosen by ${binding.selection_source.replace(/-/g, " ")})` : ""].join("")]);
      const health = isObj(s.provider_operation_health) ? s.provider_operation_health : null;
      if (health && health.state) rows.push(["Provider health", chip(health.state.replace(/_/g, " "), health.state === "idle" ? "ok" : health.state === "busy" ? "busy" : /exited|unreachable/.test(health.state) ? "bad" : ""),
        health.retry_action && health.retry_action !== "unknown" ? ` · next: ${health.retry_action.replace(/_/g, " ")}` : ""]);
      const activity = isObj(s.analysis_activity) ? s.analysis_activity : null;
      if (activity && activity.status && activity.status !== "not_observed") rows.push(["Activity", activity.status]);
      const ta = isObj(s.tool_availability) ? s.tool_availability : null;
      let unavailable = [];
      if (ta) {
        if (Array.isArray(ta.groups)) {
          let yes = 0, no = 0;
          for (const g of ta.groups) {
            const n = Array.isArray(g.names) ? g.names.length : 1;
            if (g.available) yes += n; else { no += n; unavailable.push(g); }
          }
          rows.push(["Tools", `${yes.toLocaleString()} available now${no ? `, ${no.toLocaleString()} not (see below)` : ""}`]);
        } else if (typeof ta.available === "number") {
          rows.push(["Tools", `${ta.available.toLocaleString()} available now${ta.unavailable ? `, ${ta.unavailable.toLocaleString()} not` : ""}`]);
        }
      }
      const pieces = [h("dl", { class: "pn-facts" }, rows.flatMap(([k, ...vals]) => [h("dt", null, k), h("dd", null, vals)]))];
      if (s.cleanup_pending === true) {
        pieces.unshift(h("div", { class: "notice" }, icon("alert", 16),
          h("div", null, h("b", null, "REA is still cleaning up. "), "Something from the last target is still being released. Wait a moment before opening another target, then check again.")));
      }
      if (unavailable.length) {
        const reasons = h("ul", { class: "pn-plain" }, unavailable.slice(0, 40).map((g) => h("li", null,
          h("code", null, (g.names || []).slice(0, 6).join(", ") + ((g.names || []).length > 6 ? ` and ${g.names.length - 6} more` : "")),
          `: ${String(g.reason || "unavailable").replace(/_/g, " ")}`, g.remediation ? h("span", { class: "pn-hint" }, ` ${g.remediation}`) : null)));
        pieces.push(disclosure(`Why some tools can’t run now (${unavailable.length})`, () => h("div", null,
          h("p", { class: "pn-hint" }, "REA’s own reasons. You can still run any tool; REA answers with what is missing."), reasons)));
      }
      const candidates = Array.isArray(s.analysis_provider_candidates) ? s.analysis_provider_candidates : [];
      if (candidates.length) {
        pieces.push(h("div", { class: "pn-candidates" }, h("b", null, "Providers REA can use"),
          h("ul", { class: "pn-plain" }, candidates.map((c) => {
            const av = isObj(c.availability) ? c.availability.status : "";
            const sup = isObj(c.target_support) ? c.target_support.status : "";
            return h("li", null, providerName(c.provider) || "provider", " ",
              av ? chip(av, av === "available" ? "ok" : av === "unavailable" ? "bad" : "") : null, " ",
              sup ? chip(sup === "supported" ? "supports this target" : sup === "unsupported" ? "can’t read this target" : "support unknown", sup === "supported" ? "ok" : sup === "unsupported" ? "warn" : "") : null,
              isObj(c.availability) && c.availability.reason && av !== "available" ? h("span", { class: "pn-hint" }, ` ${c.availability.reason}`) : null);
          }))));
      }
      pieces.push(disclosure("REA’s full answer", () => RE.evidence.renderValue(value)));
      keepFocus(out, () => put(out, pieces), () => p.focusTarget());
      // the provider choice for the next open
      const keep = provider.value;
      const opts = candidates.filter((c) => c && isObj(c.provider) && c.provider.id && (!isObj(c.availability) || c.availability.status !== "unavailable"));
      put(provider, h("option", { value: "" }, "Choose the provider for me"),
        opts.map((c) => h("option", { value: c.provider.id }, providerName(c.provider))));
      if ([...provider.options].some((o) => o.value === keep)) provider.value = keep;
      show(providerF.el, !isWeb(v.brief) && opts.length > 1 && !open);
      v.sessionOpen = open;
      drawButtons();
    }
    function drawButtons() {
      const live = running(v);
      const open = !!v.sessionOpen && !!value;
      show(openBtn, !isWeb(v.brief) && v.tools.has("open_binary") && !open);
      show(closeBtn, !isWeb(v.brief) && v.tools.has("close_binary") && open && live);
      block(openBtn, claudeHere(v) ? "Claude is working here. Ask it, or wait." : claudeAnywhere() ? "Claude is working on another subject." : "");
      block(closeBtn, claudeHere(v) ? "Claude is working here. Stop Claude first." : runsHere(v) ? "A tool is running here." : "");
      block(refresh, live ? "" : "The engine isn’t running.");
    }

    async function openTarget() {
      await busy(openBtn, "Opening…", async () => {
        const d = await RE.api(`/api/tool/defaults?slug=${enc(v.slug)}&name=open_binary`);
        const args = d && isObj(d.arguments) ? { ...d.arguments } : {};
        if (!args.path) { RE.toast("The subject has no target path to open.", { kind: "bad" }); return; }
        if (provider.value) args.provider_id = provider.value;
        RE.announce("Opening the target in REA…");
        const reply = await RE.runTool(v.slug, "open_binary", args, { source: "panel" });
        if (v.disposed || (reply && reply.cancelled)) return;
        if (!reply || reply.error) { fill(msg, back, note("bad", reply ? reply.error : "The studio did not answer.")); RE.announce(reply ? reply.error : "No answer.", "assertive"); return; }
        if (reply.entry && reply.entry.error) { fill(msg, back, failNote({ toolError: reply.entry.error, message: reply.entry.error.message }, "open the target")); RE.announce(`REA could not open the target: ${reply.entry.error.message || ""}`, "assertive"); }
        else RE.announce("The target is open in REA.");
        if (reply.entry && reply.entry.id) v.findings.revealWhenLoaded(reply.entry.id, false);
        await RE.refresh();
        await reload(true, true);
      });
    }
    async function closeTarget() {
      const ok = await RE.confirm({ title: "Close the target in REA?", confirmLabel: "Close target", danger: true,
        message: "REA closes its analysis session and every provider it started for this target. Its in-memory evidence ledger and unknowns are cleared too: export an evidence bundle first if you want them. The studio’s Findings stay as they are." });
      if (!ok) return;
      await busy(closeBtn, "Closing…", async () => {
        const r = await call(v, "close_binary", {}, { record: true, start: false, confirmed: true });
        if (v.disposed) return;
        if (!r.ok) { fill(msg, back, failNote(r, "close the target")); RE.announce(r.message, "assertive"); }
        else RE.announce("The target is closed in REA.");
        await RE.refresh();
        await reload(true, true);
        v.unknowns.reload(true);
      });
    }

    return {
      el: p.el, panel: p, reload,
      catalog() { drawButtons(); },
      update() { drawButtons(); reload(false); },
      state() { drawButtons(); if (stale && !engineBusy(v)) reload(true); },
    };
  }

  function disclosure(label, build) {
    const det = h("details", { class: "re-disc pn-disc" }, h("summary", null, icon("caret", 13), label));
    let built = false;
    det.addEventListener("toggle", () => {
      if (!det.open || built) return;
      built = true;
      let node;
      try { node = build(); } catch (err) { console.error(err); node = h("p", { class: "pn-hint" }, "This could not be shown."); }
      det.append(h("div", { class: "pn-disc-body" }, node));
    });
    return det;
  }

  // ======================================================================
  // Debug sessions: Frida, GDB, Rizin debugger, Cutter (from /api/sessions)
  // ======================================================================
  const DEBUG = {
    frida: { label: "Frida", status: "frida_session_status", close: "close_frida_session" },
    gdb: { label: "GDB", status: "gdb_session_status", close: "close_gdb_session" },
    rizin: { label: "Rizin debugger", status: "rizin_debug_session_status", close: "close_rizin_debug_session" },
    cutter: { label: "Cutter", status: null, close: null },
  };
  function debugSection(v) {
    const p = panel("debug", { title: "Debug sessions", iconName: "bug",
      lede: "Debugger and instrumentation sessions REA has open for this subject. Close them when you are done: closing detaches REA; it never kills the target." });
    const list = h("ul", { class: "pn-rows" });
    const msg = h("div", { class: "results" });
    p.body.append(msg, list);
    show(p.el, false);
    const rows = new Map();
    let seq = 0, sig = "";
    async function reload() {
      if (v.disposed) return;
      if (!running(v)) { draw([]); return; }
      const my = ++seq;
      const r = await RE.api(`/api/sessions?slug=${enc(v.slug)}`);
      if (my !== seq || v.disposed) return;
      if (r.error) { fill(msg, () => p.focusTarget(), note("bad", r.error)); return; }
      fill(msg, () => p.focusTarget());
      draw(Array.isArray(r.sessions) ? r.sessions : []);
    }
    function draw(sessions) {
      const now = JSON.stringify(sessions);
      if (now === sig) return;
      sig = now;
      p.setCount(sessions.length ? String(sessions.length) : "");
      const keys = sessions.map((s) => `${s.kind}:${s.session_id}`);
      for (const [key, row] of rows) {
        if (keys.includes(key)) continue;
        if (row.li.contains(document.activeElement)) {
          // The session just closed: focus goes to the next one, or to Findings when none is left.
          const next = row.li.nextElementSibling || row.li.previousElementSibling;
          const target = next && next.querySelector("button");
          (target || (sessions.length ? p.focusTarget() : v.findings.panel.focusTarget())).focus({ preventScroll: true });
        }
        row.li.remove();
        rows.delete(key);
      }
      sessions.forEach((s, i) => {
        const key = keys[i];
        let row = rows.get(key);
        if (!row) { row = makeRow(s, key); rows.set(key, row); }
        if (list.children[i] !== row.li) list.insertBefore(row.li, list.children[i] || null);
      });
      show(p.el, sessions.length > 0);
    }
    function makeRow(s, key) {
      const info = DEBUG[s.kind] || { label: s.kind, status: null, close: null };
      const out = h("div", { class: "pn-row-out" });
      const actions = h("div", { class: "pn-row-actions" });
      const name = `${info.label} session ${shortId(s.session_id)}`;
      if (info.status && v.tools.has(info.status)) {
        const st = button("Status", { iconName: "info", act: "status", aria: `Status of the ${name}` });
        st.addEventListener("click", () => busy(st, "Asking…", async () => {
          const r = await call(v, info.status, { session_id: s.session_id }, { record: false, start: false });
          if (v.disposed) return;
          const entry = r.entry || null;
          put(out, entry ? h("div", { class: "pn-inline-ev" }, RE.evidence.body(entry, { slug: v.slug })) : failNote(r, "report the session"),
            button("Hide", { cls: "btn ghost small", act: "hide", onclick: () => { put(out); st.focus(); } }));
          RE.announce(r.ok ? `Status of the ${name} is shown.` : r.message, r.ok ? "polite" : "assertive");
        }));
        actions.append(st);
      }
      if (info.close && v.tools.has(info.close)) {
        const cl = button("Close", { iconName: "power", cls: "btn small danger", act: "close", aria: `Close the ${name}` });
        cl.addEventListener("click", async () => {
          if (cl.getAttribute("aria-disabled") === "true") return;
          const ok = await RE.confirm({ title: `Close the ${info.label} session?`, confirmLabel: "Close session", danger: true,
            message: s.kind === "frida" ? "REA unloads its scripts and detaches from the process. A paused process it spawned is resumed. The process is not killed."
              : "REA closes its debugger. A target it can detach from safely may keep running; REA never kills it. REA’s binary session is retired too, so open the target again before more analysis." });
          if (!ok) return;
          await busy(cl, "Closing…", async () => {
            // Recorded, so the studio's session list (built from findings) sees the close.
            const r = await call(v, info.close, { session_id: s.session_id }, { record: true, start: false, confirmed: true });
            if (v.disposed) return;
            if (!r.ok) { put(out, failNote(r, "close the session")); RE.announce(r.message, "assertive"); return; }
            RE.announce(`The ${info.label} session is closed.`);
            await RE.refresh();
            reload();
          });
        });
        actions.append(cl);
      }
      const li = h("li", { class: "pn-row", "data-key": key },
        h("div", { class: "pn-row-main" },
          h("b", null, info.label), " ",
          h("code", { title: s.session_id }, shortId(s.session_id)), " ", RE.copyButton(s.session_id, `Copy the ${info.label} session id`),
          h("span", { class: "pn-hint" }, s.at ? [" · started ", timeEl(s.at)] : null, s.tool ? ` · by ${s.tool}` : "")),
        actions, out);
      return { li };
    }
    return { el: p.el, panel: p, reload, catalog() {}, update() {}, state() {} };
  }

  // ======================================================================
  // Findings: search and filters, the keyed list of evidence cards, report, clear
  // ======================================================================
  function findingsSection(v) {
    const p = panel("findings", { title: "Findings", iconName: "list",
      lede: "Every tool result, whether Claude ran it or you did, newest first. Each keeps its evidence and its limitations." });
    const search = h("input", { class: "input pn-search", type: "search", autocomplete: "off", spellcheck: "false",
      placeholder: "Search tools, titles and results", "data-act": "search" });
    const searchF = field("Search findings", search, { wide: false });
    searchF.el.classList.add("pn-search-field");
    const toolSel = h("select", { class: "input", "data-act": "tool" }, h("option", { value: "" }, "Every tool"));
    const toolF = field("Tool", toolSel, { wide: false });
    const chips = {};
    const FILTERS = [["observed", "Observed"], ["derived", "Derived"], ["inferred", "Inferred"], ["limits", "Has limitations"], ["failed", "Failed"]];
    const filterRow = h("div", { class: "pn-filters", role: "group", "aria-label": "Show only findings that are" },
      FILTERS.map(([k, label]) => {
        const b = h("button", { class: "btn small pn-filter", type: "button", "aria-pressed": "false", "data-act": `filter-${k}` }, label);
        b.addEventListener("click", () => {
          const on = b.getAttribute("aria-pressed") !== "true";
          b.setAttribute("aria-pressed", String(on));
          if (on) active.add(k); else active.delete(k);
          draw(true);
        });
        chips[k] = b;
        return b;
      }));
    const toolbar = h("div", { class: "pn-toolbar", role: "search", "aria-label": "Findings" }, searchF.el, toolF.el, filterRow);
    const countLine = h("p", { class: "pn-countline" });
    const prunedNote = h("p", { class: "pn-hint pn-pruned", hidden: true });
    const errorBox = h("div", { class: "results" });
    const list = h("ol", { class: "pn-cards", "aria-label": "Findings, newest first" });
    const empty = h("div", { class: "pn-empty", hidden: true });
    const more = button("Show older findings", { iconName: "down", act: "more", cls: "btn small" });
    const moreRow = h("div", { class: "pn-more", hidden: true }, more);
    more.addEventListener("click", () => busy(more, "Loading…", () => load({ more: true })));

    const md = h("a", { class: "btn small", "data-act": "report-md", download: "", "aria-label": "Download the findings report as Markdown",
      title: "Every finding and the conversation, as one Markdown file" }, icon("download", 14), h("span", null, "Report"));
    const js = h("a", { class: "btn small ghost", "data-act": "report-json", download: "", "aria-label": "Download every finding as JSON",
      title: "Every finding, with its full result, as JSON" }, h("span", null, "JSON"));
    const clear = button("Clear findings", { iconName: "trash", cls: "btn small danger", act: "clear" });
    clear.addEventListener("click", clearAll);
    p.actions.append(md, js, clear);
    p.apply();
    p.body.append(toolbar, countLine, prunedNote, errorBox, list, empty, moreRow);

    const active = new Set();
    const knownTools = new Set();
    const cards = new Map();      // entry id -> { li, card, session }
    let items = [], total = 0, pruned = 0, rev = null, seq = 0, q = "", tool = "", loaded = false;
    let pendingReveal = null, timer = 0;

    const href = (fmt) => `/api/evidence/report?slug=${enc(v.slug)}&format=${fmt}`;
    md.setAttribute("href", href("md"));
    js.setAttribute("href", href("json"));

    search.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => { q = search.value.trim(); load({ user: true }); }, 250);
    });
    search.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && search.value) { e.preventDefault(); e.stopPropagation(); search.value = ""; clearTimeout(timer); q = ""; load({ user: true }); }
    });
    toolSel.addEventListener("change", () => { tool = toolSel.value; load({ user: true }); });

    async function load({ more: older = false, user = false } = {}) {
      if (v.disposed) return;
      const my = ++seq;
      const offset = older ? items.length : 0;
      const limit = older ? PAGE : user ? PAGE : Math.min(KEPT, Math.max(PAGE, items.length));
      const reply = await RE.api(`/api/evidence?slug=${enc(v.slug)}&q=${enc(q)}&tool=${enc(tool)}&offset=${offset}&limit=${limit}`);
      if (my !== seq || v.disposed) return;
      if (!reply || reply.error || !Array.isArray(reply.items)) {
        fill(errorBox, () => p.focusTarget(), note("bad", (reply && reply.error) || "The findings could not be read.",
          button("Try again", { iconName: "refresh", act: "retry", onclick: () => load() })));
        return;
      }
      fill(errorBox, () => p.focusTarget());
      if (older) {
        const have = new Set(items.map((e) => e.id));
        items = items.concat(reply.items.filter((e) => !have.has(e.id)));
      } else items = reply.items;
      total = Number(reply.total) || 0;
      pruned = Number(reply.pruned) || 0;
      loaded = true;
      if (!q && !tool) {
        for (const e of items) if (e.tool) knownTools.add(e.tool);
        v.overview.drawStats(items, total);
        v.latest = items;
      }
      drawTools();
      draw(user || older);
    }

    function drawTools() {
      const names = [...knownTools].sort();
      const have = [...toolSel.options].slice(1).map((o) => o.value);
      if (JSON.stringify(have) === JSON.stringify(names)) return;
      const keep = toolSel.value;
      // Rebuilt only when the set of tools changes; a select keeps its focus through option changes.
      while (toolSel.options.length > 1) toolSel.remove(1);
      for (const n of names) toolSel.add(new Option(n, n));
      if (keep && !names.includes(keep)) toolSel.add(new Option(keep, keep));
      toolSel.value = keep;
    }

    function keep(e) {
      if (!active.size) return true;
      const ev = envelopeOf(e);
      const conf = ev && typeof ev.confidence === "string" ? ev.confidence : null;
      const confs = ["observed", "derived", "inferred"].filter((k) => active.has(k));
      if (confs.length && !confs.includes(conf)) return false;
      if (active.has("limits") && !(ev && Array.isArray(ev.limitations) && ev.limitations.length)) return false;
      if (active.has("failed") && !e.error) return false;
      return true;
    }
    const keyOf = (e) => e.id || `seq-${e.seq}`;

    function draw(say = false) {
      const session = (v.brief.engine && v.brief.engine.session) || null;
      const filtered = !!(q || tool || active.size);
      const hasAny = total > 0 || (v.brief.evidence_count || 0) > 0;
      if (!(hasAny || filtered) && toolbar.contains(document.activeElement)) p.focusTarget().focus({ preventScroll: true });
      show(toolbar, hasAny || filtered);
      const away = () => (RE.visible(search) ? search : p.focusTarget());
      const shown = items.filter(keep);
      const keys = shown.map(keyOf);
      const wanted = new Set(keys);
      const all = new Set(items.map(keyOf));
      const detach = (c) => {
        if (!c.li.isConnected) return;
        if (c.li.contains(document.activeElement)) away().focus({ preventScroll: true });
        c.li.remove();
      };
      for (const [key, c] of cards) {
        if (!all.has(key)) { detach(c); cards.delete(key); continue; }
        if (c.session !== session) {
          // A new engine session changes a card's "earlier session" label. A closed card is
          // rebuilt for it; an open or focused one is left as it is, so nothing open is lost.
          if (!c.card.open && !c.li.contains(document.activeElement)) { detach(c); cards.delete(key); continue; }
          c.session = session;
        }
        if (!wanted.has(key)) detach(c);   // filtered out: kept, so it comes back as it was
      }
      let before = list.firstElementChild;
      shown.forEach((e, i) => {
        const key = keys[i];
        let c = cards.get(key);
        if (!c) {
          let card;
          try { card = RE.evidence.card(e, { slug: v.slug, session }); }
          catch (err) { console.error("A finding could not be drawn:", err); card = h("div", { class: "ev" }, h("p", { class: "pn-hint" }, `${e.tool || "A finding"} could not be shown.`)); }
          c = { li: h("li", { class: "pn-card", "data-key": key }, card), card, session };
          cards.set(key, c);
        }
        if (c.li !== before) {
          const had = c.li.contains(document.activeElement) ? document.activeElement : null;
          list.insertBefore(c.li, before);
          if (had) had.focus({ preventScroll: true });
        } else before = before.nextElementSibling;
      });

      let text;
      if (!loaded) text = "Loading…";
      else if (!total && !filtered) text = "";
      else if (filtered) {
        text = `${plural(shown.length, "finding")} ${shown.length === 1 ? "matches" : "match"}`;
        if (active.size && items.length < total) text += ` among the ${items.length.toLocaleString()} loaded; show older ones to look further`;
        else if (shown.length < total) text += `; showing the newest ${shown.length.toLocaleString()} of ${total.toLocaleString()}`;
        text += ".";
      } else text = shown.length < total ? `Showing the newest ${shown.length.toLocaleString()} of ${total.toLocaleString()}.` : `${plural(total, "finding")}.`;
      setText(countLine, text);
      show(countLine, !!text);
      p.setCount(total && !filtered ? total.toLocaleString() : "");
      if (say && loaded) RE.announce(text || "No findings.");

      show(prunedNote, pruned > 0);
      if (pruned > 0) setText(prunedNote, `${plural(pruned, "older finding")} ${pruned === 1 ? "was" : "were"} removed to keep the store small: it keeps the newest ${KEPT}. Download the report to keep a full record as you go.`);

      const none = loaded && !shown.length;
      show(empty, none);
      if (none) {
        const k = filtered ? "filtered" : "none";
        if (empty.dataset.kind !== k) {
          empty.dataset.kind = k;
          keepFocus(empty, () => put(empty, filtered
            ? [h("p", null, "Nothing matches these filters."), button("Clear the search and filters", { act: "reset", onclick: resetFilters })]
            : [h("p", null, h("b", null, "No findings yet. ")), h("p", null, "Ask Claude something, use a quick action, or run a tool from the Tools page. Every result lands here with its evidence.")]),
          () => search);
        }
      }
      if (moreRow.contains(document.activeElement) && !(loaded && items.length < total)) away().focus({ preventScroll: true });
      show(moreRow, loaded && items.length < total);
      if ([md, js, clear].includes(document.activeElement) && !hasAny) p.focusTarget().focus({ preventScroll: true });
      show(md, hasAny); show(js, hasAny); show(clear, hasAny);
      block(clear, claudeHere(v) ? "Claude is working here. Wait for it first." : "");
      p.apply();

      if (pendingReveal && cards.has(pendingReveal.id)) {
        const c = cards.get(pendingReveal.id);
        const focus = pendingReveal.focus;
        pendingReveal = null;
        if (c.li.isConnected) {
          if (focus) RE.evidence.reveal(c.card);
          else { c.card.classList.add("ev-flash"); setTimeout(() => c.card.classList.remove("ev-flash"), 1800); }
        }
      }
    }

    function resetFilters() {
      search.value = ""; q = "";
      toolSel.value = ""; tool = "";
      active.clear();
      for (const b of Object.values(chips)) b.setAttribute("aria-pressed", "false");
      search.focus();
      load({ user: true });
    }

    async function clearAll() {
      if (clear.getAttribute("aria-disabled") === "true") { RE.announce(clear.dataset.why || "Not now.", "assertive"); return; }
      const n = v.brief.evidence_count || total;
      const ok = await RE.confirm({ title: "Clear every finding?", confirmLabel: "Clear findings", danger: true,
        message: `This deletes the ${plural(n, "finding")} kept for ${v.brief.name} on this PC. The conversation stays. REA’s own record in a running engine is not changed: export an evidence bundle first if you want to keep it, or download the report.` });
      if (!ok) return;
      const reply = await RE.post("/api/evidence/clear", { slug: v.slug });
      if (reply.error) { RE.toast(reply.error, { kind: "bad" }); return; }
      RE.announce("Findings cleared.");
      if (p.el.contains(document.activeElement) || document.activeElement === document.body) p.focusTarget().focus({ preventScroll: true });
      await RE.refresh();
    }

    return {
      el: p.el, panel: p,
      revealWhenLoaded(id, focus = true) {
        pendingReveal = { id, focus };
        if (cards.has(id)) draw();
      },
      catalog() {},
      update(b) {
        if (b.evidence_rev !== rev) { rev = b.evidence_rev; load(); }
        else if (loaded) draw();
      },
      state() { if (loaded) block(clear, claudeHere(v) ? "Claude is working here. Wait for it first." : ""); },
    };
  }

  // ======================================================================
  // Unknowns: REA's residual-unknown ledger (list, filter, record, change status, verify, audit)
  // ======================================================================
  const STATUSES = ["open", "investigating", "blocked", "contradicted", "resolved"];
  const SEVERITIES = ["low", "medium", "high", "critical"];
  const STATUS_KIND = { open: "warn", investigating: "busy", blocked: "bad", contradicted: "bad", resolved: "ok" };
  const SEVERITY_KIND = { low: "", medium: "warn", high: "bad", critical: "bad" };
  const DISPOSITIONS = [["", "No resolution yet"], ["verified", "Verified by evidence"], ["withdrawn", "Withdrawn"], ["out-of-scope", "Out of scope"]];

  function enumOf(schema, key, fallback) {
    const prop = schema && schema.properties && schema.properties[key];
    if (prop && Array.isArray(prop.enum)) return prop.enum;
    return fallback;
  }

  function unknownsSection(v) {
    const p = panel("unknowns", { title: "Unknowns", iconName: "question", collapsible: true, open: true,
      lede: "Open questions REA tracks for this engine session, each with its evidence. Record one, change its status, or have Claude audit them.",
      onOpen: () => reload(true) });
    const status = h("select", { class: "input", "data-act": "u-status" }, h("option", { value: "" }, "Any status"), STATUSES.map((s) => h("option", { value: s }, s)));
    const severity = h("select", { class: "input", "data-act": "u-severity" }, h("option", { value: "" }, "Any severity"), SEVERITIES.map((s) => h("option", { value: s }, s)));
    const statusF = field("Status", status, { wide: false });
    const severityF = field("Severity", severity, { wide: false });
    const refresh = button("Refresh", { iconName: "refresh", act: "u-refresh", title: "Asks REA for its unknowns. Starts nothing.",
      onclick: () => busy(refresh, "Loading…", () => reload(true, true)) });
    const recordBtn = button("Record an unknown", { iconName: "plus", act: "u-record", onclick: () => recordDialog() });
    const auditBtn = button("Audit with Claude", { iconName: "sparkle", act: "u-audit", onclick: () => audit(null) });
    p.actions.append(refresh);
    p.apply();
    const msg = h("div", { class: "results" });
    const list = h("ul", { class: "pn-rows pn-unknowns", "aria-label": "Unknowns" });
    const countLine = h("p", { class: "pn-countline", hidden: true });
    const more = button("Show more", { iconName: "down", act: "u-more", onclick: () => busy(more, "Loading…", () => reload(true, true, true)) });
    const moreRow = h("div", { class: "pn-more", hidden: true }, more);
    p.body.append(h("div", { class: "pn-toolbar" }, statusF.el, severityF.el, h("div", { class: "pn-toolbar-actions" }, recordBtn, auditBtn)),
      msg, countLine, list, moreRow);
    status.addEventListener("change", () => reload(true, true));
    severity.addEventListener("change", () => reload(true, true));

    const rows = new Map();   // unknown_id -> { li, sig, head, out }
    const back = () => p.focusTarget();
    let heads = [], total = 0, nextOffset = null, loadedKey = null, seq = 0, stale = false;
    const listSchema = () => (v.tools.get("list_unknowns") || {}).inputSchema || {};

    async function reload(force = false, user = false, older = false) {
      if (v.disposed) return;
      if (!v.tools.has("list_unknowns")) return;
      if (!p.open && !user) { stale = true; return; }
      if (!running(v)) {
        const had = loadedKey !== "idle";
        heads = []; total = 0; nextOffset = null; loadedKey = "idle";
        if (had) {
          draw();
          fill(msg, back, note("soft", "The engine isn’t running. REA keeps unknowns in memory while it runs: start it from Overview, or import an evidence bundle that holds them."));
        }
        drawButtons();
        return;
      }
      const key = `${v.runKey}|${v.brief.evidence_rev}|${status.value}|${severity.value}`;
      if (!force && key === loadedKey) return;
      if (!user && engineBusy(v)) { stale = true; return; }
      stale = false;
      loadedKey = key;
      const args = {};
      if (status.value) args.status = status.value;
      if (severity.value) args.severity = severity.value;
      const props = listSchema().properties || {};
      if (older && nextOffset !== null) { args.offset = nextOffset; if ("limit" in props) args.limit = 100; }
      else if ("limit" in props) args.limit = 100;
      const my = ++seq;
      const r = await call(v, "list_unknowns", args);
      if (my !== seq || v.disposed) return;
      if (!r.ok) { fill(msg, back, failNote(r, "list its unknowns")); return; }
      fill(msg, back);
      const val = isObj(r.value) ? r.value : {};
      const got = Array.isArray(val.items) ? val.items.filter(isObj) : Array.isArray(r.value) ? r.value.filter(isObj) : [];
      heads = older ? heads.concat(got.filter((g) => !heads.some((x) => x.unknown_id === g.unknown_id))) : got;
      total = typeof val.total === "number" ? val.total : heads.length;
      nextOffset = typeof val.next_offset === "number" ? val.next_offset : null;
      draw();
      if (user) RE.announce(heads.length ? `${plural(total, "unknown")}.` : "No unknowns match.");
    }

    function draw() {
      const keys = heads.map((u) => String(u.unknown_id || ""));
      for (const [key, row] of rows) {
        if (keys.includes(key)) continue;
        if (row.li.contains(document.activeElement)) p.focusTarget().focus({ preventScroll: true });
        row.li.remove();
        rows.delete(key);
      }
      heads.forEach((u, i) => {
        const key = keys[i];
        const sig = JSON.stringify(u);
        let row = rows.get(key);
        if (!row) { row = { li: h("li", { class: "pn-row pn-unk", "data-key": key }), sig: "", out: h("div", { class: "pn-row-out" }) }; rows.set(key, row); }
        row.head = u;
        if (row.sig !== sig) { row.sig = sig; keepFocus(row.li, () => fillRow(row, u), () => p.focusTarget()); }
        if (list.children[i] !== row.li) {
          const had = row.li.contains(document.activeElement) ? document.activeElement : null;
          list.insertBefore(row.li, list.children[i] || null);
          if (had) had.focus({ preventScroll: true });
        }
      });
      const filtered = !!(status.value || severity.value);
      show(countLine, running(v));
      setText(countLine, !heads.length ? (filtered ? "No unknowns match these filters." : "REA tracks no unknowns for this subject yet.")
        : heads.length < total ? `Showing ${heads.length.toLocaleString()} of ${total.toLocaleString()}.` : `${plural(total, "unknown")}.`);
      p.setCount(total ? total.toLocaleString() : "");
      show(moreRow, nextOffset !== null && heads.length < total);
      drawButtons();
    }

    function evidenceChips(ids, label) {
      if (!Array.isArray(ids) || !ids.length) return null;
      return h("div", { class: "pn-unk-ev" }, h("span", { class: "pn-hint" }, `${label}: `),
        h("span", { class: "ev-chips" }, ids.slice(0, 12).map((id) => {
          const b = h("button", { class: "chip-btn mono", type: "button", "data-act": `ev-${id}`, title: id, "aria-label": `Show evidence ${shortId(id)}` }, shortId(id));
          b.addEventListener("click", () => {
            if (!RE.evidence.reveal(id)) RE.toast("That evidence isn’t among the loaded findings. It may be from an imported bundle or an earlier engine session.", { kind: "info" });
          });
          return b;
        }), ids.length > 12 ? h("span", { class: "pn-hint" }, ` and ${ids.length - 12} more`) : null));
    }

    function fillRow(row, u) {
      const name = `unknown: ${String(u.question || u.unknown_id).slice(0, 60)}`;
      const res = isObj(u.resolution) ? u.resolution : null;
      const verify = v.tools.has("verify_unknown_resolution")
        ? button("Verify", { iconName: "check", act: "verify", aria: `Verify the ${name}`, title: "Asks REA to recheck this unknown’s record and resolution against its evidence" }) : null;
      const change = v.tools.has("update_unknown") ? button("Change status", { iconName: "settings", act: "change", aria: `Change the status of the ${name}` }) : null;
      const auditOne = button("Audit with Claude", { iconName: "sparkle", act: "audit", aria: `Audit the ${name} with Claude` });
      if (verify) verify.addEventListener("click", () => busy(verify, "Checking…", async () => {
        const r = await call(v, "verify_unknown_resolution", { unknown_id: u.unknown_id });
        if (v.disposed) return;
        put(row.out, r.ok ? verdictView(r.value) : failNote(r, "verify it"),
          button("Hide", { cls: "btn ghost small", act: "hide-verify", onclick: () => { put(row.out); verify.focus(); } }));
        RE.announce(r.ok ? verdictText(r.value) : r.message, r.ok ? "polite" : "assertive");
      }));
      if (change) change.addEventListener("click", () => changeDialog(row));
      auditOne.addEventListener("click", () => audit(u.unknown_id));
      put(row.li,
        h("div", { class: "pn-row-main" },
          h("p", { class: "pn-unk-q" }, u.question || "(no question text)"),
          h("div", { class: "ev-chips" },
            u.status ? chip(u.status, STATUS_KIND[u.status] || "", "Status") : null,
            u.severity ? chip(`${u.severity} severity`, SEVERITY_KIND[u.severity] || "", "Severity") : null,
            u.domain ? chip(u.domain, "", "Domain") : null,
            u.required_confidence ? chip(`needs ${u.required_confidence}`, "", "The confidence an answer needs") : null,
            u.revision !== undefined ? chip(`revision ${u.revision}`, "", "Each change makes a new revision") : null,
            h("code", { class: "pn-hint", title: u.unknown_id }, shortId(u.unknown_id))),
          evidenceChips(u.supporting_evidence_ids, "Supported by"),
          evidenceChips(u.contradicting_evidence_ids, "Contradicted by"),
          res ? h("p", { class: "pn-hint" }, h("b", null, `Resolution: ${res.disposition || "set"}. `), res.rationale || "") : null),
        h("div", { class: "pn-row-actions" }, verify, change, v.prompts.some((x) => x.name === "audit_residual_unknowns") ? auditOne : null),
        row.out);
    }

    function verdictText(val) {
      const x = isObj(val) ? val : {};
      if (typeof x.verdict === "string") return `REA’s verdict: ${x.verdict}.`;
      const bits = [];
      if (x.valid === true) bits.push("The record checks out.");
      else if (x.valid === false) bits.push("REA found a problem with this record.");
      if (x.truthVerified === true) bits.push("Its resolution is verified by evidence.");
      else if (x.truthVerified === false) bits.push("Its answer is not verified as true.");
      return bits.join(" ") || "REA answered.";
    }
    function verdictView(val) {
      const x = isObj(val) ? val : {};
      const good = x.valid === true && x.truthVerified === true;
      return h("div", { class: `result ${good ? "ok" : x.valid === false ? "bad" : "soft"}` }, icon(good ? "check" : "info", 16),
        h("div", null, h("div", null, verdictText(val)), disclosure("REA’s full answer", () => RE.evidence.renderValue(val))));
    }

    async function audit(unknownId) {
      const prompt = v.prompts.find((x) => x.name === "audit_residual_unknowns");
      if (!prompt) { RE.toast("REA lists no unknowns audit workflow.", { kind: "info" }); return; }
      await startWorkflow(v, prompt, unknownId ? { unknown_id: unknownId } : {});
    }

    // Change status: every required field is carried from the head as it is; only status,
    // severity and the resolution are edited, with expected_revision = the head's revision.
    function changeDialog(row) {
      let head = row.head;
      const schema = (v.tools.get("update_unknown") || {}).inputSchema || {};
      const st = h("select", { class: "input" }, enumOf(listSchema(), "status", STATUSES).map((s) => h("option", { value: s }, s)));
      const sv = h("select", { class: "input" }, enumOf(listSchema(), "severity", SEVERITIES).map((s) => h("option", { value: s }, s)));
      const disp = h("select", { class: "input" }, DISPOSITIONS.map(([val, label]) => h("option", { value: val }, label)));
      const why = h("textarea", { class: "input area", rows: 3, spellcheck: "true" });
      const ids = h("textarea", { class: "input area", rows: 3, spellcheck: "false", placeholder: "ev_…" });
      const stF = field("Status", st, { wide: false });
      const svF = field("Severity", sv, { wide: false });
      const dispF = field("Resolution", disp, { hint: "How the question was settled. Verified needs at least one evidence id." });
      const whyF = field("Rationale", why, { hint: "Why it is settled, in a sentence or two." });
      const idsF = field("Evidence ids", ids, { optional: true, hint: "One per line. Copy them from a finding’s Evidence id." });
      const problem = h("div", { class: "form-error", role: "alert", hidden: true });
      const fill = () => {
        st.value = head.status || "open";
        sv.value = head.severity || "medium";
        const res = isObj(head.resolution) ? head.resolution : null;
        disp.value = res && res.disposition ? res.disposition : "";
        why.value = res && res.rationale ? res.rationale : "";
        ids.value = res && Array.isArray(res.evidence_ids) ? res.evidence_ids.join("\n") : "";
      };
      fill();
      const sync = () => { const on = !!disp.value; show(whyF.el, on); show(idsF.el, on); };
      disp.addEventListener("change", sync);
      sync();
      const save = h("button", { class: "btn primary", type: "submit" }, h("span", { class: "pn-label" }, "Save"));
      const cancel = h("button", { class: "btn", type: "button" }, "Cancel");
      const form = h("form", { class: "form", novalidate: true },
        h("p", { class: "modal-text" }, h("b", null, head.question || head.unknown_id)),
        h("p", { class: "pn-hint" }, `Revision ${head.revision}. Saving makes revision ${Number(head.revision) + 1}; REA refuses it if someone changed the unknown in between.`),
        h("div", { class: "fields" }, stF.el, svF.el, dispF.el, whyF.el, idsF.el), problem,
        h("div", { class: "modal-foot" }, cancel, save));
      const d = RE.dialog.open({ title: "Change the unknown’s status", body: form, wide: true, initialFocus: st });
      cancel.addEventListener("click", () => d.close());
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        for (const f of [whyF, idsF]) f.error("");
        problem.hidden = true;
        let resolution = null;
        if (disp.value) {
          const list_ = ids.value.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
          const bad = list_.find((x) => !/^ev[-_]/.test(x));   // REA checks the exact form
          if (!why.value.trim()) { whyF.error("Say why it is settled."); why.focus(); return; }
          if (bad) { idsF.error(`“${bad.slice(0, 40)}” isn’t an evidence id (they start with ev_).`); ids.focus(); return; }
          if (disp.value === "verified" && !list_.length) { idsF.error("Verified needs at least one evidence id."); ids.focus(); return; }
          resolution = { disposition: disp.value, rationale: why.value.trim(), evidence_ids: list_ };
        }
        const props = schema.properties || null;
        const carry = {
          unknown_id: head.unknown_id, expected_revision: head.revision, status: st.value, severity: sv.value,
          supporting_evidence_ids: Array.isArray(head.supporting_evidence_ids) ? head.supporting_evidence_ids : [],
          contradicting_evidence_ids: Array.isArray(head.contradicting_evidence_ids) ? head.contradicting_evidence_ids : [],
          required_authority: head.required_authority === undefined ? null : head.required_authority,
          required_environment: head.required_environment === undefined ? null : head.required_environment,
          recommended_probes: Array.isArray(head.recommended_probes) ? head.recommended_probes : [],
          relationships: Array.isArray(head.relationships) ? head.relationships : [],
          resolution,
        };
        if (head.required_confidence !== undefined) carry.required_confidence = head.required_confidence;
        const args = {};
        for (const [k, val] of Object.entries(carry)) if (!props || k in props) args[k] = val;
        await busy(save, "Saving…", async () => {
          const r = await call(v, "update_unknown", args, { record: true, start: false });
          if (v.disposed) return;
          if (r.ok) {
            d.close();
            RE.toast("The unknown is updated.", { kind: "ok" });
            await RE.refresh();
            reload(true);
            return;
          }
          const code = r.toolError && (r.toolError.code || r.toolError.category);
          if (code === "revision_conflict" || /conflict/.test(String(code))) {
            await reload(true, true);
            const fresh = heads.find((x) => x.unknown_id === head.unknown_id);
            if (fresh) {
              head = fresh;
              put(problem, icon("alert", 16), h("div", null, `The unknown changed since it was loaded; it is now revision ${fresh.revision}. Your choices are kept: press Save again to apply them over it.`));
            } else put(problem, icon("alert", 16), h("div", null, "The unknown changed and is no longer listed. Close this and check the list."));
            problem.hidden = false;
            return;
          }
          put(problem, icon("alert", 16), h("div", null, r.message));
          problem.hidden = false;
        });
      });
    }

    // Record: a form built from REA's own record_unknown schema.
    function recordDialog() {
      const tool = v.tools.get("record_unknown");
      if (!tool) { RE.toast("REA lists no record_unknown tool.", { kind: "info" }); return; }
      const schema = tool.inputSchema || {};
      const props = schema.properties || {};
      const prefill = {};
      // Required lists start empty; every choice is left to the person recording it.
      for (const k of ["recommended_probes", "relationships"]) if (props[k] && (schema.required || []).includes(k)) prefill[k] = [];
      const form = RE.forms.build(schema, { idPrefix: "unk-new", prefill });
      const save = h("button", { class: "btn primary", type: "submit" }, h("span", { class: "pn-label" }, "Record unknown"));
      const cancel = h("button", { class: "btn", type: "button" }, "Cancel");
      const formEl = h("form", { class: "f-dialog-form", novalidate: true },
        h("p", { class: "f-intro" }, "An open question REA should keep track of, with what an answer would need. It is held for this engine session; export an evidence bundle to keep it."),
        form.el, h("div", { class: "modal-foot" }, cancel, save));
      const d = RE.dialog.open({ title: "Record an unknown", body: formEl, wide: true });
      cancel.addEventListener("click", () => d.close());
      formEl.addEventListener("submit", async (e) => {
        e.preventDefault();
        const r0 = form.read();
        form.setErrors(r0.errors);
        if (!r0.ok) { form.focusFirstError(); RE.announce(`${plural(r0.errors.length, "field")} need${r0.errors.length === 1 ? "s" : ""} attention.`, "assertive"); return; }
        if (!running(v)) { form.setErrors([{ field: null, message: "Start the engine first: REA keeps unknowns while it runs." }]); return; }
        await busy(save, "Recording…", async () => {
          const r = await call(v, "record_unknown", r0.value, { record: true, start: false });
          if (v.disposed) return;
          if (!r.ok) { form.setErrors([{ field: null, message: r.message }]); RE.announce(r.message, "assertive"); return; }
          d.close();
          RE.toast("Unknown recorded.", { kind: "ok" });
          await RE.refresh();
          reload(true);
        });
      });
    }

    function drawButtons() {
      const live = running(v);
      show(recordBtn, v.tools.has("record_unknown"));
      show(auditBtn, v.prompts.some((x) => x.name === "audit_residual_unknowns"));
      block(recordBtn, live ? "" : "Start the engine first.");
      block(refresh, live ? "" : "The engine isn’t running.");
      block(auditBtn, askBlocked());
    }

    return {
      el: p.el, panel: p, reload,
      catalog() { show(p.el, v.tools.has("list_unknowns")); drawButtons(); for (const row of rows.values()) { row.sig = ""; } draw(); },
      update(b, prev) {
        drawButtons();
        // Changes to unknowns show up as new findings (record_unknown, update_unknown, imports), so
        // a new evidence_rev is the cue to look again, once the engine is free.
        if (!prev || prev.evidence_rev !== b.evidence_rev || engineOf(prev) !== engineOf(b)) reload(false);
      },
      state() { drawButtons(); if (stale && !engineBusy(v)) reload(true); },
    };
  }

  // ======================================================================
  // Analysis settings: REA's editable runtime limits, and keeping them for every engine start
  // ======================================================================
  function analysisSection(v) {
    const p = panel("analysis", { title: "Analysis settings", iconName: "sliders", collapsible: true, open: false,
      lede: "REA’s adjustable limits: timeouts, heap sizes, admission sizes. Changes last until REA exits; keep one here and the studio sets it again after every engine start.",
      onOpen: () => reload(true) });
    const refresh = button("Refresh", { iconName: "refresh", act: "a-refresh", title: "Asks REA for its settings. Starts nothing.",
      onclick: () => busy(refresh, "Loading…", () => reload(true, true)) });
    p.actions.append(refresh);
    p.apply();
    const msg = h("div", { class: "results" });
    const list = h("ul", { class: "pn-rows pn-settings", "aria-label": "Analysis settings" });
    const keptNote = h("div", { class: "results" });
    p.body.append(msg, list, keptNote);
    const rows = new Map();
    let items = [], saved = {}, loadedKey = null, seq = 0, stale = false, savedLoaded = false;

    function specs() {
      const tool = v.tools.get("update_analysis_settings");
      const out = {};
      const items_ = tool && tool.inputSchema && tool.inputSchema.properties && tool.inputSchema.properties.changes
        ? tool.inputSchema.properties.changes.items : null;
      const options = items_ ? items_.anyOf || items_.oneOf || [] : [];
      let reset = false;
      for (const o of options) {
        const props = o && o.properties;
        if (!props || !props.setting) continue;
        const action = props.action && props.action.const;
        if (action === "reset") { reset = true; continue; }
        const name = props.setting.const;
        if (typeof name === "string" && props.value) out[name] = props.value;
      }
      return { values: out, reset, form: items_ ? "changes" : tool && tool.inputSchema && tool.inputSchema.properties && tool.inputSchema.properties.settings ? "settings" : "changes" };
    }
    function numberRule(schema) {
      // {nullable, integer, min, max} from a value schema like {anyOf:[{type:integer,minimum,maximum},{type:null}]}
      const rule = { nullable: false, integer: false, min: null, max: null };
      const walk = (s) => {
        if (!isObj(s)) return;
        if (Array.isArray(s.anyOf || s.oneOf)) { (s.anyOf || s.oneOf).forEach(walk); return; }
        const types = [].concat(s.type || []);
        if (types.includes("null")) rule.nullable = true;
        if (types.includes("integer")) rule.integer = true;
        if (typeof s.minimum === "number") rule.min = s.minimum;
        if (typeof s.maximum === "number") rule.max = s.maximum;
      };
      walk(schema);
      return rule;
    }

    async function loadSaved() {
      const r = await RE.api("/api/settings");
      if (v.disposed) return;
      saved = r && r.settings && isObj(r.settings.analysis_settings) ? r.settings.analysis_settings : {};
      savedLoaded = true;
    }

    async function reload(force = false, user = false) {
      if (v.disposed || !v.tools.has("inspect_analysis_settings")) return;
      if (!p.open && !user) { stale = true; return; }
      if (!savedLoaded || user) await loadSaved();
      const back = () => p.focusTarget();
      if (!running(v)) {
        items = []; loadedKey = null;
        draw();
        fill(msg, back, note("soft", "The engine isn’t running. Start it from Overview to see and change REA’s limits."));
        return;
      }
      if (!force && loadedKey === v.runKey) return;
      if (!user && engineBusy(v)) { stale = true; return; }
      stale = false;
      loadedKey = v.runKey;
      const my = ++seq;
      const r = await call(v, "inspect_analysis_settings", {});
      if (my !== seq || v.disposed) return;
      if (!r.ok) { fill(msg, back, failNote(r, "list its settings")); return; }
      fill(msg, back);
      const val = isObj(r.value) ? r.value : {};
      items = (Array.isArray(val.settings) ? val.settings : Array.isArray(r.value) ? r.value : []).filter(isObj).map((s) => ({
        name: String(s.setting || s.name || ""), value: s.value, launch: s.launch_value, def: s.default_value !== undefined ? s.default_value : s.default,
        min: s.minimum, max: s.maximum, unit: s.unit || "", applies: s.applies_at || "", source: s.source || "", provider: s.provider || "",
        operations: Array.isArray(s.operations) ? s.operations : [], error: isObj(s.configuration_error) ? s.configuration_error : null,
      })).filter((s) => s.name);
      draw();
      if (user) RE.announce(`${plural(items.length, "setting")} loaded.`);
    }

    const fmtVal = (x, unit) => (x === null ? "REA’s default" : x === undefined ? "—" : `${Number(x).toLocaleString()}${unit ? " " + unit : ""}`);
    function draw() {
      const sp = specs();
      const names = items.map((s) => s.name);
      for (const [key, row] of rows) {
        if (names.includes(key)) continue;
        if (row.li.contains(document.activeElement)) p.focusTarget().focus({ preventScroll: true });
        row.li.remove();
        rows.delete(key);
      }
      items.forEach((s, i) => {
        let row = rows.get(s.name);
        if (!row) { row = makeRow(s.name); rows.set(s.name, row); }
        row.fill(s, sp);
        if (list.children[i] !== row.li) list.insertBefore(row.li, list.children[i] || null);
      });
      const keptNames = Object.keys(saved);
      const notListed = keptNames.filter((k) => !names.includes(k));
      put(keptNote, keptNames.length
        ? note("soft", `Kept for every engine start: ${keptNames.map((k) => `${k} = ${saved[k] === null ? "default" : saved[k]}`).join(", ")}.`,
          notListed.length && items.length ? `REA doesn’t list ${notListed.join(", ")} now; Settings can stop keeping ${notListed.length === 1 ? "it" : "them"}.` : null)
        : null);
      p.setCount(keptNames.length ? `${keptNames.length} kept` : "");
    }

    function makeRow(name) {
      const safe = name.replace(/[^a-z0-9]+/gi, "-");
      const input = h("input", { class: "input mono", type: "number", inputmode: "numeric", id: `pn-as-${safe}`, "data-act": "value" });
      const meta = h("span", { class: "field-hint", id: `pn-as-${safe}-meta` });
      const err = h("span", { class: "field-error", id: `pn-as-${safe}-err`, hidden: true });
      const set = button("Set", { act: "set", aria: `Set ${name}` });
      const reset = button("Reset", { cls: "btn small ghost", act: "reset", aria: `Reset ${name} to its launch value` });
      const keep = h("input", { type: "checkbox", id: `pn-as-${safe}-keep`, "data-act": "keep" });
      const keepLabel = h("label", { for: keep.id, class: "pn-check" }, keep,
        h("span", null, "Keep for every engine start", h("span", { class: "sr-only" }, ` (${name})`)));
      const label = h("label", { class: "field-label mono", for: input.id }, name);
      const li = h("li", { class: "pn-row pn-setting", "data-key": name },
        h("div", { class: "pn-setting-main" }, label, meta, err),
        h("div", { class: "pn-setting-edit" }, input, set, reset, keepLabel));
      let current = null, rule = null, sp = null;
      const describe = () => setAttr(input, "aria-describedby", [err.hidden ? null : err.id, meta.id].filter(Boolean).join(" "));
      const showErr = (text) => { err.textContent = text || ""; err.hidden = !text; setAttr(input, "aria-invalid", text ? "true" : null); describe(); };
      describe();
      input.addEventListener("input", () => showErr(""));
      // Enter in the value sets it, as in every other value form in the studio.
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); apply("set"); }
      });
      function parse() {
        const raw = input.value.trim();
        if (!raw) {
          if (rule && rule.nullable) return { ok: true, value: null };
          return { ok: false, message: "Give a number." };
        }
        const n = Number(raw);
        if (!isFinite(n)) return { ok: false, message: "Give a number." };
        if (rule && rule.integer && !Number.isInteger(n)) return { ok: false, message: "Give a whole number." };
        const min = rule && rule.min !== null ? rule.min : current && typeof current.min === "number" ? current.min : null;
        const max = rule && rule.max !== null ? rule.max : current && typeof current.max === "number" ? current.max : null;
        if (min !== null && n < min) return { ok: false, message: `The smallest REA allows is ${min.toLocaleString()}.` };
        if (max !== null && n > max) return { ok: false, message: `The largest REA allows is ${max.toLocaleString()}.` };
        return { ok: true, value: n };
      }
      async function apply(action) {
        const parsed = action === "set" ? parse() : { ok: true, value: undefined };
        if (!parsed.ok) { showErr(parsed.message); input.focus(); RE.announce(parsed.message, "assertive"); return; }
        const args = sp.form === "settings"
          ? { settings: [{ name, value: action === "reset" ? (current ? current.def : null) : parsed.value }] }
          : { changes: [action === "reset" ? { setting: name, action: "reset" } : { setting: name, action: "set", value: parsed.value }] };
        const b = action === "set" ? set : reset;
        await busy(b, action === "set" ? "Setting…" : "Resetting…", async () => {
          const r = await call(v, "update_analysis_settings", args, { record: false, start: false });
          if (v.disposed) return;
          if (!r.ok) { showErr(r.message); RE.announce(r.message, "assertive"); return; }
          const val = isObj(r.value) ? r.value : {};
          // A kept value follows the change: a new value is kept instead, a reset stops keeping it.
          const wasKept = keep.checked;
          if (wasKept) await saveKept(action === "set" ? parsed.value : undefined, true);
          RE.announce(`${name} ${action === "set" ? "set" : "reset"}${wasKept && action === "reset" ? " and no longer kept" : ""}.${val.rea_restart_required ? " REA needs a restart for it to apply." : ""}`);
          if (val.rea_restart_required) RE.toast(`${name} applies after REA restarts: close the engine and start it again.`, { kind: "info" });
          await reload(true, false);
        });
      }
      async function saveKept(value, quiet = false) {
        const next = { ...saved };
        if (value === undefined) delete next[name]; else next[name] = value;
        const reply = await RE.post("/api/settings", { values: { analysis_settings: next } });
        if (reply.problems || reply.error) {
          const text = (reply.problems || [reply.error]).join(" ");
          showErr(text); RE.announce(text, "assertive");
          keep.checked = name in saved;
          return false;
        }
        saved = reply.settings && isObj(reply.settings.analysis_settings) ? reply.settings.analysis_settings : next;
        if (!quiet) RE.announce(value === undefined ? `${name} is no longer kept.` : `${name} is kept and set again after every engine start.`);
        draw();
        return true;
      }
      set.addEventListener("click", () => apply("set"));
      reset.addEventListener("click", () => {
        apply("reset");
      });
      keep.addEventListener("change", async () => {
        if (keep.checked) {
          const parsed = parse();
          if (!parsed.ok) { keep.checked = false; showErr(parsed.message); RE.announce(parsed.message, "assertive"); return; }
          await saveKept(parsed.value);
        } else await saveKept(undefined);
      });
      return {
        li,
        fill(s, specs_) {
          current = s; sp = specs_;
          rule = numberRule(sp.values[name]);
          if (rule.integer) setAttr(input, "step", "1"); else setAttr(input, "step", "any");
          const min = rule.min !== null ? rule.min : s.min, max = rule.max !== null ? rule.max : s.max;
          setAttr(input, "min", typeof min === "number" ? min : null);
          setAttr(input, "max", typeof max === "number" ? max : null);
          setAttr(input, "placeholder", rule.nullable ? "Empty: REA’s default" : "");
          if (document.activeElement !== input && !input.getAttribute("aria-invalid")) {
            const show_ = s.value === null || s.value === undefined ? "" : String(s.value);
            if (input.dataset.shown !== show_) { input.value = show_; input.dataset.shown = show_; }
          }
          const bits = [`Now ${fmtVal(s.value, s.unit)}`];
          if (s.launch !== undefined && s.launch !== s.value) bits.push(`at launch ${fmtVal(s.launch, s.unit)}`);
          if (s.def !== undefined) bits.push(`default ${fmtVal(s.def, s.unit)}`);
          if (typeof min === "number" || typeof max === "number") bits.push(`allowed ${typeof min === "number" ? min.toLocaleString() : "…"} to ${typeof max === "number" ? max.toLocaleString() : "…"}`);
          if (s.applies) bits.push(`applies at the ${s.applies.replace(/_/g, " ")}`);
          if (s.provider) bits.push(`for ${s.provider}`);
          if (s.source && s.source !== "default") bits.push(`set by ${s.source}`);
          if (name in saved) bits.push(`kept: ${saved[name] === null ? "default" : Number(saved[name]).toLocaleString()}`);
          setText(meta, bits.join(" · ") + "." + (s.error ? ` REA reports a problem with its configuration: ${s.error.message || s.error.code}.` : ""));
          keep.checked = name in saved;
          show(reset, sp.reset || sp.form === "settings");
          const why = running(v) ? (claudeHere(v) ? "Claude is working here. Wait for it first." : "") : "The engine isn’t running.";
          block(set, why); block(reset, why);
        },
      };
    }

    return {
      el: p.el, panel: p, reload,
      catalog() { show(p.el, v.tools.has("inspect_analysis_settings")); },
      update(b, prev) { if (!prev || engineOf(prev) !== engineOf(b)) reload(false); else if (items.length) draw(); },
      state() { if (stale && !engineBusy(v)) reload(true); },
    };
  }

  // ======================================================================
  // Evidence bundles: export REA's ledger for this engine session, or import one
  // ======================================================================
  function bundlesSection(v) {
    const p = panel("bundles", { title: "Evidence bundles", iconName: "archive", collapsible: true, open: false,
      lede: "REA’s own record of this engine session: every Evidence record, including ones Findings doesn’t list, and its unknowns. Export it to keep it past an engine restart; import one to pick up where you left off.",
      onOpen: () => prefill() });
    const outPath = h("input", { class: "input mono", type: "text", autocomplete: "off", spellcheck: "false", "data-act": "export-path" });
    const overwrite = h("input", { type: "checkbox", id: `pn-bundle-over-${v.slug.replace(/[^a-z0-9]/gi, "-")}`, "data-act": "overwrite" });
    const outF = field("Export to", outPath, { hint: "A full path on this PC. The studio suggests this subject’s work folder." });
    const exportBtn = button("Export bundle", { iconName: "download", cls: "btn small primary", act: "export" });
    const exportOut = h("div", { class: "results" });
    const inPath = h("input", { class: "input mono", type: "text", autocomplete: "off", spellcheck: "false", "data-act": "import-path", placeholder: "C:\\…\\evidence.json" });
    const inF = field("Import from", inPath, { hint: "A bundle REA exported before. Its content is read as data and merged; nothing in it runs." });
    const importBtn = button("Import bundle", { iconName: "upload", cls: "btn small", act: "import" });
    const importOut = h("div", { class: "results" });
    p.body.append(
      h("div", { class: "pn-bundle" }, h("h3", { class: "pn-h3" }, "Export"), outF.el,
        h("div", { class: "pn-form-row" }, h("label", { class: "pn-check", for: overwrite.id }, overwrite, h("span", null, "Replace the file if it is there")), h("span", { class: "grow" }), exportBtn),
        exportOut),
      h("div", { class: "pn-bundle" }, h("h3", { class: "pn-h3" }, "Import"), inF.el,
        h("div", { class: "pn-form-row" }, h("span", { class: "grow" }), importBtn), importOut));
    let prefilled = false;
    async function prefill() {
      if (prefilled || v.disposed || !v.tools.has("export_evidence_bundle")) return;
      prefilled = true;
      const d = await RE.api(`/api/tool/defaults?slug=${enc(v.slug)}&name=export_evidence_bundle`);
      if (v.disposed) return;
      const path = d && isObj(d.arguments) ? d.arguments.path : "";
      if (path && !outPath.value) outPath.value = path;
    }
    function checkPath(f, text) {
      const t = String(text || "").trim().replace(/^(["'])(.*)\1$/, "$2").trim();
      if (!t) { f.error("Give a full path."); return null; }
      if (networkPath(t)) { f.error("Network paths aren’t supported. Use a folder on this PC."); return null; }
      if (!absolutePath(t)) { f.error("Give the full path, starting with the drive letter, like C:\\Users\\you\\evidence.json."); return null; }
      f.error("");
      return t;
    }
    outPath.addEventListener("input", () => outF.error(""));
    inPath.addEventListener("input", () => inF.error(""));
    function receipt(val, lines) {
      const x = isObj(val) ? val : {};
      return note("ok", lines(x));
    }
    exportBtn.addEventListener("click", async () => {
      if (exportBtn.getAttribute("aria-disabled") === "true") { RE.announce(exportBtn.dataset.why || "Not now.", "assertive"); return; }
      const path = checkPath(outF, outPath.value);
      if (!path) { outPath.focus(); return; }
      outPath.value = path;
      if (overwrite.checked) {
        const ok = await RE.confirm({ title: "Replace the file if it is there?", confirmLabel: "Export and replace", danger: true,
          message: `REA writes the bundle to ${path}, replacing any file already there.` });
        if (!ok) return;
      }
      await busy(exportBtn, "Exporting…", async () => {
        const args = { path };
        if (overwrite.checked) args.overwrite = true;
        // The path is shown and chosen here, so this click is the confirmation the server asks for.
        const r = await call(v, "export_evidence_bundle", args, { record: true, start: false, confirmed: true });
        if (v.disposed) return;
        if (!r.ok) { put(exportOut, failNote(r, "export its bundle")); RE.announce(r.message, "assertive"); return; }
        const where = (isObj(r.value) && r.value.path) || path;
        put(exportOut, receipt(r.value, (x) => [
          h("span", null, "Exported to ", h("code", null, where), " ", RE.copyButton(where, "Copy the bundle’s path")),
          [typeof x.bytes === "number" ? RE.fmt.bytes(x.bytes) : null, typeof x.records === "number" ? plural(x.records, "record") : null,
            typeof x.unknowns === "number" ? plural(x.unknowns, "unknown") : null].filter(Boolean).join(" · ")]));
        RE.announce(`Bundle exported to ${where}.`);
        await RE.refresh();
      });
    });
    importBtn.addEventListener("click", async () => {
      if (importBtn.getAttribute("aria-disabled") === "true") { RE.announce(importBtn.dataset.why || "Not now.", "assertive"); return; }
      const path = checkPath(inF, inPath.value);
      if (!path) { inPath.focus(); return; }
      inPath.value = path;
      await busy(importBtn, "Importing…", async () => {
        const r = await call(v, "import_evidence_bundle", { path }, { record: true, start: true });
        if (v.disposed) return;
        if (!r.ok) { put(importOut, failNote(r, "import the bundle")); RE.announce(r.message, "assertive"); return; }
        put(importOut, receipt(r.value, (x) => [
          `Imported ${typeof x.imported === "number" ? plural(x.imported, "record") : "the bundle"}${typeof x.unknowns_added === "number" && x.unknowns_added ? ` and ${plural(x.unknowns_added, "unknown")}` : ""}.`,
          typeof x.total === "number" ? `REA now holds ${plural(x.total, "record")} for this session.` : null]));
        RE.announce("Bundle imported.");
        await RE.refresh();
        v.unknowns.reload(true);
      });
    });
    function drawButtons() {
      show(p.el, v.tools.has("export_evidence_bundle") || v.tools.has("import_evidence_bundle"));
      const live = running(v);
      block(exportBtn, !live ? "Start the engine first: the bundle is REA’s in-memory record of its session." : claudeHere(v) ? "Claude is working here. Wait for it first." : "");
      block(importBtn, claudeHere(v) ? "Claude is working here. Wait for it first." : claudeAnywhere() ? "Claude is working on another subject." : "");
      show(outF.el.parentElement, v.tools.has("export_evidence_bundle"));
      show(inF.el.parentElement, v.tools.has("import_evidence_bundle"));
    }
    if (p.open) prefill();
    return { el: p.el, panel: p, catalog() { drawButtons(); if (p.open) prefill(); }, update() { drawButtons(); }, state() { drawButtons(); } };
  }

  // ======================================================================
  // The view: one per open subject
  // ======================================================================
  let view = null;

  function createView(container, brief) {
    const v = { slug: brief.slug, brief, container, disposed: false, runKey: 0, tools: new Map(), prompts: [], latest: [],
      sessionOpen: false, catalogKey: null, jobKey: "" };
    v.overview = overviewSection(v);
    v.session = sessionSection(v);
    v.debug = debugSection(v);
    v.findings = findingsSection(v);
    v.unknowns = unknownsSection(v);
    v.analysis = analysisSection(v);
    v.bundles = bundlesSection(v);
    v.sections = [v.overview, v.session, v.debug, v.findings, v.unknowns, v.analysis, v.bundles];
    // Panels that need REA's catalogue stay hidden until it is read.
    for (const s of [v.session, v.unknowns, v.analysis, v.bundles]) show(s.el, false);
    v.root = h("div", { class: "pn-root" }, v.sections.map((s) => s.el));
    put(container, v.root);

    async function loadCatalog() {
      const c = RE.state.catalog || {};
      const key = `${c.ready}|${c.count}|${c.version || ""}`;
      if (key === v.catalogKey) return;
      v.catalogKey = key;
      let cat = null;
      try { cat = await RE.catalog(); } catch (err) { console.error(err); }
      if (v.disposed) return;
      v.tools.clear();
      for (const group of (cat && cat.categories) || []) for (const t of group.tools || []) if (t && t.name) v.tools.set(t.name, t);
      v.prompts = (cat && Array.isArray(cat.prompts)) ? cat.prompts : [];
      if (!cat || cat.error) v.catalogKey = null;   // the studio did not answer: try again on a later poll
      show(v.session.el, v.tools.has("binary_session"));
      for (const s of v.sections) { try { s.catalog(); } catch (err) { console.error(err); } }
      v.session.reload(false);
      v.unknowns.reload(false);
      v.analysis.reload(false);
    }
    v.loadCatalog = loadCatalog;

    const off = RE.on("state", () => {
      if (v.disposed) return;
      loadCatalog();
      const j = RE.state.job;
      const jobKey = j && j.slug === v.slug ? `${j.status}` : "";
      const ended = v.jobKey === "running" && jobKey !== "running";
      v.jobKey = jobKey;
      for (const s of v.sections) { try { s.state(); } catch (err) { console.error(err); } }
      if (ended) { v.session.reload(true); v.unknowns.reload(true); v.debug.reload(); }
    });
    v.dispose = () => { v.disposed = true; off(); };
    loadCatalog();
    return v;
  }

  function update(v, brief) {
    const prev = v.brief === brief ? null : v.brief;
    const first = !v.updated;
    v.updated = true;
    if ((first || (prev && engineOf(prev) !== "running")) && engineOf(brief) === "running") v.runKey++;
    if (prev && prev.engine && brief.engine && prev.engine.session && brief.engine.session && prev.engine.session !== brief.engine.session) v.runKey++;
    v.brief = brief;
    for (const s of v.sections) {
      try { s.update(brief, first ? null : prev); } catch (err) { console.error("A subject panel failed to update:", err); }
    }
    if (first || !prev || prev.evidence_rev !== brief.evidence_rev || engineOf(prev) !== engineOf(brief)) v.debug.reload();
  }

  function render(container, brief) {
    if (!container || !brief || !brief.slug) return;
    if (!view || view.slug !== brief.slug || view.container !== container || !container.contains(view.root)) {
      const hadFocus = container.contains(document.activeElement);
      if (view) view.dispose();
      view = createView(container, brief);
      if (hadFocus) {
        const title = document.getElementById("title");
        if (title) title.focus({ preventScroll: true });
      }
    }
    update(view, brief);
  }

  RE.panels = { render };
})();
