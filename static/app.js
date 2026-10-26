// The shell: shared state, polling, routing, sidebar, top bar, the empty page with its setup
// checklist, the Ask Claude panel, the subject dialogs and the shortcuts. It renders in place:
// a poll updates text and attributes on existing nodes and never rebuilds an element that has
// focus. The subject page, the Tools page and Settings belong to their own modules.
(() => {
  "use strict";

  const RE = window.RE;
  const { h, icon, put } = RE;
  const $ = (id) => document.getElementById(id);
  const state = RE.state;
  state.offline = false;

  const APP = "Reverse Engineer";
  const POLL_MS = 2000, BUSY_MS = 1000, OFFLINE_MS = 3000;
  const DEFAULT_HINT = "REA runs on this PC. Your question, the tool results Claude reads and the files it opens are sent to Anthropic.";

  const KINDS = {
    executable: { label: "Executable or library", short: "Executable", icon: "chip" },
    archive: { label: "Archive or app bundle", short: "Archive", icon: "archive" },
    database: { label: "Analysis database", short: "Analysis database", icon: "database" },
    artifact: { label: "Other artifact", short: "Artifact", icon: "file" },
    website: { label: "Website", short: "Website", icon: "globe" },
  };
  const kindOf = (s) => KINDS[s && s.kind] || KINDS.executable;
  const isWeb = (s) => !!s && s.kind === "website";
  const enc = encodeURIComponent;

  // ---------- small state helpers ----------
  const busy = () => !!state.job && state.job.status === "running";
  const workingOn = (slug) => busy() && state.job.slug === slug;
  const opening = new Set();   // slugs whose engine start this page asked for and is waiting on
  function engineState(s) {
    if (opening.has(s.slug)) return "starting";
    return (s.engine && s.engine.state) || "stopped";
  }
  function webState(s) {
    if (!s.web) return opening.has(s.slug) ? "starting" : "stopped";
    return s.web.state || "stopped";
  }
  function runsFor(slug) { return Object.values(state.runs || {}).filter((r) => r && r.slug === slug && r.status === "running"); }
  function setupNow() {
    if (state.setup) return state.setup;
    const c = state.checks || {};
    return { rea_ready: !!(state.catalog && state.catalog.ready), claude_ready: !!(c.claude && c.claude.ok),
      node_ready: !!(c.node && c.node.ok), notice_seen: true };
  }
  function setText(el, text) { const t = String(text ?? ""); if (el.textContent !== t) el.textContent = t; }
  function setAttr(el, name, value) {
    if (value === null || value === undefined || value === false) { if (el.hasAttribute(name)) el.removeAttribute(name); }
    else if (el.getAttribute(name) !== String(value)) el.setAttribute(name, value);
  }
  function show(el, on) { if (el.hidden === !!on) el.hidden = !on; }
  function isTyping(el) {
    return !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  }
  function dialogOpen() { return !!document.querySelector("dialog[open]"); }

  // Focus that would otherwise fall to <body> after a re-render goes to the page heading instead.
  let lastFocused = null;
  document.addEventListener("focusin", (e) => { lastFocused = e.target; });
  function rescueFocus() {
    if (dialogOpen() || !lastFocused) return;
    const active = document.activeElement;
    if (active && active !== document.body && RE.visible(active)) return;
    if (lastFocused !== active && RE.visible(lastFocused)) return;
    const target = headingNow();
    if (target) target.focus({ preventScroll: true });
  }
  function headingNow() {
    if (state.page === "subject" && !state.subjects.length) return $("emptyTitle");
    return RE.visible($("title")) ? $("title") : $("main");
  }

  // ---------- sidebar (one row per subject, updated in place) ----------
  const rows = new Map();
  function makeRow(slug) {
    const mark = h("span", { class: "nav-mark" });
    const name = h("span", { class: "name" });
    const sub = h("span", { class: "sub" });
    const dot = h("i", { class: "dot", "aria-hidden": "true" });
    const btn = h("button", { class: "nav-item", type: "button", "data-slug": slug, onclick: () => RE.go(enc(slug)) },
      mark, h("span", { class: "nav-text" }, name, sub), dot);
    return { li: h("li", { "data-slug": slug }, btn), btn, mark, name, sub, dot, kind: "" };
  }
  function rowStatus(s) {
    if (workingOn(s.slug)) return ["Claude is working", "busy"];
    const engine = engineState(s);
    const web = isWeb(s) ? webState(s) : null;
    if (engine === "crashed" || web === "crashed") return ["Stopped unexpectedly", "bad"];
    if (engine === "starting" || web === "starting") return [isWeb(s) ? "Starting the browser…" : "Starting REA…", "busy"];
    if (runsFor(s.slug).length) return ["Running a tool", "busy"];
    if (engine === "running") return [isWeb(s) && web === "running" ? "Browser and engine running" : "Engine running", "ok"];
    if (!isWeb(s) && s.exists === false) return [`${kindOf(s).short} · target missing`, "bad"];
    return [kindOf(s).short, ""];
  }
  function renderList() {
    const list = $("list");
    const seen = new Set();
    let cursor = list.firstElementChild;
    for (const s of state.subjects) {
      seen.add(s.slug);
      let row = rows.get(s.slug);
      if (!row) { row = makeRow(s.slug); rows.set(s.slug, row); }
      if (row.kind !== s.kind) { row.kind = s.kind; put(row.mark, icon(kindOf(s).icon, 16)); }
      const [sub, dot] = rowStatus(s);
      setText(row.name, s.name);
      setText(row.sub, sub);
      const dotClass = `dot${dot ? " " + dot : ""}`;
      if (row.dot.className !== dotClass) row.dot.className = dotClass;
      setAttr(row.btn, "aria-current", state.page === "subject" && s.slug === state.slug ? "page" : null);
      if (row.li === cursor) cursor = cursor.nextElementSibling;
      else {   // new, or the order changed: move it, keeping its focus
        const had = row.li.contains(document.activeElement) ? document.activeElement : null;
        list.insertBefore(row.li, cursor);
        if (had) had.focus({ preventScroll: true });
      }
      // A tooltip only when the name is cut off.
      setAttr(row.btn, "title", row.name.scrollWidth > row.name.clientWidth + 1 ? s.name : null);
    }
    for (const [slug, row] of rows) {
      if (seen.has(slug)) continue;
      if (row.li.contains(document.activeElement)) {
        const next = row.li.nextElementSibling || row.li.previousElementSibling;
        (next ? next.querySelector("button") : $("newSubject")).focus({ preventScroll: true });
      }
      row.li.remove();
      rows.delete(slug);
    }
    show($("listEmpty"), !state.subjects.length);
  }

  function renderSideFoot() {
    const known = !!state.checks && !state.checks.checking;
    show($("setupFoot"), known && RE.setupMissing(setupItems()).length > 0);
  }

  // ---------- top bar ----------
  let titleMarkKey = "";
  let statusSig = "";
  function renderTop() {
    const page = state.page;
    const s = RE.current();
    const top = $("top");
    const onSubject = page === "subject" && !!s;
    show(top, page !== "subject" || !!s);
    let title = APP, markKey = "", markIcon = null;
    if (page === "tools") { title = "Tools"; markKey = "tools"; markIcon = "tools"; }
    else if (page === "settings") { title = "Settings"; markKey = "settings"; markIcon = "settings"; }
    else if (s) { title = s.name; markKey = `kind:${s.kind}`; markIcon = kindOf(s).icon; }
    setText($("title"), title);
    if (markKey !== titleMarkKey) { titleMarkKey = markKey; put($("titleMark"), markIcon ? icon(markIcon, 18) : null); }
    const docTitle = page === "subject" && !s ? APP : `${title} · ${APP}`;
    if (document.title !== docTitle) document.title = docTitle;

    show($("subjectSettings"), onSubject);
    show($("engineBtn"), onSubject);
    show($("showAssist"), onSubject && !assistOpen);
    setAttr($("showAssist"), "aria-expanded", String(assistOpen && onSubject));
    if (!onSubject) { if (statusSig) { statusSig = ""; put($("status")); } return; }

    // Status chips: built only when what they say changes (they hold nothing focusable).
    const engine = engineState(s);
    const web = isWeb(s) ? webState(s) : null;
    const chips = [];
    const ENGINE = { stopped: ["Engine stopped", ""], starting: ["Starting REA…", "busy"], running: ["Engine running", "ok"], crashed: ["Engine stopped unexpectedly", "bad"] };
    const [eText, eKind] = ENGINE[engine] || ENGINE.stopped;
    chips.push({ text: eText, kind: eKind, title: engine === "crashed" && s.engine && s.engine.error ? s.engine.error : "" });
    if (web) {
      const WEB = { stopped: ["Browser closed", ""], starting: ["Starting the browser…", "busy"], running: ["Browser running", "ok"], crashed: ["Browser stopped unexpectedly", "bad"] };
      const [wText, wKind] = WEB[web] || WEB.stopped;
      chips.push({ text: wText, kind: wKind, title: s.web && s.web.error ? s.web.error : "" });
    }
    if (workingOn(s.slug)) chips.push({ text: `Claude is working · ${RE.fmt.duration(state.job.seconds || 0)}`, kind: "busy" });
    const runs = runsFor(s.slug).length;
    if (runs) chips.push({ text: runs > 1 ? `${runs} tools running` : "A tool is running", kind: "busy" });
    if (s.hashing) chips.push({ text: "Reading the target…", kind: "" });
    if (!web && s.exists === false) chips.push({ text: "Target missing", kind: "bad", title: "The file is not at its path now." });
    const sig = JSON.stringify(chips);
    if (sig !== statusSig) {
      statusSig = sig;
      put($("status"), chips.map((c) => h("span", { class: `chip ${c.kind}`, title: c.title || null },
        c.kind === "busy" ? h("i", { class: "pulse", "aria-hidden": "true" }) : c.kind === "ok" ? h("i", { class: "dot ok", "aria-hidden": "true" }) : c.kind === "bad" ? icon("alert", 13) : null,
        c.text)));
    }
    renderEngineButton(s, engine, web);
  }

  // One button that starts or closes this subject's engine (and browser, for a website).
  function renderEngineButton(s, engine, web) {
    const btn = $("engineBtn");
    const starting = engine === "starting" || web === "starting";
    const running = engine === "running" || web === "running";
    const claudeHere = workingOn(s.slug);
    let label, iconName, why = "";
    if (starting) { label = "Starting…"; iconName = "refresh"; why = "REA is starting for this subject."; }
    else if (running) { label = isWeb(s) ? "Close browser and engine" : "Close engine"; iconName = "power"; }
    else { label = isWeb(s) ? "Start browser and engine" : "Start engine"; iconName = "play"; }
    if (running && claudeHere) why = "Claude is using the engine. Stop Claude first.";
    const key = `${label}|${iconName}`;
    if (btn.dataset.key !== key) { btn.dataset.key = key; put(btn, icon(iconName, 14), h("span", null, label)); }
    setAttr(btn, "aria-disabled", starting || !!why ? "true" : null);
    setAttr(btn, "title", why || (running ? "Stops REA for this subject. Findings are kept." : isWeb(s)
      ? "Opens the site in a browser the studio controls, then starts REA. Observing a site loads it over the network."
      : "Starts REA for this subject. Claude also starts it when you ask something."));
    btn.classList.toggle("busy", starting);
    btn.dataset.action = running ? "close" : "open";
  }

  async function openEngine(slug) {
    if (opening.has(slug)) return;
    opening.add(slug);
    renderAll();
    const s = state.subjects.find((x) => x.slug === slug);
    RE.announce(isWeb(s) ? "Starting the browser and REA…" : "Starting REA…");
    const reply = await RE.post("/api/subject/open", { slug });
    opening.delete(slug);
    if (reply.error) RE.toast(reply.error, { kind: "bad" });
    else RE.announce(isWeb(s) ? "The browser and REA are running." : "REA is running.");
    await RE.refresh();
  }
  async function closeEngine(slug, what = "all") {
    const reply = await RE.post("/api/subject/close", { slug, what });
    if (reply.error) RE.toast(reply.error, { kind: "bad" });
    else RE.announce(what === "browser" ? "Browser closed." : "Engine closed.");
    await RE.refresh();
  }

  // ---------- setup checklist (empty page) and the setup banner (subject page) ----------
  // One definition (RE.setupItems), shared with Settings.
  function setupItems() { return RE.setupItems(state.checks, setupNow(), state.catalog); }

  const setupRows = new Map();
  function renderSetupList() {
    RE.drawSetup($("setupList"), setupRows, setupItems(), (id) => RE.go(`~settings/${id}`));
  }

  let bannerHidden = false;   // dismissed for this window
  let bannerFirst = "rea";    // the section the banner's button opens
  function renderBanner() {
    const box = $("setupBanner");
    const known = !!state.checks && !state.checks.checking;
    const missing = known ? RE.setupMissing(setupItems()) : [];
    const want = state.page === "subject" && !!RE.current() && missing.length > 0 && !bannerHidden;
    if (!want) { if (!box.hidden && box.contains(document.activeElement)) $("title").focus(); show(box, false); return; }
    bannerFirst = missing[0].id;
    if (!box.firstChild) {
      const text = h("div", { class: "banner-text" });
      const go = h("button", { class: "btn small", type: "button", onclick: () => RE.go(`~settings/${bannerFirst}`) }, "Open Settings");
      const x = h("button", { class: "btn ghost small square", type: "button", "aria-label": "Hide this notice", title: "Hide this notice",
        onclick: () => { bannerHidden = true; $("title").focus(); renderBanner(); } }, icon("x", 14));
      put(box, icon("alert", 16), text, go, x);
    }
    const askNeeds = missing.some((i) => i.id === "rea" || i.id === "claude") ? " Asking Claude needs both REA and Claude Code." : "";
    const said = missing.map((i) => i.missing).join(", ");
    setText(box.querySelector(".banner-text"), `Setup isn’t finished: ${said.charAt(0).toUpperCase()}${said.slice(1)}.${askNeeds}`);
    show(box, true);
  }

  // ---------- pages ----------
  let toolsRenderedFor = null;   // the catalogue readiness the Tools page was last built for
  let appearance = null;
  function showPage(page, { focus = false, tool = "", anchor = "" } = {}) {
    const before = state.page;
    state.page = page;
    const s = RE.current();
    const empty = page === "subject" && !state.subjects.length;
    const app = $("app");
    app.classList.toggle("on-tools", page === "tools");
    app.classList.toggle("on-settings", page === "settings");
    app.classList.toggle("on-empty", empty);
    show($("subjectPage"), page === "subject" && !!s);
    show($("emptyPage"), empty);
    show($("toolsPage"), page === "tools");
    show($("settingsPage"), page === "settings");
    setAttr($("openTools"), "aria-current", page === "tools" ? "page" : null);
    setAttr($("openSettings"), "aria-current", page === "settings" ? "page" : null);
    renderAll();
    if (page === "tools") enterTools(tool);
    if (page === "settings") enterSettings(anchor);
    if (page === "subject" && s) RE.store.set("lastSlug", s.slug);
    syncThread();
    if (before !== page || focus) RE.emit("route", { page, slug: state.slug });
    if (focus && !dialogOpen()) {
      const target = headingNow();
      if (target) target.focus({ preventScroll: true });
    }
  }

  async function enterTools(tool) {
    const box = $("toolsPage");
    if (!RE.tools || !RE.tools.render) {
      put(box, h("p", { class: "lede" }, "The Tools page did not load. Reload the window to try again."));
      return;
    }
    const ready = !!(state.catalog && state.catalog.ready);
    if (toolsRenderedFor !== ready) {
      toolsRenderedFor = ready;
      try { await RE.tools.render(box); } catch (err) { console.error(err); RE.toast("The Tools page could not be shown.", { kind: "bad" }); }
    }
    if (tool && state.page === "tools") {
      try { await RE.tools.openTool(tool); } catch (err) { console.error(err); }
    }
  }

  async function enterSettings(anchor) {
    const box = $("settingsPage");
    if (!window.studioSettings) { put(box, h("p", { class: "lede" }, "The Settings page did not load. Reload the window to try again.")); return; }
    if (!appearance) appearance = appearanceSection();
    try {
      await window.studioSettings.render({ h, icon, get: RE.api, post: RE.post, put, checks: () => state.checks, setTheme }, box, appearance);
    } catch (err) { console.error(err); RE.toast("The Settings page could not be shown.", { kind: "bad" }); return; }
    if (anchor && state.page === "settings") {
      const section = document.getElementById(`set-${anchor}`);
      if (section) {
        section.scrollIntoView({ block: "start" });
        const heading = section.querySelector("h2, h3") || section;
        heading.setAttribute("tabindex", "-1");
        heading.focus({ preventScroll: true });
      }
    }
  }

  // ---------- subject page ----------
  let panelsSig = "";
  function renderSubjectPage() {
    const s = RE.current();
    if (state.page !== "subject" || !s) return;
    renderBanner();
    if (!RE.panels || !RE.panels.render) return;
    const sig = JSON.stringify([s, opening.has(s.slug), state.job && [state.job.slug, state.job.status]]);
    if (sig === panelsSig) return;
    panelsSig = sig;
    try { RE.panels.render($("panels"), s); } catch (err) { console.error("The subject panels failed to render:", err); }
  }

  function renderAll() {
    renderList();
    renderSideFoot();
    renderTop();
    if (state.page === "subject") {
      if (state.subjects.length) renderSubjectPage();
      else renderSetupList();
    } else show($("setupBanner"), false);
    renderAssist();
  }

  // ---------- routing ----------
  // #<slug> · #~tools · #~tools/<tool> · #~settings · #~settings/<section> · #~new (the dialog, over the current page)
  let loaded = false;
  let backHash = "";   // where #~new returns to when its dialog closes
  let newPushed = false;   // the dialog's #~new was pushed from inside the studio (Back undoes it)
  let newTarget = "";      // set when the dialog closes on a new subject: where to go instead
  let skipRoute = null;    // the hash a history.back() of our own lands on: nothing to re-route
  function parseHash() {
    const raw = location.hash.replace(/^#/, "");
    let mark = raw;
    try { mark = decodeURIComponent(raw); } catch {}
    if (mark === "~new") return { page: "new" };
    if (mark === "~tools" || mark.startsWith("~tools/")) return { page: "tools", tool: mark.slice(7) };
    if (mark === "~settings" || mark.startsWith("~settings/")) return { page: "settings", anchor: mark.slice(10) };
    return { page: "subject", slug: mark || null };
  }
  function fallbackSlug() {
    const last = RE.store.get("lastSlug", null);
    if (last && state.subjects.some((s) => s.slug === last)) return last;
    return state.subjects.length ? state.subjects[0].slug : null;
  }
  function setSlug(slug) {
    if (state.slug === slug) return;
    state.slug = slug;
    panelsSig = "";
    RE.emit("subject", slug);
  }
  function route(user = true) {
    if (!loaded) return;
    const r = parseHash();
    if (r.page === "new") {
      if (!newDialog) newPushed = user;   // a hashchange pushed it; the first load did not
      if (!backHash) {   // opened straight from the address: put a page behind the dialog
        setSlug(fallbackSlug());
        backHash = state.slug ? `#${enc(state.slug)}` : "";
        showPage("subject");
      }
      openNewSubject();
      return;
    }
    if (newDialog) newDialog.close();
    if (r.page === "subject") {
      let slug = r.slug && state.subjects.some((s) => s.slug === r.slug) ? r.slug : null;
      if (!slug) {
        slug = fallbackSlug();
        const want = slug ? `#${enc(slug)}` : "";
        if (location.hash !== want) history.replaceState(null, "", want || location.pathname + location.search);
      }
      setSlug(slug);
    }
    backHash = location.hash;
    showPage(r.page, { focus: user, tool: r.tool, anchor: r.anchor });
  }
  function go(hash) {
    const want = "#" + String(hash || "").replace(/^#/, "");
    if (location.hash === want || (want === "#" && !location.hash)) route(true);
    else location.hash = want;
  }
  RE.go = go;
  window.addEventListener("hashchange", () => {
    if (skipRoute !== null && location.hash === skipRoute) { skipRoute = null; return; }
    skipRoute = null;
    route(true);
  });

  // ---------- polling ----------
  let timer = 0;
  let inflight = null;
  let queued = null;
  let offlineToast = null;
  const lastEngine = new Map();   // slug -> "engine|web" states seen, to notice a crash once

  function refresh() {
    if (inflight) {
      if (!queued) queued = inflight.then(() => { queued = null; return refresh(); });
      return queued;
    }
    clearTimeout(timer);
    inflight = pollOnce().finally(() => {
      inflight = null;
      const wait = state.offline ? OFFLINE_MS : busy() || Object.values(state.runs || {}).some((r) => r && r.status === "running") ? BUSY_MS : POLL_MS;
      clearTimeout(timer);
      timer = setTimeout(refresh, wait);
    });
    return inflight;
  }
  RE.refresh = refresh;

  async function pollOnce() {
    const data = await RE.api("/api/state");
    if (!data || !Array.isArray(data.subjects)) {
      goOffline(data && data.offline ? "The studio is not running. Start it again from its shortcut."
        : data && data.error ? `${data.error} Open the studio again from its shortcut.` : "The studio sent an answer it could not read.");
      return;
    }
    backOnline();
    const wasJob = state.job ? { slug: state.job.slug, status: state.job.status } : null;
    Object.assign(state, {
      subjects: data.subjects, job: data.job || null, runs: data.runs || {}, checks: data.checks || null,
      catalog: data.catalog || null, setup: data.setup || null, permissions: data.permissions || null, rev: data.rev,
    });
    try {
      if (!loaded) { loaded = true; route(false); }
      else if (state.page === "subject" && state.slug && !RE.current()) {
        // The open subject was removed: show another one (or the empty page).
        setSlug(fallbackSlug());
        history.replaceState(null, "", state.slug ? `#${enc(state.slug)}` : location.pathname + location.search);
        backHash = location.hash;
        showPage("subject");
      } else if (state.page === "subject" && !state.slug && state.subjects.length) {
        setSlug(fallbackSlug());
        history.replaceState(null, "", `#${enc(state.slug)}`);
        backHash = location.hash;
        showPage("subject");
      } else renderAll();
      noticeCrashes();
      await syncThread();
      noticeJob(wasJob);
      if (state.page === "settings" && window.studioSettings && window.studioSettings.refresh) window.studioSettings.refresh(state.checks);
      RE.emit("state", data);
      rescueFocus();
    } catch (err) {
      console.error("The page could not be brought up to date:", err);
    }
  }

  let offlineMuted = false;   // the person dismissed the notice; the Ask box still says why it is blocked
  function goOffline(message) {
    const first = !state.offline;
    state.offline = true;
    if (!offlineToast && !offlineMuted) {
      offlineToast = RE.toast(message, { kind: "bad", sticky: true,
        action: { label: "Try again", keep: true, run: () => RE.refresh() },
        onClose: () => { offlineToast = null; if (state.offline) offlineMuted = true; } });
    } else if (offlineToast) offlineToast.update(message);
    if (first) renderAssist();
  }
  function backOnline() {
    if (!state.offline) return;
    state.offline = false;
    offlineMuted = false;
    if (offlineToast) { offlineToast.close(); offlineToast = null; }
    RE.toast("Connected to the studio again.", { kind: "ok" });
  }

  function noticeCrashes() {
    for (const s of state.subjects) {
      const now = `${(s.engine && s.engine.state) || "stopped"}|${s.web ? s.web.state || "stopped" : ""}`;
      const before = lastEngine.get(s.slug);
      lastEngine.set(s.slug, now);
      if (before === now) continue;
      const crashed = (s.engine && s.engine.state === "crashed") && !(before || "").startsWith("crashed");
      const webCrashed = s.web && s.web.state === "crashed" && !(before || "").endsWith("|crashed");
      if (crashed || webCrashed) {
        const what = crashed ? "REA" : "The browser";
        const error = crashed ? s.engine.error : s.web.error;
        RE.toast(`${what} stopped unexpectedly for ${s.name}${error ? `: ${error}` : "."}`, { kind: "bad",
          action: { label: "Start again", run: () => openEngine(s.slug) } });
      }
    }
  }

  // ---------- Ask Claude ----------
  let assistOpen = RE.store.get("assistOpen", true) !== false;
  const assist = {
    slug: undefined, rev: undefined, seq: 0, messages: [], nodes: new Map(), empty: null,
    asking: false, flash: "", drafts: new Map(),
    activity: null, stepKeys: [], lastStepSaid: 0, said: 0,
  };

  function applyAssistOpen() {
    $("app").classList.toggle("no-assist", !assistOpen);
    setAttr($("hideAssist"), "aria-expanded", String(assistOpen));
    renderTop();
  }
  function toggleAssist(open = !assistOpen) {
    assistOpen = !!open;
    RE.store.set("assistOpen", assistOpen);
    applyAssistOpen();
    if (assistOpen && RE.visible($("askText"))) $("askText").focus();
    else if (!assistOpen && RE.visible($("showAssist"))) $("showAssist").focus();
  }
  function focusAsk() {
    if (state.page !== "subject" || !RE.current()) { RE.toast("Open a subject to ask Claude about it.", { kind: "info" }); return; }
    if (!assistOpen) toggleAssist(true);
    $("askText").focus();
  }

  function switchThread(slug) {
    const box = $("messages");
    if (assist.slug !== undefined && assist.slug) assist.drafts.set(assist.slug, $("askText").value);
    assist.slug = slug;
    assist.rev = undefined;
    assist.messages = [];
    assist.nodes.clear();
    assist.empty = null;
    assist.flash = "";
    if (box.contains(document.activeElement)) $("askText").focus({ preventScroll: true });
    put(box);
    if (assist.activity) { assist.activity.el.remove(); assist.activity = null; }
    assist.stepKeys = [];
    const draft = slug ? assist.drafts.get(slug) || "" : "";
    $("askText").value = draft;
    grow();
  }

  async function syncThread() {
    const s = state.page === "subject" ? RE.current() : null;
    const slug = s ? s.slug : null;
    if (slug !== assist.slug) switchThread(slug);
    if (!slug) { renderAssist(); return; }
    const rev = s.thread_rev !== undefined ? s.thread_rev : state.rev;
    if (rev === assist.rev) { renderAssist(); return; }
    assist.rev = rev;
    const seq = ++assist.seq;
    const reply = await RE.api(`/api/thread?slug=${enc(slug)}`);
    if (seq !== assist.seq || assist.slug !== slug) return;
    if (reply.error || !Array.isArray(reply.messages)) { assist.rev = undefined; return; }
    drawMessages(reply.messages);
    renderAssist();
  }

  const msgKey = (m, i) => m.id || `${i}|${m.role}|${m.at}`;
  function drawMessages(list) {
    const thread = $("thread");
    const box = $("messages");
    const nearBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 80;
    const first = assist.messages.length === 0 && !assist.empty;
    assist.messages = list;
    if (!list.length) {
      for (const node of assist.nodes.values()) node.remove();
      assist.nodes.clear();
      if (!assist.empty) { assist.empty = emptyHint(); box.append(assist.empty); }
      return;
    }
    if (assist.empty) {
      if (assist.empty.contains(document.activeElement)) $("askText").focus({ preventScroll: true });
      assist.empty.remove(); assist.empty = null;
    }
    const keys = list.map(msgKey);
    const keep = new Set(keys);
    for (const [key, node] of assist.nodes) {
      if (keep.has(key)) continue;
      if (node.contains(document.activeElement)) $("askText").focus({ preventScroll: true });
      node.remove();
      assist.nodes.delete(key);
    }
    let before = box.firstChild;
    list.forEach((m, i) => {
      const key = keys[i];
      let node = assist.nodes.get(key);
      if (!node) { node = messageEl(m); assist.nodes.set(key, node); }
      const prev = list[i - 1];
      node.classList.toggle("cont", !!prev && prev.role === "claude" && m.role === "claude");
      if (node !== before) box.insertBefore(node, before);
      else before = before.nextSibling;
    });
    if (first || nearBottom) thread.scrollTop = thread.scrollHeight;
  }

  function timeEl(at) {
    if (!at) return null;
    return h("time", { datetime: at, title: RE.fmt.full(at) }, RE.fmt.time(at));
  }
  function markdown(text) {
    if (RE.markdown) {
      try { return RE.markdown(text || ""); } catch (err) { console.error("Markdown failed:", err); }
    }
    return h("p", { class: "plain" }, text || "");
  }
  function messageEl(m) {
    if (m.role === "user") {
      const prompt = m.prompt && m.prompt.name ? h("div", { class: "msg-prompt" }, icon("flow", 13),
        h("span", null, "Workflow: ", h("b", null, m.prompt.title || m.prompt.name)),
        Object.entries(m.prompt.arguments || {}).filter(([, v]) => v !== "" && v !== null && v !== undefined)
          .map(([k, v]) => h("code", null, `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`))) : null;
      return h("article", { class: "msg user", "aria-label": "You" },
        h("div", { class: "msg-head" }, h("span", { class: "who" }, "You"), timeEl(m.at)),
        prompt, m.text ? h("div", { class: "bubble" }, m.text) : null);
    }
    if (m.role === "info") return h("div", { class: "msg info", role: "note" }, icon("info", 13), h("span", null, m.text), timeEl(m.at));
    const meta = m.meta ? h("div", { class: "msg-meta" }, m.meta) : null;
    if (m.role === "error") {
      return h("article", { class: "msg error", "aria-label": "Error" }, icon("alert", 16),
        h("div", { class: "msg-body" }, h("div", { class: "msg-head" }, h("b", null, "Something went wrong"), timeEl(m.at)),
          h("div", { class: "msg-text" }, m.text), stepsEl(m.steps), meta));
    }
    const copy = m.text ? RE.copyButton(() => m.text, "Copy Claude’s reply") : null;
    return h("article", { class: "msg claude", "aria-label": "Claude" },
      h("div", { class: "msg-head" }, h("span", { class: "avatar" }, icon("sparkle", 13)), h("span", { class: "who" }, "Claude"), timeEl(m.at),
        h("span", { class: "grow" }), copy),
      m.text ? h("div", { class: "msg-text md" }, markdown(m.text)) : null,
      stepsEl(m.steps), meta);
  }
  function stepsEl(steps) {
    if (!Array.isArray(steps) || !steps.length) return null;
    return h("details", { class: "steps" },
      h("summary", null, icon("caret", 13), "What Claude did", h("span", { class: "steps-count" }, `${steps.length}`)),
      h("ol", null, steps.map((s) => h("li", null, h("time", null, RE.fmt.time(s.at)),
        h("span", { class: "step-text" }, s.text || ""), s.detail ? h("code", { class: "step-detail" }, s.detail) : null))));
  }

  const STARTERS = [
    ["Give me an overview of this target.", "What it is, how it is built, and the obvious entry points."],
    ["List the interesting strings.", "URLs, keys, paths and messages worth a look."],
    ["How does <a feature> work?", "Name a feature you see; Claude traces it down to the code."],
  ];
  function emptyHint() {
    const s = RE.current();
    const workflows = h("div", { class: "suggests", hidden: true });
    const box = h("div", { class: "assist-empty" },
      h("div", { class: "assist-empty-mark" }, icon("sparkle", 22)),
      h("b", null, "Take it apart with Claude"),
      h("p", null, `Claude drives REA’s tools on ${s ? s.name : "this subject"}, as in a terminal session. Every result lands in Findings.`),
      h("div", { class: "suggests" }, h("div", { class: "suggest-label", id: "startersLabel" }, "Try asking"),
        h("ul", { class: "suggest-list", "aria-labelledby": "startersLabel" }, STARTERS.map(([title, sub]) => h("li", null,
          h("button", { class: "suggest", type: "button", onclick: () => fillAsk(title) }, h("span", null, title), h("small", null, sub)))))),
      workflows);
    RE.catalog().then((cat) => {
      const prompts = (cat && cat.prompts) || [];
      if (!prompts.length || !box.isConnected) return;
      put(workflows, h("div", { class: "suggest-label", id: "workflowsLabel" }, "REA workflows"),
        h("ul", { class: "suggest-list", "aria-labelledby": "workflowsLabel" }, prompts.slice(0, 4).map((p) => h("li", null,
          h("button", { class: "suggest", type: "button", onclick: () => runWorkflow(p) },
            h("span", null, icon("flow", 13), p.title || p.name), p.description ? h("small", null, p.description) : null)))),
        prompts.length > 4 ? h("button", { class: "btn ghost small", type: "button", onclick: chooseWorkflow }, `All ${prompts.length} workflows`) : null);
      workflows.hidden = false;
    });
    return box;
  }
  function fillAsk(text) {
    const box = $("askText");
    box.value = text;
    grow();
    box.focus();
    const at = text.indexOf("<");
    if (at >= 0) box.setSelectionRange(at, text.indexOf(">", at) + 1);
  }

  // The running job's steps, appended in place; the clock is not announced.
  function drawActivity() {
    const job = state.job;
    const mine = !!job && job.status === "running" && job.slug === assist.slug && !!assist.slug;
    if (!mine) {
      if (assist.activity) { assist.activity.el.remove(); assist.activity = null; assist.stepKeys = []; }
      return;
    }
    if (!assist.activity) {
      const clock = h("span", { class: "activity-time" });
      const bar = h("div", { class: "activity-bar", role: "progressbar", "aria-label": "Claude’s progress", hidden: true }, h("i"));
      const list = h("ol", { class: "activity-list" });
      const el = h("div", { class: "activity" },
        h("div", { class: "activity-head" }, h("i", { class: "pulse", "aria-hidden": "true" }), h("span", null, "Claude is working"), clock),
        bar, list);
      assist.activity = { el, clock, bar, list };
      assist.stepKeys = [];
      $("activity").append(el);
      const thread = $("thread");
      thread.scrollTop = thread.scrollHeight;
    }
    const a = assist.activity;
    setText(a.clock, RE.fmt.duration(job.seconds || 0));
    const p = job.progress;
    if (p && Number(p.total) > 0) {
      a.bar.hidden = false;
      const pct = Math.max(0, Math.min(100, Math.round((Number(p.progress) / Number(p.total)) * 100)));
      setAttr(a.bar, "aria-valuenow", pct);
      setAttr(a.bar, "aria-valuemin", 0);
      setAttr(a.bar, "aria-valuemax", 100);
      setAttr(a.bar, "aria-valuetext", p.message ? `${pct}% · ${p.message}` : `${pct}%`);
      a.bar.firstChild.style.width = pct + "%";
    } else a.bar.hidden = true;
    const steps = (job.activity || []).slice(-14);
    const keys = steps.map((x, i) => `${x.at}|${x.text}|${x.detail || ""}|${steps.slice(0, i).filter((y) => y.at === x.at && y.text === x.text).length}`);
    const thread = $("thread");
    const nearBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 80;
    // Drop steps that scrolled out of the server's window, then append the new ones.
    while (assist.stepKeys.length && !keys.includes(assist.stepKeys[0])) { assist.stepKeys.shift(); a.list.firstChild && a.list.firstChild.remove(); }
    let added = 0;
    keys.forEach((key, i) => {
      if (assist.stepKeys.includes(key)) return;
      assist.stepKeys.push(key);
      const x = steps[i];
      a.list.append(h("li", null, h("time", null, RE.fmt.time(x.at)), h("span", null, x.text), x.detail ? h("small", null, x.detail) : null));
      added++;
    });
    if (added && nearBottom) thread.scrollTop = thread.scrollHeight;
    // Say the newest step now and then, never every second.
    if (added && Date.now() - assist.lastStepSaid > 8000 && steps.length) {
      assist.lastStepSaid = Date.now();
      RE.announce(`Claude: ${steps[steps.length - 1].text}`);
    }
  }

  function noticeJob(was) {
    const job = state.job;
    const now = job ? { slug: job.slug, status: job.status } : null;
    const startedHere = now && now.status === "running" && (!was || was.status !== "running" || was.slug !== now.slug);
    if (startedHere && now.slug === assist.slug) { assist.lastStepSaid = Date.now(); RE.announce("Claude is working on it."); }
    const ended = was && was.status === "running" && (!now || now.status !== "running" || now.slug !== was.slug);
    if (!ended) return;
    if (was.slug !== assist.slug) {
      const s = state.subjects.find((x) => x.slug === was.slug);
      if (s) RE.announce(`Claude finished working on ${s.name}.`);
      return;
    }
    const last = assist.messages[assist.messages.length - 1];
    if (job && job.status === "stopped") RE.announce("Stopped.");
    else if (last && last.role === "error") RE.announce(`Claude stopped with an error: ${last.text}`, "assertive");
    else if (job && job.status === "error" && job.error) RE.announce(`Claude stopped with an error: ${job.error}`, "assertive");
    else if (last && last.role === "claude") {
      const first = String(last.text || "").replace(/[#*_`>|]/g, "").replace(/\s+/g, " ").trim().match(/^.{1,200}?[.!?](\s|$)|^.{1,200}/);
      RE.announce(`Claude replied: ${first ? first[0].trim() : ""}`);
    } else RE.announce("Claude finished.");
  }

  function blockReason() {
    const s = RE.current();
    const st = setupNow();
    const c = state.checks || {};
    if (state.offline) return "Not connected to the studio.";
    if (!s) return "Open a subject first.";
    if (!st.rea_ready) return "REA isn’t built yet, so Claude has no tools to use. See Settings.";
    if (st.claude_ready === false && c.claude && !c.checking) return "Claude Code isn’t signed in. See Settings.";
    return "";
  }
  function renderAssist() {
    const s = state.page === "subject" ? RE.current() : null;
    setText($("assistContext"), s ? s.name : "");
    drawActivity();
    const text = $("askText");
    const send = $("sendBtn");
    const stop = $("stopBtn");
    const why = blockReason();
    const runningHere = !!s && workingOn(s.slug);
    const runningElsewhere = busy() && !runningHere;
    const hold = !!why || assist.asking || busy();
    text.readOnly = !!why;
    setAttr(text, "aria-disabled", why ? "true" : null);
    setAttr(send, "aria-disabled", hold ? "true" : null);
    if (!runningHere && document.activeElement === stop) text.focus({ preventScroll: true });
    show(stop, runningHere);
    setAttr(text, "placeholder", s ? `Ask Claude about ${s.name}…` : "Ask Claude…");
    let hint = DEFAULT_HINT, tone = "";
    if (assist.flash) { hint = assist.flash; tone = "bad"; }
    else if (why) { hint = why; tone = "warn"; }
    else if (assist.asking) { hint = isWeb(s) ? `Starting the browser and REA for ${s.name}…` : `Starting REA for ${s.name}…`; tone = "busy"; }
    else if (runningHere) { hint = "Claude is working. Wait for the answer, or press Stop."; tone = "busy"; }
    else if (runningElsewhere) {
      const other = state.subjects.find((x) => x.slug === state.job.slug);
      hint = `Claude is working on ${other ? other.name : "another subject"}. One question at a time.`;
      tone = "busy";
    }
    const hintBox = $("askHint");
    setText(hintBox, hint);
    if (hintBox.dataset.tone !== tone) hintBox.dataset.tone = tone;
    const canClear = !!s && assist.messages.length > 0 && !runningHere;
    setAttr($("assistClear"), "aria-disabled", canClear ? null : "true");
    $("composer").classList.toggle("pending", assist.asking);
    show($("workflowBtn"), !!s && !!(state.catalog && state.catalog.ready));
  }

  function grow() {
    const box = $("askText");
    box.style.height = "auto";
    box.style.height = Math.min(box.scrollHeight, 240) + "px";
  }

  async function sendAsk(slug, body, { clearComposer = true } = {}) {
    if (assist.asking) return false;
    assist.asking = true;
    assist.flash = "";
    renderAssist();
    const reply = await RE.post("/api/ask", { slug, ...body });
    assist.asking = false;
    if (reply.error) {
      assist.flash = reply.error;
      RE.announce(reply.error, "assertive");
    } else {
      if (clearComposer) { $("askText").value = ""; assist.drafts.delete(slug); grow(); }
      RE.announce("Sent. Claude is working on it.");
    }
    renderAssist();
    await RE.refresh();
    return !reply.error;
  }

  async function submitAsk(e) {
    e.preventDefault();
    const s = RE.current();
    const text = $("askText").value.trim();
    if (!text || !s) return;
    const why = blockReason();
    if (why) { RE.announce(why, "assertive"); return; }
    if (busy()) { RE.announce("Claude is still working. Wait for the answer, or press Stop.", "assertive"); return; }
    await sendAsk(s.slug, { text });
  }

  async function chooseWorkflow() {
    const s = RE.current();
    if (!s) return;
    const cat = await RE.catalog();
    const prompts = (cat && cat.prompts) || [];
    if (!prompts.length) { RE.toast(cat && cat.ready ? "REA lists no workflows." : "REA isn’t built yet, so it has no workflows.", { kind: "info" }); return; }
    let d = null;
    const list = h("ul", { class: "pick-list" }, prompts.map((p) => h("li", null,
      h("button", { class: "pick", type: "button", onclick: () => { d.close(); runWorkflow(p); } },
        h("span", { class: "pick-title" }, icon("flow", 14), p.title || p.name),
        p.description ? h("small", null, p.description) : null))));
    d = RE.dialog.open({ title: "Start an REA workflow", wide: true,
      body: [h("p", { class: "modal-text" }, `A workflow is one of REA’s own step-by-step investigations. Claude follows it on ${s.name}.`), list] });
  }
  async function runWorkflow(prompt, given = {}) {
    const s = RE.current();
    if (!s) return;
    if (!RE.forms || !RE.forms.promptDialog) { RE.toast("Workflows could not load. Reload the window to try again.", { kind: "bad" }); return; }
    const why = blockReason();
    if (why) { RE.toast(why, { kind: "bad" }); return; }
    const prefill = { ...given };
    const names = (prompt.arguments || []).map((a) => a.name);
    if (names.includes("target_path") && s.path && !isWeb(s)) prefill.target_path = s.path;
    let picked = null;
    try { picked = await RE.forms.promptDialog(prompt, { slug: s.slug, prefill }); } catch (err) { console.error(err); }
    if (!picked) return;
    if (busy()) { RE.toast("Claude is still working. Wait for the answer, or press Stop.", { kind: "info" }); return; }
    const typed = $("askText").value.trim();
    const text = typed || `Follow the REA workflow “${prompt.title || prompt.name}”.`;
    await sendAsk(s.slug, { text, prompt: picked }, { clearComposer: !!typed });
  }

  async function clearConversation() {
    const s = RE.current();
    if (!s || $("assistClear").getAttribute("aria-disabled") === "true") return;
    const ok = await RE.confirm({ title: "Clear this conversation?", danger: true, confirmLabel: "Clear conversation",
      message: `Claude’s conversation about ${s.name} is forgotten. Findings stay as they are; clear those from the Findings panel.` });
    if (!ok) return;
    const reply = await RE.post("/api/clear", { slug: s.slug });
    if (reply.error) RE.toast(reply.error, { kind: "bad" });
    else RE.announce("Conversation cleared.");
    await RE.refresh();
    $("askText").focus();
  }

  async function stopRun() {
    if (!busy()) { RE.toast("Claude isn’t working on anything.", { kind: "info" }); return; }
    const reply = await RE.post("/api/stop", {});
    if (reply.error) RE.toast(reply.error, { kind: "bad" });
    else RE.announce("Stopping Claude…");
    await RE.refresh();
  }

  // ---------- the Ask panel's width ----------
  const ASSIST_MIN = 320, ASSIST_DEFAULT = 400;
  const assistMax = () => Math.max(ASSIST_MIN, Math.min(760, Math.round(window.innerWidth * 0.5)));
  function setAssistWidth(px, save = true) {
    const w = Math.round(Math.max(ASSIST_MIN, Math.min(assistMax(), Number(px) || ASSIST_DEFAULT)));
    $("app").style.setProperty("--assist-w", w + "px");
    const handle = $("assistResize");
    setAttr(handle, "aria-valuemin", ASSIST_MIN);
    setAttr(handle, "aria-valuemax", assistMax());
    setAttr(handle, "aria-valuenow", w);
    setAttr(handle, "aria-valuetext", `${w} pixels wide`);
    if (save) RE.store.set("assistWidth", w);
    return w;
  }
  function wireResize() {
    const handle = $("assistResize");
    let dragging = false;
    handle.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      dragging = true;
      handle.setPointerCapture(e.pointerId);
      document.body.classList.add("resizing");
      e.preventDefault();
    });
    handle.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const right = $("assist").getBoundingClientRect().right;
      setAssistWidth(right - e.clientX, false);
    });
    const end = (e) => {
      if (!dragging) return;
      dragging = false;
      try { handle.releasePointerCapture(e.pointerId); } catch {}
      document.body.classList.remove("resizing");
      RE.store.set("assistWidth", Number(handle.getAttribute("aria-valuenow")));
    };
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
    handle.addEventListener("dblclick", () => setAssistWidth(ASSIST_DEFAULT));
    handle.addEventListener("keydown", (e) => {
      const now = Number(handle.getAttribute("aria-valuenow")) || ASSIST_DEFAULT;
      const step = e.shiftKey ? 64 : 16;
      const next = { ArrowLeft: now + step, ArrowRight: now - step, Home: ASSIST_MIN, End: assistMax() }[e.key];
      if (next === undefined) return;
      e.preventDefault();
      setAssistWidth(next);
    });
    window.addEventListener("resize", () => setAssistWidth(RE.store.get("assistWidth", ASSIST_DEFAULT), false));
    setAssistWidth(RE.store.get("assistWidth", ASSIST_DEFAULT), false);
  }

  // ---------- form fields for the dialogs ----------
  function field({ id, label, control, hint, optional = false }) {
    control.id = id;
    const hintId = `${id}-hint`, errId = `${id}-err`;
    const hintEl = h("span", { class: "field-hint", id: hintId, hidden: !hint }, hint || "");
    const err = h("span", { class: "field-error", id: errId, hidden: true });
    const wrap = h("div", { class: "field wide" },
      h("label", { class: "field-label", for: id }, label, optional ? h("span", { class: "opt" }, " (optional)") : null),
      control, hintEl, err);
    const describe = () => setAttr(control, "aria-describedby", [err.hidden ? null : errId, hintEl.hidden ? null : hintId].filter(Boolean).join(" ") || null);
    describe();
    return {
      el: wrap, control,
      hint(text) { hintEl.textContent = text || ""; hintEl.hidden = !text; describe(); },
      error(text) {
        err.textContent = text || ""; err.hidden = !text;
        setAttr(control, "aria-invalid", text ? "true" : null);
        describe();
      },
    };
  }

  const KIND_ORDER = ["executable", "archive", "database", "artifact", "website"];
  const KIND_HELP = {
    executable: "EXE, DLL, SYS, ELF, Mach-O, .so, .dylib…",
    archive: "APK, IPA, AAB, ASAR, MSIX, JAR, ZIP…",
    database: "A saved REA, Ghidra, Hopper, IDA or Binary Ninja project",
    artifact: "A capture, firmware image, dump, model or other file",
    website: "A live site, observed in a browser the studio controls",
  };
  const EXT_KIND = {};
  for (const e of "apk ipa aab xapk apks zip asar msix msixbundle appx appxbundle jar war ear nupkg crx xpi vsix".split(" ")) EXT_KIND[e] = "archive";
  for (const e of "exe dll sys so dylib elf o ko efi scr ocx cpl drv mui node axf out".split(" ")) EXT_KIND[e] = "executable";
  for (const e of "gpr rep hop i64 idb bndb rzdb rea".split(" ")) EXT_KIND[e] = "database";
  for (const e of "pcap pcapng sr bin img fw hex dmp mdmp onnx tflite pt gguf sqlite db dol com".split(" ")) EXT_KIND[e] = "artifact";
  const FORMATS = [["", "Detect from the file’s header (usual)"], ["raw-binary", "Raw binary: headerless bytes"],
    ["dos-com", "DOS COM program"], ["gamecube-dol", "GameCube or Wii DOL"]];
  const ARCHES = ["x86", "x86_64", "arm", "arm64", "6502", "m68k"];

  function cleanPath(text) {
    let t = String(text || "").trim();
    const m = t.match(/^(["'])(.*)\1$/);
    if (m) t = m[2].trim();
    return t;
  }
  const looksLikeUrl = (t) => /^\s*["']?(https?:\/\/|www\.)/i.test(t);
  function baseName(p) {
    const file = cleanPath(p).split(/[\\/]/).filter(Boolean).pop() || "";
    const dot = file.lastIndexOf(".");
    return (dot > 0 ? file.slice(0, dot) : file).slice(0, 120);
  }
  function hostOf(u) {
    try { return new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(u) ? u : `https://${u}`).hostname; } catch { return ""; }
  }

  // ---------- new subject ----------
  let newDialog = null;
  function openNewSubject() {
    if (newDialog) { const first = RE.focusables(newDialog.el)[0]; if (first) first.focus(); return; }
    const kind = h("select", { class: "input" }, KIND_ORDER.map((k) => h("option", { value: k }, KINDS[k].label)));
    const path = h("input", { class: "input mono", type: "text", autocomplete: "off", spellcheck: "false", placeholder: "C:\\Users\\you\\Downloads\\app.apk" });
    const url = h("input", { class: "input mono", type: "text", inputmode: "url", autocomplete: "off", spellcheck: "false", placeholder: "https://example.com" });
    const name = h("input", { class: "input", type: "text", maxlength: 120, autocomplete: "off", placeholder: "Filled from the file name" });
    const fmt = h("select", { class: "input" }, FORMATS.map(([v, l]) => h("option", { value: v }, l)));
    const arch = h("select", { class: "input" }, h("option", { value: "" }, "Choose the CPU family"), ARCHES.map((a) => h("option", { value: a }, a)));
    const entry = h("input", { class: "input mono", type: "text", autocomplete: "off", spellcheck: "false", placeholder: "0x1000" });

    const pathF = field({ id: "ns-path", label: "Full path on this PC", control: path,
      hint: "Paste it as Explorer’s “Copy as path” gives it; quotes are fine. The file stays where it is and REA reads it there." });
    const urlF = field({ id: "ns-url", label: "Website address", control: url,
      hint: "The studio opens this in a browser it controls so REA can observe it. Observing a site loads it over the network, like opening it yourself." });
    const kindF = field({ id: "ns-kind", label: "Kind", control: kind, hint: KIND_HELP.executable });
    const nameF = field({ id: "ns-name", label: "Name", control: name, optional: true, hint: "How it shows in the sidebar." });
    const fmtF = field({ id: "ns-format", label: "Format", control: fmt, hint: "Leave this unless REA can’t tell what the file is." });
    const archF = field({ id: "ns-arch", label: "CPU family", control: arch, hint: "Raw binaries need it." });
    const entryF = field({ id: "ns-entry", label: "Entry / load address", control: entry, hint: "In hex. File offset 0 is mapped here." });
    const advanced = h("details", { class: "advanced" }, h("summary", null, icon("caret", 13), "Advanced: how REA reads the file"),
      h("div", { class: "fields" }, fmtF.el, archF.el, entryF.el));
    const problem = h("div", { class: "form-error", role: "alert", hidden: true });
    const make = h("button", { class: "btn primary", type: "submit" }, icon("plus", 15), h("span", null, "Add subject"));
    const cancel = h("button", { class: "btn", type: "button", onclick: () => newDialog && newDialog.close() }, "Cancel");
    const form = h("form", { class: "form", novalidate: true },
      h("div", { class: "fields" }, pathF.el, urlF.el, kindF.el, nameF.el), advanced, problem,
      h("div", { class: "modal-foot" }, cancel, make));

    let kindTouched = false, nameTouched = false;
    const sync = () => {
      const k = kind.value;
      const web = k === "website";
      const plain = web || k === "database";
      pathF.el.hidden = web; path.disabled = web;
      urlF.el.hidden = !web; url.disabled = !web;
      advanced.hidden = plain; fmt.disabled = plain;
      const raw = !plain && fmt.value === "raw-binary";
      const dol = !plain && fmt.value === "gamecube-dol";
      archF.el.hidden = !raw; arch.disabled = !raw;
      entryF.el.hidden = !(raw || dol); entry.disabled = !(raw || dol);
      entryF.hint(raw ? "Required for a raw binary, in hex, like 0x1000. File offset 0 is mapped here."
        : "Optional for a GameCube DOL; if given, it must equal the entry in the DOL header (hex, like 0x80003100).");
      kindF.hint(!kindTouched && kind.dataset.guessed ? `Guessed from the file’s extension; change it if it’s wrong. ${KIND_HELP[k]}` : KIND_HELP[k]);
      setAttr(name, "placeholder", web ? "Filled from the address" : "Filled from the file name");
    };
    const fillName = () => {
      if (nameTouched) return;
      name.value = kind.value === "website" ? hostOf(url.value.trim()) : baseName(path.value);
    };
    // A server error stays only until the user changes something it may be about.
    const clearProblem = () => { problem.hidden = true; };
    kind.addEventListener("change", () => { kindTouched = true; delete kind.dataset.guessed; clearProblem(); sync(); fillName(); });
    fmt.addEventListener("change", () => { clearProblem(); fmtF.error(""); sync(); });
    name.addEventListener("input", () => { nameTouched = name.value.trim() !== ""; nameF.error(""); });
    path.addEventListener("input", () => {
      pathF.error("");
      clearProblem();
      if (looksLikeUrl(path.value)) {   // a web address in the path box: this is a website subject
        url.value = cleanPath(path.value);
        path.value = "";
        kind.value = "website";
        kindTouched = true;
        sync();
        fillName();
        url.focus();
        url.setSelectionRange(url.value.length, url.value.length);
        RE.announce("That’s a web address, so the kind is now Website.");
        return;
      }
      const ext = (cleanPath(path.value).match(/\.([a-z0-9]+)$/i) || [])[1];
      const guess = ext && EXT_KIND[ext.toLowerCase()];
      if (guess && !kindTouched && kind.value !== guess) { kind.value = guess; kind.dataset.guessed = "1"; sync(); }
      fillName();
    });
    path.addEventListener("blur", () => { const c = cleanPath(path.value); if (c !== path.value) path.value = c; });
    url.addEventListener("input", () => { urlF.error(""); clearProblem(); fillName(); });
    arch.addEventListener("change", () => { archF.error(""); clearProblem(); });
    entry.addEventListener("input", () => { entryF.error(""); clearProblem(); });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (make.getAttribute("aria-disabled") === "true") return;
      for (const f of [pathF, urlF, archF, entryF, nameF, fmtF, kindF]) f.error("");
      problem.hidden = true;
      const k = kind.value;
      const body = { kind: k, name: name.value.trim() };
      let bad = null;
      if (k === "website") {
        let u = url.value.trim();
        if (!u) bad = [urlF, "Give the website’s address."];
        else {
          if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(u)) u = `https://${u}`;
          let parsed = null;
          try { parsed = new URL(u); } catch {}
          if (!parsed || !parsed.hostname) bad = [urlF, "That isn’t a web address. Try one like https://example.com."];
          else if (!/^https?:$/.test(parsed.protocol)) bad = [urlF, "Only http and https addresses can be observed."];
          else if (parsed.username || parsed.password) bad = [urlF, "Leave the user name and password out of the address."];
          url.value = u;
        }
        body.url = u;
        if (!body.name) body.name = hostOf(u);
      } else {
        const p = cleanPath(path.value);
        path.value = p;
        if (!p) bad = [pathF, "Give the file’s full path."];
        else if (/^(\\\\|\/\/)/.test(p)) bad = [pathF, "Network paths aren’t supported. Copy the file to this PC first."];
        else if (!/^[a-zA-Z]:[\\/]/.test(p) && !p.startsWith("/")) bad = [pathF, "Give the full path, starting with the drive letter, like C:\\Users\\you\\app.exe."];
        body.path = p;
        if (!body.name) body.name = baseName(p);
        if (k !== "database" && fmt.value) {
          body.format = fmt.value;
          const ep = entry.value.trim();
          if (fmt.value === "raw-binary") {
            if (!arch.value) bad = bad || [archF, "Choose the CPU family the bytes are for."];
            if (!/^0x[0-9a-f]{1,16}$/i.test(ep)) bad = bad || [entryF, "Give the address in hex, like 0x1000."];
            body.architecture = arch.value;
            body.entry_point = ep;
          } else if (fmt.value === "gamecube-dol" && ep) {
            if (!/^0x[0-9a-f]{1,16}$/i.test(ep)) bad = bad || [entryF, "Give the address in hex, like 0x80003100, or leave it empty."];
            body.entry_point = ep;
          }
        }
      }
      if (bad) {
        const [f, message] = bad;
        if (advanced.contains(f.el)) advanced.open = true;
        f.error(message);
        f.control.focus();
        return;
      }
      make.setAttribute("aria-disabled", "true");
      put(make, icon("refresh", 15), h("span", null, "Adding…"));
      const reply = await RE.post("/api/subjects/new", body);
      make.removeAttribute("aria-disabled");
      put(make, icon("plus", 15), h("span", null, "Add subject"));
      if (reply.problems || reply.error || !reply.slug) {
        const lines = reply.problems || [reply.error || "The subject could not be added."];
        // The server names the input at fault when it can: the error goes on that field.
        const at = { path: pathF, url: urlF, kind: kindF, name: nameF, format: fmtF, architecture: archF, entry_point: entryF }[reply.field];
        if (at && !at.el.hidden) {
          if (advanced.contains(at.el)) advanced.open = true;
          at.error(lines.join(" "));
          at.control.focus();
          return;
        }
        put(problem, icon("alert", 16), h("div", null, lines.map((l) => h("div", null, l))));
        problem.hidden = false;
        return;
      }
      await RE.refresh();
      const slug = reply.slug;
      RE.toast(`Added ${body.name || "the subject"}.`, { kind: "ok" });
      newTarget = `#${enc(slug)}`;   // the dialog's entry becomes the new subject's page
      if (newDialog) newDialog.close();
      else go(enc(slug));
    });

    sync();
    newDialog = RE.dialog.open({
      title: "New subject", body: form, wide: true,
      initialFocus: path,
      onClose: () => {
        newDialog = null;
        const pushed = newPushed, target = newTarget;
        newPushed = false; newTarget = "";
        if (location.hash !== "#~new") return;
        if (target) { history.replaceState(null, "", target); route(true); return; }   // the new subject
        // Opened from inside the studio: step back off the entry the dialog pushed, so Back is not
        // spent on a copy of this page. A deep link to #~new has nothing to go back to here.
        if (pushed) { skipRoute = backHash || ""; history.back(); }
        else history.replaceState(null, "", backHash || location.pathname + location.search);
      },
    });
  }

  // ---------- one subject: rename, remove ----------
  function openSubjectDialog() {
    const s = RE.current();
    if (!s) return;
    const name = h("input", { class: "input", type: "text", value: s.name, maxlength: 120, autocomplete: "off" });
    const nameF = field({ id: "sd-name", label: "Name", control: name });
    const where = isWeb(s) ? s.url : s.path;
    const target = h("div", { class: "field wide" }, h("span", { class: "field-label", id: "sd-target-label" }, isWeb(s) ? "Website address" : "Target file"),
      h("div", { class: "readonly", "aria-labelledby": "sd-target-label", role: "group" }, h("code", null, where || "—"),
        where ? RE.copyButton(where, isWeb(s) ? "Copy the address" : "Copy the path") : null));
    const save = h("button", { class: "btn primary", type: "submit" }, "Save name");
    let d = null;
    const form = h("form", { class: "form", novalidate: true }, h("div", { class: "fields" }, nameF.el, target),
      h("div", { class: "modal-foot" }, h("button", { class: "btn", type: "button", onclick: () => d.close() }, "Close"), save));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const next = name.value.trim();
      if (!next) { nameF.error("Give it a name."); name.focus(); return; }
      if (next === s.name) { d.close(); return; }
      save.setAttribute("aria-disabled", "true");
      const reply = await RE.post("/api/rename", { slug: s.slug, name: next });
      save.removeAttribute("aria-disabled");
      if (reply.error || reply.problems) { nameF.error(reply.error || reply.problems.join(" ")); name.focus(); return; }
      d.close();
      RE.toast(`Renamed to ${next}.`, { kind: "ok" });
      RE.refresh();
    });
    name.addEventListener("input", () => nameF.error(""));
    const remove = h("button", { class: "btn danger", type: "button" }, icon("trash", 14), h("span", null, "Remove subject"));
    remove.addEventListener("click", async () => {
      const ok = await RE.confirm({ title: `Remove ${s.name}?`, danger: true, confirmLabel: "Remove subject",
        message: `This forgets ${s.name} here: its conversation, its findings, its work folder (REA’s outputs and exports)${isWeb(s) ? " and its browser profile" : ""}. The ${isWeb(s) ? "website" : "target file"} itself is not touched.` });
      if (!ok) return;
      const reply = await RE.post("/api/remove", { slug: s.slug });
      if (reply.error) { RE.toast(reply.error, { kind: "bad" }); return; }
      if (RE.store.get("lastSlug", null) === s.slug) RE.store.set("lastSlug", undefined);
      d.close();
      RE.toast(`Removed ${s.name}.`, { kind: "ok" });
      await RE.refresh();
      rescueFocus();
    });
    const danger = h("div", { class: "danger-zone" },
      h("div", null, h("b", null, "Remove this subject"),
        h("p", null, "Forgets its conversation, findings and work folder on this PC. The target itself is left as it is.")), remove);
    d = RE.dialog.open({ title: "Subject settings", body: [form, danger], initialFocus: name });
  }

  // ---------- theme ----------
  let themeButtons = [];
  function themeSync() {
    const dark = window.studioTheme.resolved() === "dark";
    const btn = $("themeBtn");
    const label = dark ? "Switch to light theme" : "Switch to dark theme";
    const key = dark ? "sun" : "moon";
    if (btn.dataset.icon !== key) { btn.dataset.icon = key; put(btn, icon(key, 16)); }
    setAttr(btn, "aria-label", label);
    setAttr(btn, "title", label);
    const choice = window.studioTheme.get();
    for (const b of themeButtons) setAttr(b, "aria-pressed", String(b.dataset.value === choice));
  }
  async function setTheme(choice) {
    window.studioTheme.set(choice);
    const reply = await RE.post("/api/settings", { values: { theme: choice } });
    if (reply.error || reply.problems) RE.toast(`The theme was changed here but not saved: ${reply.error || reply.problems.join(" ")}`, { kind: "bad" });
  }
  function toggleTheme() {
    const next = window.studioTheme.resolved() === "dark" ? "light" : "dark";
    setTheme(next);
    RE.announce(`${next === "dark" ? "Dark" : "Light"} theme.`);
  }
  // Passed to the Settings page; built once so its buttons keep focus when pressed.
  function appearanceSection() {
    const choices = [["auto", "Match the system"], ["light", "Light"], ["dark", "Dark"]];
    themeButtons = choices.map(([value, label]) => h("button", { class: "btn", type: "button", "data-value": value,
      "aria-pressed": String(window.studioTheme.get() === value), onclick: () => setTheme(value) }, label));
    return h("section", { class: "panel", id: "set-appearance", "aria-labelledby": "set-appearance-title" },
      h("header", { class: "panel-head" }, h("span", { class: "panel-mark" }, icon("moon", 17)),
        h("div", null, h("h2", { id: "set-appearance-title" }, "Appearance"), h("p", null, "The studio’s own look. Saved for this PC."))),
      h("div", { class: "form" }, h("div", { class: "segmented", role: "group", "aria-label": "Theme" }, themeButtons)));
  }

  // ---------- shortcuts ----------
  const SHORTCUTS = [
    ["Ctrl+K", "Search subjects, pages, tools and actions"],
    ["Alt+N", "New subject"],
    ["Ctrl+/", "Ask Claude about the open subject"],
    ["Alt+↑ / Alt+↓", "Previous or next subject"],
    ["Enter", "Send your question (in the Ask box)"],
    ["Shift+Enter", "New line (in the Ask box)"],
    ["↑", "Bring back your last question (in an empty Ask box)"],
    ["?", "This list"],
  ];
  function showShortcuts() {
    const table = h("table", { class: "keys" },
      h("caption", { class: "sr-only" }, "Keyboard shortcuts"),
      h("thead", null, h("tr", null, h("th", { scope: "col" }, "Keys"), h("th", { scope: "col" }, "What it does"))),
      h("tbody", null, SHORTCUTS.map(([keys, what]) => h("tr", null,
        h("td", null, keys.split(" / ").map((k, i) => [i ? " or " : null, ...k.split("+").map((part, j) => [j ? "+" : null, h("kbd", null, part)])])),
        h("td", null, what)))));
    const done = h("button", { class: "btn", type: "button" }, "Close");
    const d = RE.dialog.open({ title: "Keyboard shortcuts", body: table, actions: [done], initialFocus: done });
    done.addEventListener("click", () => d.close());
  }
  function stepSubject(dir) {
    if (!state.subjects.length) return;
    const at = state.subjects.findIndex((s) => s.slug === state.slug);
    const next = state.subjects[(at + dir + state.subjects.length) % state.subjects.length];
    if (next) go(enc(next.slug));
  }
  document.addEventListener("keydown", (e) => {
    if (e.defaultPrevented || e.isComposing) return;
    const mod = e.ctrlKey || e.metaKey;
    const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if (mod && !e.altKey && !e.shiftKey && (key === "k" || e.code === "KeyK")) {
      e.preventDefault();
      if (RE.palette) RE.palette.open();
      return;
    }
    if (dialogOpen()) return;
    if (mod && !e.altKey && (key === "/" || e.code === "Slash")) { e.preventDefault(); focusAsk(); return; }
    if (e.altKey && !mod && !e.shiftKey && (key === "n" || e.code === "KeyN")) { e.preventDefault(); go("~new"); return; }
    if (e.altKey && !mod && (e.key === "ArrowUp" || e.key === "ArrowDown")) { e.preventDefault(); stepSubject(e.key === "ArrowUp" ? -1 : 1); return; }
    if (!mod && !e.altKey && e.key === "?" && !isTyping(e.target)) { e.preventDefault(); showShortcuts(); }
  });

  // Actions other modules (the palette) can call.
  RE.shell = { newSubject: () => go("~new"), toggleTheme, toggleAssist: () => toggleAssist(), focusAsk, stopRun, showShortcuts,
    openEngine, closeEngine, chooseWorkflow, runWorkflow, assistOpen: () => assistOpen };

  // ---------- wire up ----------
  for (const [id, name, size] of [
    ["newSubject", "plus", 16], ["emptyNew", "plus", 16], ["showAssist", "sparkle", 15], ["assistMark", "sparkle", 17],
    ["emptyMark", "target", 28], ["assistClear", "trash", 15], ["hideAssist", "panel", 15], ["sendBtn", "send", 16],
    ["stopBtn", "stop", 13], ["sideNote", "lock", 13], ["openTools", "tools", 17], ["openSettings", "settings", 17],
    ["subjectSettings", "gear", 17], ["setupFoot", "alert", 14], ["openPalette", "search", 15], ["shortcutsBtn", "keyboard", 16],
    ["workflowBtn", "flow", 14],
  ]) { const el = $(id); if (el) el.prepend(icon(name, size)); }

  $("newSubject").addEventListener("click", () => go("~new"));
  $("emptyNew").addEventListener("click", () => go("~new"));
  $("openTools").addEventListener("click", () => go("~tools"));
  $("openSettings").addEventListener("click", () => go("~settings"));
  $("setupFoot").addEventListener("click", () => go("~settings"));
  $("openPalette").addEventListener("click", () => RE.palette && RE.palette.open());
  $("shortcutsBtn").addEventListener("click", showShortcuts);
  $("subjectSettings").addEventListener("click", openSubjectDialog);
  $("engineBtn").addEventListener("click", () => {
    const s = RE.current();
    const btn = $("engineBtn");
    if (!s) return;
    if (btn.getAttribute("aria-disabled") === "true") { RE.announce(btn.title || "Not now.", "assertive"); return; }
    if (btn.dataset.action === "close") closeEngine(s.slug, "all"); else openEngine(s.slug);
  });
  $("showAssist").addEventListener("click", () => toggleAssist(true));
  $("hideAssist").addEventListener("click", () => toggleAssist(false));
  $("assistClear").addEventListener("click", clearConversation);
  $("workflowBtn").addEventListener("click", chooseWorkflow);
  $("themeBtn").addEventListener("click", toggleTheme);
  $("stopBtn").addEventListener("click", stopRun);
  $("askForm").addEventListener("submit", submitAsk);
  const askText = $("askText");
  askText.addEventListener("input", () => {
    grow();
    if (assist.flash) { assist.flash = ""; renderAssist(); }
  });
  askText.addEventListener("keydown", (e) => {
    if (e.isComposing) return;
    if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey) {
      e.preventDefault();
      if ($("sendBtn").getAttribute("aria-disabled") !== "true") $("askForm").requestSubmit();
      else if (askText.value.trim()) RE.announce(blockReason() || (assist.asking ? "Still sending." : "Claude is still working. Wait for the answer, or press Stop."), "assertive");
      return;
    }
    if (e.key === "ArrowUp" && !e.shiftKey && !e.altKey && !e.ctrlKey && !askText.value) {
      const last = [...assist.messages].reverse().find((m) => m.role === "user" && m.text);
      if (last) { e.preventDefault(); askText.value = last.text; grow(); askText.setSelectionRange(last.text.length, last.text.length); }
    }
  });
  $("sendBtn").addEventListener("click", (e) => {
    if ($("sendBtn").getAttribute("aria-disabled") === "true") {
      e.preventDefault();
      RE.announce(blockReason() || (assist.asking ? "Still sending." : "Claude is still working. Wait for the answer, or press Stop."), "assertive");
    }
  });
  // Skip links move focus without touching the address (the hash is the router's).
  $("skip").addEventListener("click", (e) => { e.preventDefault(); $("main").focus(); });
  $("skipAsk").addEventListener("click", (e) => { e.preventDefault(); focusAsk(); });

  window.studioTheme.onChange(themeSync);
  themeSync();
  applyAssistOpen();
  wireResize();
  refresh();
})();
