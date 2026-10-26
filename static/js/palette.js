// Ctrl+K (or Cmd+K): one box to jump to a subject, a page or a tool, or run a studio action.
// An accessible combobox: the input owns a listbox and points at the active option with
// aria-activedescendant, so focus never leaves the input.
(() => {
  "use strict";

  const RE = window.RE;
  const { h, icon } = RE;
  const MAX_TOOLS = 40;
  let openNow = null;

  function kindIcon(kind) {
    return { website: "globe", archive: "archive", database: "database", artifact: "file" }[kind] || "chip";
  }

  // Everything the palette can offer right now, grouped. Tools arrive from the catalogue.
  function baseItems() {
    const st = RE.state;
    const shell = RE.shell || {};
    const items = [];
    for (const s of st.subjects || []) {
      items.push({ group: "Subjects", label: s.name, hint: s.kind === "website" ? s.url : s.path, icon: kindIcon(s.kind),
        keys: `${s.slug} ${s.kind}`, run: () => RE.go(encodeURIComponent(s.slug)), current: st.page === "subject" && st.slug === s.slug });
    }
    items.push({ group: "Pages", label: "Tools", hint: "Every REA tool, grouped and searchable", icon: "tools", run: () => RE.go("~tools") });
    items.push({ group: "Pages", label: "Settings", hint: "REA, Claude Code, Node and the analysis backends", icon: "settings", run: () => RE.go("~settings") });
    items.push({ group: "Actions", label: "New subject", hint: "Alt+N", icon: "plus", keys: "add create file website", run: () => shell.newSubject && shell.newSubject() });
    const dark = window.studioTheme && window.studioTheme.resolved() === "dark";
    items.push({ group: "Actions", label: dark ? "Switch to light theme" : "Switch to dark theme", icon: dark ? "sun" : "moon",
      keys: "theme appearance dark light mode", run: () => shell.toggleTheme && shell.toggleTheme() });
    const subject = RE.current();
    if (st.page === "subject" && subject) {
      const open = shell.assistOpen ? shell.assistOpen() : true;
      items.push({ group: "Actions", label: open ? "Hide the Ask Claude panel" : "Show the Ask Claude panel", icon: "panel",
        keys: "assist chat claude toggle", run: () => shell.toggleAssist && shell.toggleAssist() });
      items.push({ group: "Actions", label: `Ask Claude about ${subject.name}`, hint: "Ctrl+/", icon: "sparkle", keys: "chat question composer",
        run: () => shell.focusAsk && shell.focusAsk() });
      if (st.catalog && st.catalog.ready) {
        items.push({ group: "Actions", label: "Start an REA workflow", icon: "flow", keys: "prompt workflow investigate",
          run: () => shell.chooseWorkflow && shell.chooseWorkflow() });
      }
      const engine = (subject.engine && subject.engine.state) || "stopped";
      if (engine === "running") items.push({ group: "Actions", label: `Close the engine for ${subject.name}`, icon: "power", keys: "rea stop engine",
        run: () => shell.closeEngine && shell.closeEngine(subject.slug, "all") });
      else if (engine !== "starting") items.push({ group: "Actions", label: `Start the engine for ${subject.name}`, icon: "play", keys: "rea open engine",
        run: () => shell.openEngine && shell.openEngine(subject.slug) });
    }
    if (st.job && st.job.status === "running") {
      items.push({ group: "Actions", label: "Stop Claude", hint: "Stops the run and its REA call", icon: "stop", keys: "cancel halt run",
        run: () => shell.stopRun && shell.stopRun() });
    }
    items.push({ group: "Actions", label: "Keyboard shortcuts", hint: "?", icon: "keyboard", keys: "help keys", run: () => shell.showShortcuts && shell.showShortcuts() });
    return items;
  }

  function toolItems(catalog) {
    const out = [];
    for (const cat of (catalog && catalog.categories) || []) {
      for (const t of cat.tools || []) {
        out.push({ group: "Tools", label: t.title || t.name, hint: t.name, icon: "tools", keys: `${t.name} ${cat.name} ${t.description || ""}`,
          run: () => RE.go(`~tools/${encodeURIComponent(t.name)}`), tool: true });
      }
    }
    return out;
  }

  // Every word must appear; ranked as the Tools page's search ranks (RE.matchScore).
  function score(item, words, query) {
    const hay = `${item.label} ${item.hint || ""} ${item.keys || ""} ${item.group}`.toLowerCase();
    return RE.matchScore({ label: item.label, name: item.tool ? item.hint : "", hay }, words, query);
  }

  const GROUPS = ["Subjects", "Pages", "Actions", "Tools"];

  function open() {
    if (openNow) { openNow.input.focus(); openNow.input.select(); return; }
    let all = baseItems();
    let tools = [];
    let shown = [];
    let active = 0;

    const listId = "palette-list";
    const input = h("input", { class: "palette-input", type: "text", role: "combobox", "aria-expanded": "true", "aria-controls": listId,
      "aria-autocomplete": "list", "aria-label": "Search subjects, pages, tools and actions", placeholder: "Search subjects, pages, tools and actions…",
      autocomplete: "off", spellcheck: "false" });
    const list = h("div", { class: "palette-list", id: listId, role: "listbox", "aria-label": "Results" });
    const count = h("div", { class: "sr-only", role: "status", "aria-live": "polite" });
    const foot = h("div", { class: "palette-foot", "aria-hidden": "true" },
      h("span", null, h("kbd", null, "↑"), h("kbd", null, "↓"), " to move"), h("span", null, h("kbd", null, "Enter"), " to open"),
      h("span", null, h("kbd", null, "Esc"), " to close"));
    const body = h("div", { class: "palette-box" },
      h("div", { class: "palette-search" }, icon("search", 18), input), list, count, foot);

    const d = RE.dialog.open({ title: "Search and jump", hideTitle: true, body, className: "palette", initialFocus: input,
      onClose: () => { openNow = null; } });
    openNow = { input, close: () => d.close() };

    let countTimer = 0;
    function draw() {
      const query = input.value.trim().toLowerCase();
      const words = query.split(/\s+/).filter(Boolean);
      let items;
      if (!words.length) items = all.map((it, i) => ({ it, s: 0, i }));
      else {
        items = [];
        [...all, ...tools].forEach((it, i) => { const s = score(it, words, query); if (s >= 0) items.push({ it, s, i }); });
        items.sort((a, b) => a.s - b.s || GROUPS.indexOf(a.it.group) - GROUPS.indexOf(b.it.group) || a.i - b.i);
      }
      // Keep groups together, in a fixed order, with tools capped.
      const byGroup = new Map(GROUPS.map((g) => [g, []]));
      for (const x of items) byGroup.get(x.it.group).push(x.it);
      const toolsFound = byGroup.get("Tools").length;
      byGroup.set("Tools", byGroup.get("Tools").slice(0, MAX_TOOLS));
      const order = words.length ? [...GROUPS].sort((a, b) => {
        const best = (g) => { const f = items.find((x) => x.it.group === g); return f ? f.s : 9; };
        return best(a) - best(b) || GROUPS.indexOf(a) - GROUPS.indexOf(b);
      }) : GROUPS;
      shown = [];
      const blocks = [];
      for (const g of order) {
        const its = byGroup.get(g);
        if (!its.length) continue;
        const gid = `palette-g-${g.toLowerCase()}`;
        blocks.push(h("div", { role: "group", "aria-labelledby": gid, class: "palette-group" },
          h("div", { class: "palette-group-label", id: gid, role: "presentation" },
            g === "Tools" && toolsFound > its.length ? `Tools · first ${its.length} of ${toolsFound}` : g),
          its.map((it) => {
            const n = shown.length;
            shown.push(it);
            return h("div", { class: "palette-item", role: "option", id: `palette-opt-${n}`, "aria-selected": "false", "data-n": n,
              onclick: () => choose(n), onmousemove: () => { if (active !== n) setActive(n, false); } },
              h("span", { class: "palette-icon" }, icon(it.icon || "arrow", 16)),
              h("span", { class: "palette-label" }, it.label, it.current ? h("span", { class: "palette-tag" }, "open") : null),
              it.hint ? h("span", { class: `palette-hint${it.tool ? " mono" : ""}` }, it.hint) : null);
          })));
      }
      if (!shown.length) blocks.push(h("div", { class: "palette-empty" }, words.length ? `Nothing matches “${input.value.trim()}”.` : "Nothing to show."));
      list.replaceChildren(...blocks);
      setActive(0, true);
      clearTimeout(countTimer);
      countTimer = setTimeout(() => { count.textContent = shown.length ? `${shown.length} result${shown.length === 1 ? "" : "s"}` : "No results"; }, 350);
    }
    function setActive(n, scroll) {
      const prev = list.querySelector('[aria-selected="true"]');
      if (prev) prev.setAttribute("aria-selected", "false");
      active = Math.max(0, Math.min(shown.length - 1, n));
      const el = list.querySelector(`[data-n="${active}"]`);
      if (el) {
        el.setAttribute("aria-selected", "true");
        input.setAttribute("aria-activedescendant", el.id);
        if (scroll) el.scrollIntoView({ block: "nearest" });
      } else input.removeAttribute("aria-activedescendant");
    }
    function choose(n) {
      const it = shown[n];
      if (!it) return;
      d.close();
      // After the dialog has handed focus back, so the action can move it on.
      setTimeout(() => { try { it.run(); } catch (err) { console.error(err); } }, 0);
    }
    input.addEventListener("input", draw);
    input.addEventListener("keydown", (e) => {
      if (e.isComposing) return;
      const page = 8;
      if (e.key === "ArrowDown") { e.preventDefault(); setActive(active + 1 >= shown.length ? 0 : active + 1, true); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setActive(active - 1 < 0 ? shown.length - 1 : active - 1, true); }
      else if (e.key === "PageDown") { e.preventDefault(); setActive(active + page, true); }
      else if (e.key === "PageUp") { e.preventDefault(); setActive(active - page, true); }
      else if (e.key === "Enter") { e.preventDefault(); choose(active); }
      else if (e.key === "Tab") { e.preventDefault(); }   // the list is reached with the arrows
    });

    draw();
    RE.catalog().then((cat) => {
      if (!openNow) return;
      tools = toolItems(cat);
      if (input.value.trim()) draw();
    }).catch(() => {});
  }

  RE.palette = { open };
})();
