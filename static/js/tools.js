/* The Tools page (RE.tools): every REA tool, searchable, each runnable from a form built from
   its own input schema (RE.forms.build). Built once and kept, so filled-in forms and results
   survive leaving the page and coming back. Routes: #~tools, and #~tools/<name> to open a tool.

   RE.tools.render(container)   build on first call, then sync the subject picker and the route
   RE.tools.openTool(name)      go to the Tools page with that tool open
*/
(() => {
  "use strict";
  const RE = (window.RE = window.RE || {});
  const h = (...args) => RE.h(...args);

  const KEEP_RESULTS = 5;          // per tool
  const RECENT_MAX = 12;
  const STORE = { fav: "tools.favourites", recent: "tools.recent", view: "tools.view", filters: "tools.filters" };

  // Examples for compact-profile object inputs whose schema names no properties. From REA's
  // docs (mcp-contracts.md): a retained Evidence reference and the summary view.
  const RETAINED = { kind: "retained-evidence", evidence_id: "" };
  const TEMPLATES = {
    inspect_analysis_view: { source: RETAINED, view: { kind: "summary" } },
    compare_functions: { left: RETAINED, right: RETAINED },
    match_native_functions: { left: RETAINED, right: RETAINED },
  };

  const T = {
    container: null, root: null, loading: null, ready: false,
    items: new Map(), groups: [], view: "all", viewBeforeSearch: null, searching: false, query: "",
    filters: { safe: false, closed: false }, slug: null, lastGlobal: undefined, subjectsSig: "",
    els: {},
  };

  RE.addIcons && RE.addIcons({
    star: ["M12 3.6l2.5 5.2 5.7.8-4.1 4 1 5.7L12 16.6l-5.1 2.7 1-5.7-4.1-4 5.7-.8z"],
  });

  // ---------- small helpers ----------
  const store = (key, fallback) => { try { return RE.store.get(key, fallback); } catch { return fallback; } };
  const save = (key, value) => { try { RE.store.set(key, value); } catch { /* per-viewer convenience only */ } };
  const subjects = () => (RE.state && Array.isArray(RE.state.subjects) ? RE.state.subjects : []);
  const subjectOf = (slug) => subjects().find((s) => s.slug === slug) || null;
  const icon = (name, size) => (RE.icon ? RE.icon(name, size) : null);
  const safeId = (text) => String(text).replace(/[^A-Za-z0-9_-]/g, "_");
  const flag = (tool, key, hint) => (typeof tool[key] === "boolean" ? tool[key] : !!(tool.annotations && tool.annotations[hint]));
  const isReadOnly = (t) => flag(t, "read_only", "readOnlyHint");
  const isDestructive = (t) => flag(t, "destructive", "destructiveHint");
  const isOpenWorld = (t) => flag(t, "open_world", "openWorldHint");
  function seconds(ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    if (RE.fmt && RE.fmt.duration) return RE.fmt.duration(s);
    return s >= 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s} s`;
  }
  function onToolsPage() { return !!(T.container && !T.container.hidden && T.root && T.container.contains(T.root)); }
  let announceTimer = 0;
  function announceSoon(text) {
    clearTimeout(announceTimer);
    announceTimer = setTimeout(() => RE.announce && RE.announce(text, "polite"), 700);
  }

  // ---------- render ----------
  async function render(container) {
    T.container = container;
    if (T.ready && T.root && container.contains(T.root)) { syncSubject(); applyRoute(); return; }
    if (!T.loading) T.loading = load(container).finally(() => { T.loading = null; });
    await T.loading;
    if (T.ready) { syncSubject(); applyRoute(); }
  }

  async function load(container) {
    if (!T.root || !container.contains(T.root)) {
      RE.put(container, h("p", { class: "lede tl-loading", role: "status" }, "Loading tools…"));
    }
    let cat;
    try { cat = await RE.catalog(); } catch { cat = null; }
    if (!cat || !cat.ready || !Array.isArray(cat.categories)) {
      const notBuilt = cat && !cat.error;
      RE.put(container, h("div", { class: "empty tl-not-ready" },
        icon("tools", 30),
        h("h2", null, notBuilt ? "REA isn’t built yet" : "The tool list didn’t load"),
        h("p", null, notBuilt
          ? "Build REA once in its own checkout (npm ci, then npm run build:cached with Node 24), then come back here. Settings shows where the studio looks for it."
          : "The studio did not answer. Check that it is still running, then open this page again."),
        h("button", { class: "btn", type: "button", onclick: () => RE.go("#~settings") }, "Open Settings")));
      T.root = null;
      return;
    }
    build(container, cat);
    T.ready = true;
  }

  function build(container, cat) {
    T.view = store(STORE.view, "all");
    const f = store(STORE.filters, {});
    T.filters = { safe: !!(f && f.safe), closed: !!(f && f.closed) };
    const total = cat.categories.reduce((n, c) => n + (c.tools || []).length, 0);

    // subject bar
    const picker = h("select", { class: "input tl-picker", id: "tlSubject", "aria-describedby": "tlSubjectState" });
    picker.addEventListener("change", () => setSubject(picker.value || null));
    const subjectState = h("span", { class: "tl-subject-state", id: "tlSubjectState" });
    const addSubject = h("button", { class: "btn small", type: "button", hidden: true, onclick: () => RE.go("#~new") }, "New subject");
    const bar = h("div", { class: "tl-bar" },
      h("div", { class: "tl-subject" }, h("label", { for: "tlSubject", class: "tl-subject-label" }, "Run on"), picker, addSubject),
      subjectState);

    // search + filters + categories
    const search = h("input", { class: "input tl-search-input", id: "tlSearch", type: "search", autocomplete: "off",
      spellcheck: "false", placeholder: `Search ${total} tools`, "aria-describedby": "tlSearchHelp", "aria-controls": "tlList" });
    search.addEventListener("input", () => setQuery(search.value));
    search.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || (e.key === "Enter" && !e.isComposing)) {
        const first = visibleToggles()[0];
        if (first) { e.preventDefault(); first.focus(); if (e.key === "Enter") setOpen(T.items.get(first.dataset.name), true); }
      } else if (e.key === "Escape" && search.value) {
        e.preventDefault(); e.stopPropagation(); search.value = ""; setQuery("");
      }
    });
    const searchBox = h("div", { class: "tl-search", role: "search" },
      h("label", { for: "tlSearch", class: "sr-only" }, "Search tools"),
      icon("search", 15), search, h("kbd", { class: "tl-kbd", "aria-hidden": "true" }, "/"));
    const help = h("p", { id: "tlSearchHelp", class: "sr-only" },
      "Filters by name, title and description as you type. Press Down arrow to move into the list; press slash from anywhere on this page to come back here.");

    const filterBox = h("fieldset", { class: "tl-filters" }, h("legend", { class: "tl-side-label" }, "Hide"),
      filterToggle("safe", "Tools that change state", "REA marks these destructive: they may change the target, a device or files on this PC. You are asked to confirm before one runs."),
      filterToggle("closed", "Tools that may reach outside", "REA marks these open-world: they may interact with things outside the opened file, such as a device, a process or a website."));

    const catList = h("ul", { class: "tl-cats-list" });
    const cats = h("nav", { class: "tl-cats", "aria-label": "Tool groups" }, catList);
    catList.addEventListener("keydown", (e) => {
      const buttons = [...catList.querySelectorAll("button")];
      const i = buttons.indexOf(document.activeElement);
      if (i < 0) return;
      let j = -1;
      if (e.key === "ArrowDown") j = Math.min(buttons.length - 1, i + 1);
      else if (e.key === "ArrowUp") j = Math.max(0, i - 1);
      else if (e.key === "Home") j = 0;
      else if (e.key === "End") j = buttons.length - 1;
      if (j >= 0) { e.preventDefault(); buttons[j].focus(); }
    });
    const side = h("div", { class: "tl-side" }, searchBox, help, filterBox, cats);

    // the list
    const viewTitle = h("h2", { class: "tl-view-title", id: "tlViewTitle" });
    const count = h("span", { class: "tl-count", id: "tlCount" });
    const empty = h("div", { class: "tl-empty", hidden: true });
    const recentBox = h("div", { class: "tl-items tl-recent-box" });
    const list = h("div", { class: "tl-list", id: "tlList" }, recentBox);
    T.items.clear();
    T.groups = cat.categories.map((c) => {
      const items = (c.tools || []).map((tool) => makeItem(tool, c.name));
      const box = h("div", { class: "tl-items" }, items.map((it) => it.el));
      // A plain div with a heading: twelve named sections would be twelve landmarks.
      const el = h("div", { class: "tl-group" }, h("h3", { class: "tl-group-title" }, c.name), box);
      return { name: c.name, items, box, el };
    });
    for (const g of T.groups) list.append(g.el);
    list.addEventListener("keydown", onListKey);
    const main = h("div", { class: "tl-main" }, h("div", { class: "tl-head" }, viewTitle, count), empty, list);

    T.root = h("div", { class: "tl" }, bar, h("div", { class: "tl-layout" }, side, main));
    RE.put(container, T.root);
    Object.assign(T.els, { picker, subjectState, addSubject, search, catList, viewTitle, count, empty, recentBox, list });
    const views = ["all", "fav", "recent", ...T.groups.map((g) => g.name)];
    if (!views.includes(T.view)) T.view = "all";
    drawCats();
    apply();
  }

  function filterToggle(key, label, why) {
    const id = `tlFilter-${key}`;
    const cb = h("input", { type: "checkbox", id, "aria-describedby": `${id}-why` });
    cb.checked = T.filters[key];
    cb.addEventListener("change", () => { T.filters[key] = cb.checked; save(STORE.filters, T.filters); drawCats(); apply(true); });
    return h("div", { class: "tl-filter" }, cb, h("label", { for: id }, label), h("span", { class: "sr-only", id: `${id}-why` }, why));
  }

  // ---------- one tool ----------
  function makeItem(tool, category) {
    const base = `tool-${safeId(tool.name)}`;
    const it = { tool, category, base, form: null, defaults: {}, running: null, results: null, built: false,
      hay: `${tool.name} ${tool.name.replace(/_/g, " ")} ${tool.title} ${tool.description} ${category}`.toLowerCase() };
    const badges = [];
    if (isReadOnly(tool)) badges.push(badge("read", "read-only", "REA marks this tool read-only: it does not change anything."));
    if (isDestructive(tool)) badges.push(badge("write", "changes state", "REA marks this tool destructive: it may change the target, a device or files on this PC. You are asked to confirm before it runs."));
    if (isOpenWorld(tool)) badges.push(badge("world", "may reach outside", "REA marks this tool open-world: it may interact with things outside the opened file, such as a device, a process or a website."));
    const descId = `${base}-desc`, badgesId = `${base}-badges`;
    it.runTag = h("span", { class: "tl-run-tag", hidden: true }, h("i", { class: "tl-pulse", "aria-hidden": "true" }), "running");
    it.toggle = h("button", { class: "tl-toggle", type: "button", id: `${base}-btn`, "aria-expanded": "false",
      "aria-controls": `${base}-body`, "aria-describedby": [descId, badges.length ? badgesId : null].filter(Boolean).join(" "),
      "data-name": tool.name },
      icon("caret", 16),
      h("span", { class: "tl-titles" }, h("span", { class: "tl-title" }, tool.title),
        h("span", { class: "tl-name" }, tool.name)));
    it.toggle.addEventListener("click", () => setOpen(it, it.toggle.getAttribute("aria-expanded") !== "true"));
    it.star = h("button", { class: "tl-star", type: "button", "aria-pressed": "false", "aria-label": `Favourite: ${tool.title}` }, icon("star", 16));
    it.star.addEventListener("click", () => toggleFavourite(it));
    const desc = h("p", { class: "tl-desc", id: descId }, tool.description || "No description.");
    desc.addEventListener("click", () => setOpen(it, it.toggle.getAttribute("aria-expanded") !== "true"));
    it.body = h("div", { class: "tl-body", id: `${base}-body`, hidden: true });
    it.el = h("article", { class: "tl-tool", id: base, "data-name": tool.name },
      h("div", { class: "tl-row" }, it.toggle, it.runTag,
        badges.length ? h("span", { class: "tl-badges", id: badgesId }, badges) : null, it.star),
      desc, it.body);
    T.items.set(tool.name, it);
    syncStar(it);
    return it;
  }
  function badge(kind, text, why) { return h("span", { class: `tl-badge ${kind}`, title: why }, text); }

  function setOpen(it, open, opts = {}) {
    if (!it) return;
    it.toggle.setAttribute("aria-expanded", String(open));
    it.el.classList.toggle("open", open);
    it.body.hidden = !open;
    if (open && !it.built) buildBody(it);
    if (opts.route === false || !onToolsPage()) return;
    // Keep the address a deep link to the open tool, without a hashchange (so the shell does
    // not re-route and move focus).
    const mark = `#~tools/${encodeURIComponent(it.tool.name)}`;
    if (open) { T.routed = it.tool.name; if (location.hash !== mark) history.replaceState(null, "", mark); }
    if (!open && location.hash === mark) { T.routed = null; history.replaceState(null, "", "#~tools"); }
  }

  function buildBody(it) {
    it.built = true;
    const tool = it.tool;
    const notes = [];
    if (isDestructive(tool)) notes.push(h("p", { class: "tl-note warn" }, icon("alert", 15),
      h("span", null, "This tool can change state: the target, a device or files on this PC. The studio asks you to confirm before it runs.")));
    if (isOpenWorld(tool)) notes.push(h("p", { class: "tl-note" }, icon("globe", 15),
      h("span", null, "This tool may reach outside the opened file, for example a device, a process or a website.")));
    it.prefillNote = h("div", { class: "tl-prefill", hidden: true });
    it.form = RE.forms.build(tool.inputSchema || {}, { idPrefix: it.base, templates: TEMPLATES[tool.name] });
    it.runBtn = h("button", { class: "btn primary small", type: "button", "aria-label": `Run ${tool.title}`,
      "aria-describedby": `${it.base}-where` }, icon("play", 14), h("span", null, "Run"));
    it.runBtn.addEventListener("click", () => run(it));
    it.cancelBtn = h("button", { class: "btn small danger", type: "button", hidden: true, "aria-label": `Cancel ${tool.title}` },
      icon("stop", 14), h("span", null, "Cancel"));
    it.cancelBtn.addEventListener("click", () => cancel(it));
    const reset = h("button", { class: "btn small ghost", type: "button", "aria-label": `Reset the ${tool.title} fields` }, "Reset");
    reset.addEventListener("click", () => { it.form.reset(); it.form.setErrors([]); fillDefaults(it, true); });
    const copy = h("button", { class: "btn small ghost", type: "button", "aria-label": `Copy the ${tool.title} arguments as JSON` },
      icon("copy", 14), h("span", null, "Copy arguments"));
    copy.addEventListener("click", () => {
      const r = it.form.read();
      it.form.setErrors(r.errors);
      if (!r.ok) return void it.form.focusFirstError();
      RE.copy(JSON.stringify(r.value, null, 2), copy);
    });
    const paste = h("button", { class: "btn small ghost", type: "button", "aria-label": `Fill the ${tool.title} fields from JSON`,
      "aria-haspopup": "dialog" }, h("span", null, "Paste arguments…"));
    paste.addEventListener("click", () => pasteArguments(it));
    it.where = h("span", { class: "tl-where", id: `${it.base}-where` });
    it.progress = h("div", { class: "tl-progress", hidden: true },
      h("progress", { "aria-label": `${tool.title} progress` }), h("span", { class: "tl-progress-text" }));
    it.results = h("div", { class: "tl-results" });
    const form = h("form", { class: "tl-form", novalidate: true, "aria-label": `${tool.title} arguments` }, it.form.el);
    // Enter in a one-line field runs the tool (implicit submission does nothing once a form
    // has two text fields and no submit button, so it is done here).
    form.addEventListener("submit", (e) => e.preventDefault());
    form.addEventListener("keydown", (e) => {
      const t = e.target;
      if (e.key !== "Enter" || e.isComposing || e.shiftKey || t.tagName !== "INPUT" || t.type === "checkbox") return;
      e.preventDefault();
      if (!it.running) run(it);
    });
    RE.put(it.body, notes, it.prefillNote, form,
      h("div", { class: "tl-foot" }, it.runBtn, it.cancelBtn, reset, copy, paste, it.where),
      it.progress, it.results);
    syncWhere(it);
    fillDefaults(it, false);
  }

  // Fill a form from a JSON object of arguments (say, copied from a Claude transcript).
  function pasteArguments(it) {
    const id = `${it.base}-paste`;
    const area = h("textarea", { class: "input area mono", id, rows: 10, spellcheck: "false", "aria-describedby": `${id}-hint ${id}-err` });
    const err = h("p", { class: "f-error", id: `${id}-err`, hidden: true });
    const apply = h("button", { class: "btn primary", type: "submit" }, "Fill the fields");
    const cancel = h("button", { class: "btn", type: "button" }, "Cancel");
    const form = h("form", { class: "f-dialog-form", novalidate: true },
      h("div", { class: "f-field" }, h("label", { class: "f-label", for: id }, "Arguments as JSON"), area,
        h("p", { class: "f-hint", id: `${id}-hint` }, `One JSON object whose keys are ${it.tool.name}’s inputs. Fields it names are replaced; others are left as they are.`),
        err),
      h("div", { class: "modal-foot" }, cancel, apply));
    const fail = (message) => {
      err.textContent = message; err.hidden = false;
      area.setAttribute("aria-invalid", "true");
      area.focus();
    };
    let handle = null;
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      let value;
      try { value = JSON.parse(area.value); } catch { return fail("That isn’t valid JSON."); }
      if (!value || typeof value !== "object" || Array.isArray(value)) return fail("Paste one JSON object: { \"name\": value, … }.");
      const known = new Set(it.form.keys);
      const extra = Object.keys(value).filter((k) => !known.has(k));
      if (extra.length) return fail(`${it.tool.name} has no input named ${extra.map((k) => `“${k}”`).join(", ")}.`);
      it.form.fill(value, { touch: true });
      it.form.setErrors([]);
      if (handle) handle.close();
      RE.announce && RE.announce(`Filled ${Object.keys(value).length} field${Object.keys(value).length === 1 ? "" : "s"}.`, "polite");
    });
    area.addEventListener("input", () => { err.hidden = true; area.removeAttribute("aria-invalid"); });
    cancel.addEventListener("click", () => handle && handle.close());
    handle = RE.dialog.open({ title: `Paste arguments for ${it.tool.title}`, body: form });
    setTimeout(() => area.focus(), 0);
  }

  // ---------- prefill from the subject (/api/tool/defaults) ----------
  async function fillDefaults(it, force) {
    if (!it.form) return;
    const slug = T.slug;
    const stamp = (it.defaultsStamp = (it.defaultsStamp || 0) + 1);
    let reply = { arguments: {}, notes: [] };
    if (slug) {
      try {
        reply = await RE.api(`/api/tool/defaults?slug=${encodeURIComponent(slug)}&name=${encodeURIComponent(it.tool.name)}`);
      } catch { reply = { arguments: {}, notes: [] }; }
    }
    if (stamp !== it.defaultsStamp) return;   // a newer subject change won
    const next = reply && reply.arguments && typeof reply.arguments === "object" ? reply.arguments : {};
    const clear = {};
    for (const k of Object.keys(it.defaults)) if (!(k in next)) clear[k] = undefined;
    it.form.fill(clear, { onlyUntouched: !force });
    it.form.fill(next, { mark: "from subject", onlyUntouched: !force });
    it.defaults = next;
    it.defaultsSlug = slug;
    const lines = Array.isArray(reply && reply.notes) ? reply.notes.filter(Boolean) : [];
    const keys = Object.keys(next);
    if (!lines.length && keys.length) lines.push(`Filled in from the subject: ${keys.join(", ")}. You can change them.`);
    it.prefillNote.hidden = !lines.length;
    RE.put(it.prefillNote, lines.map((l) => h("p", null, l)));
  }

  // ---------- running ----------
  function newRunId() {
    const bytes = new Uint8Array(6);
    (window.crypto || {}).getRandomValues ? crypto.getRandomValues(bytes) : bytes.forEach((_, i) => { bytes[i] = Math.random() * 256; });
    return `tools-${[...bytes].map((b) => b.toString(16).padStart(2, "0")).join("")}`;
  }

  async function run(it) {
    if (it.running) return;
    const slug = T.slug;
    const brief = subjectOf(slug);
    if (!brief) {
      syncWhere(it, true);
      RE.announce && RE.announce("Add a subject first, then run this tool on it.", "assertive");
      if (!T.els.addSubject.hidden) T.els.addSubject.focus();
      return;
    }
    const r = it.form.read();
    it.form.setErrors(r.errors);
    if (!r.ok) {
      it.form.focusFirstError();
      RE.announce && RE.announce(`${r.errors.length} field${r.errors.length === 1 ? " needs" : "s need"} attention before ${it.tool.title} can run.`, "assertive");
      return;
    }
    // A tool that can change state is confirmed before anything shows it as running or used.
    let confirmed = false;
    if (isDestructive(it.tool)) {
      it.running = { pending: true };
      const ok = await RE.confirmRun(slug, it.tool.name);
      it.running = null;
      if (!ok) {
        RE.announce && RE.announce(`${it.tool.title} was not run.`, "polite");
        if (document.contains(it.runBtn)) it.runBtn.focus({ preventScroll: true });
        return;
      }
      confirmed = true;
    }
    const runRec = { id: newRunId(), slug, subjectName: brief.name, started: Date.now(), tool: it.tool.name, args: r.value };
    it.running = runRec;
    pushRecent(it.tool.name);
    const block = runBlock(it, runRec);
    it.results.prepend(block.el);
    while (it.results.children.length > KEEP_RESULTS) it.results.lastElementChild.remove();
    setRunning(it, true);
    runRec.timer = setInterval(() => tick(it), 1000);
    tick(it);

    let reply;
    try {
      reply = await RE.runTool(slug, it.tool.name, r.value, { source: "tools", runId: runRec.id, confirmed });
    } catch (e) {
      reply = { error: (e && e.message) || "The run failed." };
    }
    clearInterval(runRec.timer);
    it.running = null;
    setRunning(it, false);
    finish(it, runRec, block, reply);
    if (RE.refresh) RE.refresh();
  }

  function setRunning(it, on) {
    const hadFocus = document.activeElement === it.cancelBtn || document.activeElement === it.runBtn;
    it.runBtn.hidden = on;
    it.cancelBtn.hidden = !on;
    it.cancelBtn.removeAttribute("aria-disabled");
    it.cancelBtn.lastChild.textContent = "Cancel";
    it.runTag.hidden = !on;
    it.progress.hidden = !on;
    if (on) {
      const bar = it.progress.querySelector("progress");
      bar.removeAttribute("value");
      it.progress.querySelector(".tl-progress-text").textContent = "";
      if (hadFocus) it.cancelBtn.focus({ preventScroll: true });
    } else if (hadFocus) {
      it.runBtn.focus({ preventScroll: true });
    }
  }

  function tick(it) {
    const run = it.running;
    if (!run || run.pending) return;
    const live = RE.state && RE.state.runs && RE.state.runs[run.id];
    const p = live && live.progress;
    const bar = it.progress.querySelector("progress");
    const bits = [`Running for ${seconds(Date.now() - run.started)}`];
    if (p && typeof p.progress === "number") {
      if (typeof p.total === "number" && p.total > 0) {
        bar.max = p.total; bar.value = Math.min(p.progress, p.total);
        bits.push(`${Math.round((100 * Math.min(p.progress, p.total)) / p.total)}%`);
      } else bar.removeAttribute("value");
      if (p.message) bits.push(String(p.message));
    }
    it.progress.querySelector(".tl-progress-text").textContent = bits.join(" · ");
  }

  async function cancel(it) {
    const run = it.running;
    if (!run || run.pending || run.cancelled) return;
    // Not disabled: a disabled button loses focus, and keyboard users would land at the page top.
    it.cancelBtn.setAttribute("aria-disabled", "true");
    it.cancelBtn.lastChild.textContent = "Cancelling…";
    run.cancelled = true;
    try { await RE.cancelTool(run.slug, run.id); } catch { /* the run's own reply reports the outcome */ }
  }

  function runBlock(it, run) {
    const status = h("span", { class: "tl-run-status running" }, "Running");
    const iso = new Date(run.started).toISOString();
    const meta = h("span", { class: "tl-run-meta" },
      h("time", { datetime: iso, title: RE.fmt.full(iso) }, RE.fmt.time(iso)), ` · on ${run.subjectName}`);
    const actions = h("span", { class: "tl-run-actions" });
    const body = h("div", { class: "tl-run-body" });
    const el = h("section", { class: "tl-run", "aria-label": `${it.tool.title} run at ${RE.fmt.full(iso)}` },
      h("header", { class: "tl-run-head" }, status, meta, actions), body);
    return { el, status, meta, actions, body };
  }

  function finish(it, run, block, reply) {
    const took = seconds(Date.now() - run.started);
    const title = it.tool.title;
    const dismiss = h("button", { class: "btn ghost small square", type: "button", "aria-label": `Remove this ${title} result`, title: "Remove this result" }, icon("x", 14));
    dismiss.addEventListener("click", () => {
      const next = block.el.nextElementSibling || block.el.previousElementSibling;
      block.el.remove();
      (next ? next.querySelector("button") : it.runBtn).focus();
    });

    // RE.runTool asks before a destructive tool; a "no" comes back as one of these.
    if (!reply || reply.declined || reply.cancelled || reply.needs_confirmation) {
      setStatus(block, "muted", "Not run");
      RE.put(block.body, h("p", { class: "tl-msg" }, reply && reply.error ? reply.error : "You chose not to run it."));
      block.actions.append(dismiss);
      return;
    }
    if (reply.error) {
      setStatus(block, "bad", "Didn’t run");
      RE.put(block.body, h("p", { class: "tl-msg bad" }, icon("alert", 15), h("span", null, reply.error)));
      block.actions.append(dismiss);
      RE.announce && RE.announce(`${title} didn’t run: ${reply.error}`, "assertive");
      return;
    }
    const entry = reply.entry || null;
    const result = reply.result || {};
    const err = (entry && entry.error) || parseError(result);
    const cancelled = err && (err.code === "cancelled" || err.category === "cancelled");
    block.meta.append(` · ${took}`);
    if (cancelled) {
      setStatus(block, "muted", "Cancelled");
      RE.put(block.body, h("p", { class: "tl-msg" }, run.cancelled ? "You cancelled this run." : "REA cancelled this run."));
      RE.announce && RE.announce(`${title} cancelled.`, "polite");
    } else if (err) {
      setStatus(block, "bad", "REA error");
      RE.put(block.body, resultView(entry, result, run, err));
      RE.announce && RE.announce(`${title} finished with an error: ${err.message || err.code || "see the result"}.`, "assertive");
    } else {
      setStatus(block, "ok", entry && entry.untrusted ? "Done · untrusted content" : "Done");
      RE.put(block.body, resultView(entry, result, run, null));
      RE.announce && RE.announce(`${title} finished in ${took}.`, "polite");
    }
    if (entry && entry.id) {
      const show = h("button", { class: "btn ghost small", type: "button", "aria-label": `Show this ${title} result in the subject’s findings` }, "Show in findings");
      show.addEventListener("click", () => showInFindings(run.slug, entry.id));
      block.actions.append(show);
    }
    block.actions.append(dismiss);
  }
  function setStatus(block, kind, text) { block.status.className = `tl-run-status ${kind}`; block.status.textContent = text; }

  function parseError(result) {
    if (!result || !result.isError) return null;
    const text = (result.content || []).filter((c) => c && c.type === "text").map((c) => c.text).join("\n");
    try {
      const parsed = JSON.parse(text);
      if (parsed && parsed.error && typeof parsed.error === "object") return parsed.error;
    } catch { /* plain text error */ }
    return { code: "error", category: "unknown", message: text || "REA reported an error." };
  }
  // The same result view as a finding (RE.evidence.body), from the stored entry, or built from
  // the raw MCP result when no entry came back.
  function resultView(entry, result, run, err) {
    const ev = RE.evidence;
    if (ev && ev.body) {
      const e = entry || (ev.fromResult ? { ...ev.fromResult(run.tool, run.args, result), source: "tools" } : null);
      if (e) return ev.body(e, { slug: run.slug });
    }
    if (err) return h("pre", { class: "json" }, JSON.stringify(err, null, 2));
    const value = result.structuredContent !== undefined ? result.structuredContent : null;
    const text = (result.content || []).filter((c) => c && c.type === "text").map((c) => c.text).join("\n");
    return h("pre", { class: "json" }, value !== null ? JSON.stringify(value, null, 2) : text || JSON.stringify(result, null, 2));
  }

  function showInFindings(slug, id) {
    RE.go(`#${encodeURIComponent(slug)}`);
    const started = Date.now();
    // The subject page fetches its findings after the route; wait for the card.
    const look = () => {
      const card = document.getElementById(`ev-${id}`);
      if (card) {
        if (RE.evidence && RE.evidence.reveal) RE.evidence.reveal(card);
        else { card.open = true; card.scrollIntoView({ block: "start" }); (card.querySelector("summary") || card).focus({ preventScroll: true }); }
      } else if (Date.now() - started < 4000) setTimeout(look, 150);
      else RE.toast && RE.toast("That result is not among the findings shown now.", { kind: "info" });
    };
    setTimeout(look, 50);
  }

  // ---------- subject picker ----------
  function syncSubject() {
    if (!T.ready || !T.els.picker) return;
    const list = subjects();
    const global = RE.state ? RE.state.slug : null;
    if (global !== T.lastGlobal) {
      T.lastGlobal = global;
      if (global && subjectOf(global)) T.slug = global;
    }
    if (!subjectOf(T.slug)) T.slug = (global && subjectOf(global) ? global : list[0] && list[0].slug) || null;

    const sig = list.map((s) => `${s.slug}\u0000${s.name}\u0000${s.kind}`).join("\u0001");
    const picker = T.els.picker;
    if (sig !== T.subjectsSig) {
      T.subjectsSig = sig;
      RE.put(picker, list.length
        ? list.map((s) => h("option", { value: s.slug }, `${s.name} · ${s.kind}`))
        : [h("option", { value: "" }, "No subjects yet")]);
    }
    picker.disabled = !list.length;
    T.els.addSubject.hidden = !!list.length;
    if (picker.value !== (T.slug || "")) picker.value = T.slug || "";
    if (T.formsSlug !== T.slug) {
      T.formsSlug = T.slug;
      for (const it of T.items.values()) if (it.built) { syncWhere(it); fillDefaults(it, false); }
    }
    syncSubjectState();
  }

  function setSubject(slug) {
    T.slug = slug;
    syncSubject();
  }

  function syncSubjectState() {
    const s = subjectOf(T.slug);
    let text;
    if (!s) text = "Add a subject to run tools on it. You can browse every tool without one.";
    else {
      const eng = (s.engine && s.engine.state) || "stopped";
      const bits = [];
      if (eng === "running") bits.push("Engine running.");
      else if (eng === "starting") bits.push("Engine starting…");
      else if (eng === "crashed") bits.push(`Engine stopped with an error${s.engine.error ? `: ${s.engine.error}` : "."} A run starts it again.`);
      else bits.push("Engine stopped: a run starts it.");
      if (s.kind === "website") {
        const w = s.web && s.web.state;
        bits.push(w === "running" ? "Observed browser running." : "Web tools observe this site in a browser the studio controls, which loads it over the network.");
      } else if (s.exists === false) bits.push("The target file is not at its path now.");
      bits.push("Results also go to its Findings.");
      text = bits.join(" ");
    }
    if (T.els.subjectState.textContent !== text) T.els.subjectState.textContent = text;
  }

  function syncWhere(it, flagIt) {
    if (!it.where) return;
    const s = subjectOf(T.slug);
    it.where.textContent = s ? `Runs on ${s.name}.` : "Add a subject first, then run this on it.";
    it.where.classList.toggle("bad", !s && !!flagIt);
  }

  // ---------- favourites and recent ----------
  function favourites() { const v = store(STORE.fav, []); return Array.isArray(v) ? v : []; }
  function recent() { const v = store(STORE.recent, []); return Array.isArray(v) ? v : []; }
  function syncStar(it) {
    const on = favourites().includes(it.tool.name);
    it.star.setAttribute("aria-pressed", String(on));
    it.star.title = on ? "Remove from favourites" : "Add to favourites";
    it.el.classList.toggle("fav", on);
  }
  function toggleFavourite(it) {
    const list = favourites().filter((n) => n !== it.tool.name);
    const on = !favourites().includes(it.tool.name);
    if (on) list.unshift(it.tool.name);
    // In Favourites, un-starring hides the tool, star and all: focus moves to the next star first.
    let next = null;
    const hadFocus = it.el.contains(document.activeElement);
    if (T.view === "fav" && !on && hadFocus) {
      const shown = [...T.els.list.querySelectorAll(".tl-tool")].filter((el) => !el.hidden && el.offsetParent !== null);
      const i = shown.indexOf(it.el);
      const near = shown[i + 1] || shown[i - 1];
      next = near ? near.querySelector(".tl-star") : null;
    }
    save(STORE.fav, list);
    syncStar(it);
    drawCats();
    if (T.view === "fav") {
      apply(true);
      // (A hidden element can still be document.activeElement until the browser's next layout.)
      if (!on && it.el.hidden && hadFocus) {
        const target = next || T.els.empty.querySelector("button")
          || T.els.catList.querySelector('[data-view="fav"]');
        if (target) target.focus({ preventScroll: true });
      }
    }
    RE.announce && RE.announce(on ? `${it.tool.title} added to favourites.` : `${it.tool.title} removed from favourites.`, "polite");
  }
  function pushRecent(name) {
    const list = [name, ...recent().filter((n) => n !== name)].slice(0, RECENT_MAX);
    save(STORE.recent, list);
    drawCats();
    if (T.view === "recent") apply();
  }

  // ---------- search, views, filters ----------
  function terms() { return T.query.toLowerCase().split(/\s+/).filter(Boolean); }
  function matchesQuery(it, words) { return words.every((w) => it.hay.includes(w)); }
  function passesFilters(it) {
    if (T.filters.safe && isDestructive(it.tool)) return false;
    if (T.filters.closed && isOpenWorld(it.tool)) return false;
    return true;
  }
  function inView(it, view) {
    if (view === "all") return true;
    if (view === "fav") return favourites().includes(it.tool.name);
    if (view === "recent") return recent().includes(it.tool.name);
    return it.category === view;
  }

  function setQuery(text) {
    T.query = text;
    const has = !!text.trim();
    if (has && !T.searching) { T.searching = true; T.viewBeforeSearch = T.view; T.view = "all"; }
    if (!has && T.searching) { T.searching = false; T.view = T.viewBeforeSearch || T.view; }
    drawCats();
    apply(true);
  }

  function setView(view) {
    T.view = view;
    if (T.searching) T.viewBeforeSearch = view;
    save(STORE.view, view);
    drawCats();
    apply(true);
  }

  function drawCats() {
    const words = terms();
    const counted = (pred) => [...T.items.values()].filter((it) => pred(it) && passesFilters(it) && matchesQuery(it, words)).length;
    const rows = [
      ["fav", "Favourites", counted((it) => inView(it, "fav")), "star"],
      ["recent", "Recent", counted((it) => inView(it, "recent")), null],
      ["all", "All tools", counted(() => true), null],
      ...T.groups.map((g) => [g.name, g.name, counted((it) => it.category === g.name), null]),
    ];
    const keepFocus = document.activeElement && T.els.catList.contains(document.activeElement) ? document.activeElement.dataset.view : null;
    RE.put(T.els.catList, rows.map(([view, label, n], i) => {
      const b = h("button", { class: "tl-cat", type: "button", "data-view": view, "aria-current": T.view === view ? "true" : null },
        h("span", { class: "tl-cat-name" }, label), h("span", { class: "tl-cat-n" }, h("span", { class: "sr-only" }, ", "), String(n),
          h("span", { class: "sr-only" }, n === 1 ? " tool" : " tools")));
      b.addEventListener("click", () => setView(view));
      const li = h("li", { class: i === 2 ? "tl-cat-sep" : null }, b);
      return li;
    }));
    if (keepFocus) {
      const again = T.els.catList.querySelector(`[data-view="${CSS.escape(keepFocus)}"]`);
      if (again) again.focus();
    }
  }

  // Search results, best match first, ranked as the Ctrl+K palette ranks them (RE.matchScore).
  function searchOrder(words) {
    const query = T.query.trim().toLowerCase();
    return [...T.items.values()]
      .map((it, i) => ({ it, i, s: inView(it, T.view) && passesFilters(it)
        ? RE.matchScore({ label: it.tool.title, name: it.tool.name, hay: it.hay }, words, query) : -1 }))
      .filter((x) => x.s >= 0)
      .sort((a, b) => a.s - b.s || a.i - b.i)
      .map((x) => x.it);
  }

  function apply(announce) {
    const words = terms();
    const view = T.view;
    let shown = 0;
    // Recent (by recency) and a search (by relevance) list their items in one box, in that order;
    // every other view keeps them in their groups. Moving a node blurs whatever inside it has
    // focus, so only move when the order changes, and put focus back after.
    const flat = view === "recent" ? recent().map((n) => T.items.get(n)).filter(Boolean)
      : T.query.trim() ? searchOrder(words) : null;
    const box = T.els.recentBox;
    const active = document.activeElement;
    if (flat) {
      const want = flat.map((it) => it.el);
      const have = [...box.children];
      if (want.length !== have.length || want.some((el, i) => el !== have[i])) box.append(...want);
    }
    if (box.children.length) {
      const keep = new Set((flat || []).map((it) => it.tool.name));
      for (const g of T.groups) {
        const away = g.items.filter((it) => !keep.has(it.tool.name) && it.el.parentNode !== g.box);
        if (away.length) g.box.append(...g.items.filter((it) => !keep.has(it.tool.name)).map((it) => it.el));
      }
    }
    if (active && active !== document.activeElement && document.contains(active)) active.focus({ preventScroll: true });
    for (const it of T.items.values()) {
      const on = inView(it, view) && passesFilters(it) && matchesQuery(it, words);
      it.el.hidden = !on;
      if (on) shown += 1;
    }
    box.hidden = !flat;
    for (const g of T.groups) g.el.hidden = !!flat || !g.items.some((it) => !it.el.hidden);
    // A heading per group only where groups are mixed
    const single = view !== "all" && view !== "fav";
    for (const g of T.groups) g.el.classList.toggle("solo", single);

    const label = view === "all" ? "All tools" : view === "fav" ? "Favourites" : view === "recent" ? "Recent" : view;
    T.els.viewTitle.textContent = T.query.trim() ? `${label}: “${T.query.trim()}”` : label;
    const total = [...T.items.values()].filter((it) => inView(it, view)).length;
    T.els.count.textContent = shown === total ? `${shown} tool${shown === 1 ? "" : "s"}` : `${shown} of ${total}`;

    const emptyEl = T.els.empty;
    emptyEl.hidden = shown > 0;
    if (!shown) {
      const parts = [];
      if (view === "fav" && !favourites().length) parts.push(h("p", null, "No favourites yet. Use the star on any tool to keep it here."));
      else if (view === "recent" && !recent().length) parts.push(h("p", null, "Tools you run show up here, newest first."));
      else parts.push(h("p", null, "No tools match."));
      if (T.query.trim()) parts.push(h("button", { class: "btn small", type: "button", onclick: () => { T.els.search.value = ""; setQuery(""); T.els.search.focus(); } }, "Clear the search"));
      if (T.filters.safe || T.filters.closed) parts.push(h("button", { class: "btn small ghost", type: "button", onclick: clearFilters }, "Show hidden tools"));
      if (view !== "all") parts.push(h("button", { class: "btn small ghost", type: "button", onclick: () => setView("all") }, "Show all tools"));
      RE.put(emptyEl, parts);
    }
    if (announce) announceSoon(shown ? `${shown} tool${shown === 1 ? "" : "s"} shown.` : "No tools match.");
  }

  function clearFilters() {
    T.filters = { safe: false, closed: false };
    save(STORE.filters, T.filters);
    for (const key of ["safe", "closed"]) { const cb = document.getElementById(`tlFilter-${key}`); if (cb) cb.checked = false; }
    drawCats();
    apply(true);
  }

  // ---------- keyboard ----------
  function visibleToggles() {
    return [...T.els.list.querySelectorAll(".tl-toggle")].filter((b) => b.offsetParent !== null);
  }
  function onListKey(e) {
    const t = e.target;
    if (!t.classList || !t.classList.contains("tl-toggle")) return;
    const all = visibleToggles();
    const i = all.indexOf(t);
    let j = -1;
    if (e.key === "ArrowDown") j = i + 1;
    else if (e.key === "ArrowUp") j = i - 1;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = all.length - 1;
    else return;
    e.preventDefault();
    if (j < 0) return void T.els.search.focus();
    if (all[j]) { all[j].focus(); all[j].scrollIntoView({ block: "nearest" }); }
  }
  document.addEventListener("keydown", (e) => {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || !onToolsPage()) return;
    const a = document.activeElement;
    const typing = a && (a.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName));
    if (typing || document.querySelector("dialog[open]")) return;
    e.preventDefault();
    T.els.search.focus();
    T.els.search.select();
  });

  // ---------- routes ----------
  function routeName() {
    const m = /^#~tools\/(.+)$/.exec(location.hash);
    if (!m) return null;
    try { return decodeURIComponent(m[1]); } catch { return null; }
  }
  function applyRoute() {
    const name = routeName();
    if (name && name !== T.routed) { T.routed = name; reveal(name); }
    if (!name) T.routed = null;
  }
  function reveal(name) {
    const it = T.items.get(name);
    if (!it) {
      RE.toast && RE.toast(`REA has no tool named ${name}.`, { kind: "bad" });
      history.replaceState(null, "", "#~tools");
      return;
    }
    if (it.el.hidden) {
      if (T.query) { T.els.search.value = ""; T.query = ""; T.searching = false; }
      if (!passesFilters(it)) clearFilters();
      T.view = inView(it, T.view) ? T.view : it.category;
      drawCats();
      apply();
    }
    setOpen(it, true, { route: false });
    // after the shell's own route focus
    setTimeout(() => {
      it.el.scrollIntoView({ block: "start" });
      it.toggle.focus({ preventScroll: true });
    }, 0);
  }

  function openTool(name) {
    const mark = `#~tools/${encodeURIComponent(name)}`;
    if (T.ready && (onToolsPage() || location.hash === mark)) {
      // Already here (or routed here): open it directly; pushState keeps Back working without re-routing.
      if (location.hash !== mark) history.pushState(null, "", mark);
      T.routed = name;
      reveal(name);
      return;
    }
    T.routed = null;
    RE.go(mark);
  }

  // ---------- live state ----------
  if (RE.on) {
    RE.on("state", () => {
      if (!T.ready) return;
      syncSubject();
      for (const it of T.items.values()) if (it.running) tick(it);
    });
    // The shell builds this page once; coming back to it follows the subject open now.
    RE.on("route", (r) => { if (T.ready && r && r.page === "tools") syncSubject(); });
  }

  RE.tools = { render, openTool };
})();
