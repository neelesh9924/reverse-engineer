// window.RE: the small shared toolkit every page module uses. Loaded right after theme.js and
// before the other modules; app.js fills in RE.state, RE.refresh and RE.go when it starts.
(() => {
  "use strict";

  const RE = (window.RE = window.RE || {});

  // ---------- DOM ----------
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (name === "class") el.className = value;
      else if (name.startsWith("on") && typeof value === "function") el.addEventListener(name.slice(2), value);
      else el.setAttribute(name, value === true ? "" : value);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  function put(box, ...pieces) { box.replaceChildren(...pieces.flat(Infinity).filter((p) => p !== null && p !== undefined && p !== false)); }

  const SVG = "http://www.w3.org/2000/svg";
  const ICONS = {
    plus: ["M12 5v14", "M5 12h14"],
    refresh: ["M20 11a8 8 0 1 0-2.3 6.3", "M20 4v7h-7"],
    sparkle: ["M11 3l1.9 5.1L18 10l-5.1 1.9L11 17l-1.9-5.1L4 10l5.1-1.9z", "M18.5 15l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"],
    stop: ["M7 7h10v10H7z"],
    send: ["M12 19V5", "M6 11l6-6 6 6"],
    trash: ["M4 7h16", "M9 7V4h6v3", "M6.5 7l1 13h9l1-13"],
    panel: ["M4 5h16v14H4z", "M15 5v14"],
    lock: ["M6 11h12v9H6z", "M9 11V8a3 3 0 0 1 6 0v3"],
    alert: ["M12 4l9 16H3z", "M12 10v4", "M12 17.2v.3"],
    info: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 11v5", "M12 7.8v.3"],
    check: ["M5 12.5l4.5 4.5L19 7.5"],
    circle: ["M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16z"],
    settings: ["M4 7h9", "M19 7h1", "M4 17h3", "M13 17h7", "M16 5a2 2 0 1 0 0 4 2 2 0 0 0 0-4z", "M10 15a2 2 0 1 0 0 4 2 2 0 0 0 0-4z"],
    gear: ["M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z", "M19.4 13.5l1.6 1-2 3.4-1.8-.6a7 7 0 0 1-2.4 1.4L14.4 21h-4l-.4-2.3a7 7 0 0 1-2.4-1.4l-1.8.6-2-3.4 1.6-1a7 7 0 0 1 0-3l-1.6-1 2-3.4 1.8.6A7 7 0 0 1 10 5.3L10.4 3h4l.4 2.3a7 7 0 0 1 2.4 1.4l1.8-.6 2 3.4-1.6 1a7 7 0 0 1 0 3z"],
    moon: ["M20 14.5A8 8 0 0 1 9.5 4a7 7 0 1 0 10.5 10.5z"],
    sun: ["M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z", "M12 3v2", "M12 19v2", "M3 12h2", "M19 12h2", "M5.6 5.6L7 7", "M17 17l1.4 1.4", "M5.6 18.4L7 17", "M17 7l1.4-1.4"],
    chip: ["M7 7h10v10H7z", "M10 3v2M14 3v2M10 19v2M14 19v2M3 10h2M3 14h2M19 10h2M19 14h2"],
    play: ["M8 5l11 7-11 7z"],
    power: ["M12 3v8", "M6.3 7.2a7.5 7.5 0 1 0 11.4 0"],
    tools: ["M14 7a3 3 0 0 1-4 4L6 15l3 3 4-4a3 3 0 0 0 4-4z", "M14.5 6.5l3-3 2 2-3 3"],
    caret: ["M9 6l6 6-6 6"],
    down: ["M6 9l6 6 6-6"],
    search: ["M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13z", "M15.5 15.5L20 20"],
    doc: ["M6 3h8l4 4v14H6z", "M14 3v4h4", "M9 12h6", "M9 16h6"],
    x: ["M6 6l12 12", "M18 6L6 18"],
    copy: ["M9 9h11v11H9z", "M5 15H4V4h11v1"],
    target: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z", "M12 11.5a.5.5 0 1 0 0 1 .5.5 0 0 0 0-1z"],
    beaker: ["M9 3h6", "M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18l-5-9V3", "M7.5 14h9"],
    globe: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M3.5 9h17", "M3.5 15h17", "M12 3c2.5 2.7 3.6 5.7 3.6 9s-1.1 6.3-3.6 9", "M12 3c-2.5 2.7-3.6 5.7-3.6 9s1.1 6.3 3.6 9"],
    archive: ["M4 4h16v4H4z", "M5 8v12h14V8", "M10 12h4"],
    database: ["M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3z", "M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6", "M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"],
    file: ["M6 3h8l4 4v14H6z", "M14 3v4h4"],
    keyboard: ["M3 6h18v12H3z", "M7 10h.01M11 10h.01M15 10h.01M7 14h10"],
    command: ["M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z"],
    flow: ["M5 4h6v6H5z", "M13 14h6v6h-6z", "M8 10v4a3 3 0 0 0 3 3h2"],
    arrow: ["M5 12h14", "M13 6l6 6-6 6"],
    clock: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 7v5l3 2"],
    list: ["M9 6h11", "M9 12h11", "M9 18h11", "M4.5 6h.01M4.5 12h.01M4.5 18h.01"],
  };
  function icon(name, size = 16) {
    const svg = document.createElementNS(SVG, "svg");
    const attrs = { viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor",
      "stroke-width": 1.9, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false",
      class: `icon icon-${name}` };
    for (const [key, value] of Object.entries(attrs)) svg.setAttribute(key, value);
    for (const d of ICONS[name] || []) {
      const shape = document.createElementNS(SVG, "path");
      shape.setAttribute("d", d);
      svg.append(shape);
    }
    return svg;
  }
  function addIcons(more) { Object.assign(ICONS, more || {}); }

  // ---------- network ----------
  const NO_ANSWER = "The studio did not answer.";
  async function api(path, body) {
    const options = body === undefined ? { cache: "no-store" }
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    let response;
    try { response = await fetch(path, options); } catch { return { error: NO_ANSWER, offline: true }; }
    try { return await response.json(); } catch {
      return { error: response.ok ? "The studio sent an answer it could not read." : `The studio answered ${response.status}.` };
    }
  }
  function post(path, body) { return api(path, body || {}); }

  // ---------- events ----------
  const listeners = new Map();
  function on(event, fn) {
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event).add(fn);
    return () => listeners.get(event).delete(fn);
  }
  function emit(event, data) {
    for (const fn of [...(listeners.get(event) || [])]) {
      try { fn(data); } catch (err) { console.error(`A "${event}" listener failed:`, err); }
    }
  }

  // ---------- live regions ----------
  // One polite region (#live) and one assertive region (#alert), both always in the page.
  const lastSaid = { polite: "", assertive: "" };
  function announce(text, politeness = "polite") {
    const id = politeness === "assertive" ? "alert" : "live";
    const box = document.getElementById(id);
    if (!box || !text) return;
    const say = String(text);
    box.textContent = "";
    // A repeat of the same words is only re-read when the region changes in between.
    const again = lastSaid[politeness] === say ? " " : "";
    lastSaid[politeness] = say;
    setTimeout(() => { box.textContent = say + again; }, 60);
  }

  // ---------- toasts ----------
  // Errors stay until dismissed; other toasts leave after a while, but never while hovered or focused.
  const toastsShown = new Map();   // text -> handle, so the same message is not stacked twice
  function toast(message, options = {}) {
    const kind = options.kind || "info";
    const sticky = options.sticky !== undefined ? !!options.sticky : kind === "bad";
    const box = document.getElementById("toasts");
    const text = String(message || "");
    if (!box || !text) return { close() {}, update() {} };
    const same = toastsShown.get(kind + "\n" + text);
    if (same) { same.flash(); return same; }

    const words = h("div", { class: "toast-text" }, text);
    const close = h("button", { class: "btn ghost small square toast-close", type: "button", "aria-label": "Dismiss this message", title: "Dismiss" }, icon("x", 14));
    const action = options.action
      ? h("button", { class: "btn small", type: "button", onclick: () => { try { options.action.run(); } finally { if (!options.action.keep) handle.close(); } } }, options.action.label)
      : null;
    const el = h("div", { class: `toast ${kind}` }, icon(kind === "bad" ? "alert" : kind === "ok" ? "check" : "info", 16), words, action, close);
    let timer = 0;
    let gone = false;
    const handle = {
      el,
      close() {
        if (gone) return;
        gone = true;
        clearTimeout(timer);
        toastsShown.delete(kind + "\n" + text);
        // Keep keyboard focus inside the page when the focused toast goes away.
        if (el.contains(document.activeElement)) {
          const main = document.getElementById("main");
          if (main) main.focus({ preventScroll: true });
        }
        el.classList.add("leaving");
        setTimeout(() => el.remove(), 160);
        if (options.onClose) options.onClose();
      },
      update(next) { words.textContent = String(next); },
      flash() { el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); arm(); },
    };
    function arm() {
      clearTimeout(timer);
      if (sticky || gone) return;
      timer = setTimeout(() => {
        if (el.matches(":hover") || el.contains(document.activeElement)) return arm();
        handle.close();
      }, options.timeout || 6000);
    }
    close.addEventListener("click", () => handle.close());
    toastsShown.set(kind + "\n" + text, handle);
    box.append(el);
    arm();
    if (options.announce !== false) announce(text, kind === "bad" ? "assertive" : "polite");
    return handle;
  }

  // ---------- clipboard ----------
  async function copy(text, button) {
    const value = String(text ?? "");
    let ok = false;
    try { await navigator.clipboard.writeText(value); ok = true; } catch {
      const area = h("textarea", { class: "sr-only", "aria-hidden": "true", tabindex: "-1" });
      area.value = value;
      document.body.append(area);
      area.select();
      try { ok = document.execCommand("copy"); } catch { ok = false; }
      area.remove();
      if (button) button.focus({ preventScroll: true });
    }
    if (button) {
      const label = button.getAttribute("aria-label");
      const title = button.getAttribute("title");
      clearTimeout(button._copyTimer);
      if (button._copyKeep === undefined) button._copyKeep = { label, title, kids: [...button.childNodes] };
      const keep = button._copyKeep;
      button.classList.toggle("copied", ok);
      if (ok) button.replaceChildren(icon("check", 14), ...(keep.kids.some((k) => k.nodeType === 3 && k.textContent.trim()) ? [document.createTextNode("Copied")] : []));
      button.setAttribute("title", ok ? "Copied" : "Could not copy");
      button._copyTimer = setTimeout(() => {
        button.classList.remove("copied");
        button.replaceChildren(...keep.kids);
        if (keep.title === null) button.removeAttribute("title"); else button.setAttribute("title", keep.title);
        button._copyKeep = undefined;
      }, 1500);
    }
    announce(ok ? "Copied" : "Could not copy to the clipboard", ok ? "polite" : "assertive");
    return ok;
  }
  // A small icon button that copies whatever getText() returns.
  function copyButton(getText, label = "Copy") {
    const b = h("button", { class: "btn ghost small square copy-btn", type: "button", "aria-label": label, title: label }, icon("copy", 14));
    b.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); copy(typeof getText === "function" ? getText() : getText, b); });
    return b;
  }

  // ---------- dialogs ----------
  const FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex='-1']), [contenteditable='true']";
  function visible(el) { return !!(el && el.isConnected && (el.offsetWidth || el.offsetHeight || el.getClientRects().length)); }
  function focusables(root) { return [...root.querySelectorAll(FOCUSABLE)].filter((el) => visible(el) && !el.closest("[inert]")); }
  let dialogCount = 0;

  // A sensible place for focus when the element that opened a dialog is gone.
  function fallbackFocus() {
    for (const id of ["title", "emptyTitle", "newSubject", "main"]) {
      const el = document.getElementById(id);
      if (visible(el)) return el;
    }
    return document.body;
  }

  function openDialog({ title, body, actions, wide, onClose, className, hideTitle, describedBy, initialFocus } = {}) {
    const opener = document.activeElement;
    const n = ++dialogCount;
    const titleId = `dialog-title-${n}`;
    const heading = h("h2", { id: titleId, class: hideTitle ? "sr-only" : "modal-title" }, title || "");
    const closeX = h("button", { class: "btn ghost small square modal-x", type: "button", "aria-label": "Close", title: "Close (Esc)" }, icon("x", 15));
    const bodyBox = h("div", { class: "modal-body" }, body || null);
    const foot = actions && actions.length ? h("div", { class: "modal-foot" }, actions) : null;
    const el = h("dialog", { class: `modal${wide ? " wide" : ""}${className ? " " + className : ""}`, "aria-labelledby": titleId, "aria-modal": "true",
      "aria-describedby": describedBy || null },
      hideTitle ? heading : h("div", { class: "modal-head" }, heading, closeX), bodyBox, foot);
    if (hideTitle) closeX.remove();
    let closed = false;
    const handle = {
      el, body: bodyBox, foot,
      close(result) {
        if (closed) return;
        closed = true;
        el.removeEventListener("keydown", trap);
        if (el.open) el.close();
        el.remove();
        let back = opener && opener !== document.body && visible(opener) ? opener : fallbackFocus();
        // With another dialog still open underneath, focus has to stay inside that one.
        const under = [...document.querySelectorAll("dialog[open]")].pop();
        if (under && !under.contains(back)) back = focusables(under)[0] || under;
        try { back.focus(); } catch {}
        if (onClose) { try { onClose(result); } catch (err) { console.error(err); } }
      },
    };
    closeX.addEventListener("click", () => handle.close());
    // Escape (the native cancel) closes through handle.close so onClose and the focus return run.
    el.addEventListener("cancel", (e) => { e.preventDefault(); handle.close(); });
    function trap(e) {
      if (e.key !== "Tab") return;
      const items = focusables(el);
      if (!items.length) { e.preventDefault(); return; }
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || !el.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
    el.addEventListener("keydown", trap);
    document.body.append(el);
    el.showModal();
    const start = (typeof initialFocus === "function" ? initialFocus() : initialFocus)
      || el.querySelector("[autofocus], [data-autofocus]")
      || focusables(bodyBox).find((x) => /^(INPUT|SELECT|TEXTAREA)$/.test(x.tagName))
      || (foot && focusables(foot)[0]) || closeX;
    if (start && start.isConnected) start.focus();
    else { el.setAttribute("tabindex", "-1"); el.focus(); }
    return handle;
  }

  function confirmDialog({ title, message, confirmLabel = "OK", cancelLabel = "Cancel", danger = false } = {}) {
    return new Promise((resolve) => {
      let answer = false;
      const yes = h("button", { class: `btn ${danger ? "danger solid" : "primary"}`, type: "button" }, confirmLabel);
      const no = h("button", { class: "btn", type: "button" }, cancelLabel);
      const msgId = `confirm-msg-${dialogCount + 1}`;
      const text = message instanceof Node ? h("div", { id: msgId, class: "modal-text" }, message) : h("p", { id: msgId, class: "modal-text" }, message || "");
      const d = openDialog({ title, body: text, actions: [no, yes], describedBy: msgId, className: "confirm",
        initialFocus: danger ? no : yes, onClose: () => resolve(answer) });
      yes.addEventListener("click", () => { answer = true; d.close(true); });
      no.addEventListener("click", () => d.close(false));
    });
  }

  // ---------- per-viewer conveniences ----------
  const store = {
    get(key, fallback) {
      try { const raw = localStorage.getItem("re." + key); return raw === null ? fallback : JSON.parse(raw); } catch { return fallback; }
    },
    set(key, value) {
      try { if (value === undefined) localStorage.removeItem("re." + key); else localStorage.setItem("re." + key, JSON.stringify(value)); } catch {}
    },
  };

  // ---------- formatting ----------
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const pad = (n) => String(n).padStart(2, "0");
  function parseTime(iso) {
    if (!iso) return null;
    if (/^\d{1,2}:\d{2}(:\d{2})?$/.test(iso)) return null;   // a bare clock time
    const d = new Date(iso);
    return isNaN(d) ? null : d;
  }
  const fmt = {
    // Short: "14:22" today, "Yesterday 14:22", "26 Oct 14:22" this year, else with the year.
    time(iso) {
      if (!iso) return "";
      if (/^\d{1,2}:\d{2}(:\d{2})?$/.test(iso)) return iso.slice(0, 5);
      const d = parseTime(iso);
      if (!d) return String(iso);
      const now = new Date();
      const clock = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
      const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
      const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      const diff = Math.round((today - day) / 86400000);
      if (diff === 0) return clock;
      if (diff === 1) return `Yesterday ${clock}`;
      if (d.getFullYear() === now.getFullYear()) return `${d.getDate()} ${MONTHS[d.getMonth()]} ${clock}`;
      return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()} ${clock}`;
    },
    // Long, for tooltips: "26 Oct 2026, 14:22:27".
    full(iso) {
      const d = parseTime(iso);
      if (!d) return String(iso || "");
      return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}, ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    },
    bytes(n) {
      const v = Number(n);
      if (!isFinite(v) || v < 0) return "";
      if (v < 1024) return `${v} B`;
      const units = ["KB", "MB", "GB", "TB"];
      let x = v / 1024, i = 0;
      while (x >= 1024 && i < units.length - 1) { x /= 1024; i++; }
      return `${x >= 100 ? Math.round(x) : x.toFixed(1)} ${units[i]}`;
    },
    duration(seconds) {
      const s = Math.max(0, Math.round(Number(seconds) || 0));
      if (s < 60) return `${s} s`;
      if (s < 3600) return `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ""}`;
      const m = Math.floor(s / 60);
      return `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ""}`;
    },
  };

  // ---------- shared state (app.js keeps it current) ----------
  const state = RE.state || {
    page: "subject", slug: null, subjects: [], job: null, runs: {}, checks: null, catalog: null, setup: null, permissions: null,
  };
  function current() { return (state.subjects || []).find((s) => s.slug === state.slug) || null; }

  // ---------- the catalogue (cached; fetched again once REA becomes ready) ----------
  let catalogPromise = null;
  let catalogKey = "";
  function catalog() {
    const c = state.catalog || {};
    const key = `${c.ready}|${c.count}|${c.version || ""}`;
    if (catalogPromise && key !== catalogKey && c.ready !== undefined) catalogPromise = null;
    if (!catalogPromise) {
      catalogKey = key;
      catalogPromise = api("/api/catalog").then((reply) => {
        if (!reply || reply.error || !reply.ready) catalogPromise = null;   // try again next time
        else catalogCache = reply;
        return reply && !reply.error ? reply : { ready: false, count: 0, categories: [], prompts: [], error: reply && reply.error };
      });
    }
    return catalogPromise;
  }
  // The tool's entry from the last catalogue fetched, without waiting (null when not loaded yet).
  let catalogCache = null;
  function toolInfo(name) {
    if (!catalogCache) return null;
    for (const cat of catalogCache.categories || []) for (const t of cat.tools || []) if (t.name === name) return t;
    return null;
  }

  // ---------- running a tool ----------
  function newRunId() {
    const bytes = crypto.getRandomValues(new Uint8Array(6));
    return "run-" + [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  }
  // The one question asked before a state-changing tool runs. Resolves true to run it.
  function confirmRun(slug, name) {
    const tool = toolInfo(name);
    const title = tool && tool.title ? tool.title : name;
    const subject = (state.subjects || []).find((s) => s.slug === slug);
    return confirmDialog({
      title: `Run ${title}?`,
      message: `REA marks ${title} as a tool that can change state, not only read it: the target, a device or `
        + `files on this PC${subject ? ` (it runs on ${subject.name})` : ""}. Run it only if you mean to.`,
      confirmLabel: "Run it", danger: true,
    });
  }
  async function runTool(slug, name, args, { record = true, start = true, source = "tools", confirmed = false, runId } = {}) {
    const run_id = runId || newRunId();
    const body = { slug, name, arguments: args || {}, record, start, source, run_id };
    if (confirmed) body.confirmed = true;
    let reply = await post("/api/tool/run", body);
    if (reply && reply.needs_confirmation && !confirmed) {
      if (!(await confirmRun(slug, name))) return { error: "Not run: you cancelled it.", cancelled: true, run_id };
      reply = await post("/api/tool/run", { ...body, confirmed: true });
    }
    if (reply && typeof reply === "object" && !reply.run_id) reply.run_id = run_id;
    return reply;
  }
  function cancelTool(slug, runId) { return post("/api/tool/cancel", runId ? { slug, run_id: runId } : { slug }); }

  // ---------- the setup checklist ----------
  // One definition, drawn on the empty page, on Settings, and behind the sidebar's "Setup isn't
  // finished" and the subject page's banner. `checks` is /api/state's checks, `setup` its setup
  // flags (Settings passes its freshly saved notice_seen), `catalog` its catalog summary.
  // Each item: {id, label, ok, pending, optional, detail, action (the button's text), missing}.
  function setupItems(checks, setup, catalog) {
    const c = checks || {};
    const st = setup || {};
    const cat = catalog || {};
    const checking = !checks || !!c.checking;
    const known = (check, flag) => (check ? !!check.ok : !!flag);
    const items = [];
    const node = c.node;
    items.push({ id: "node", label: "Node for REA", ok: known(node, st.node_ready), pending: !node && checking,
      action: "Set up Node", missing: "Node isn’t found",
      detail: node ? (node.ok ? `Node ${node.version || ""}${node.how ? ` (${node.how})` : ""}` : node.error || "Not found.") : "Checking…" });
    const rea = c.rea;
    const reaOk = known(rea, st.rea_ready);
    const version = (rea && rea.version) || cat.version || "";
    const tools = (rea && rea.tools) || cat.count || 0;
    const flows = rea && typeof rea.prompts === "number" ? `, ${rea.prompts} workflow${rea.prompts === 1 ? "" : "s"}` : "";
    items.push({ id: "rea", label: "REA built", ok: reaOk, pending: !rea && !cat.ready && checking,
      action: "Build REA", missing: "REA isn’t built",
      detail: reaOk ? `REA${version ? " " + version : ""} · ${tools} tools${flows}`
        : rea && rea.error ? rea.error : rea ? "Not built yet." : "Checking…" });
    const claude = c.claude;
    items.push({ id: "claude", label: "Claude Code signed in", ok: known(claude, st.claude_ready), pending: !claude && checking,
      action: "Sign in", missing: "Claude Code isn’t signed in",
      detail: claude ? (claude.ok ? `Signed in${claude.account ? ` as ${claude.account}` : ""}` : claude.error || "Not signed in.") : "Checking…" });
    items.push({ id: "notice", label: "Responsible use read", ok: !!st.notice_seen, pending: false,
      action: "Read it", missing: "the responsible-use notice isn’t read",
      detail: st.notice_seen ? "Marked as read." : "What the studio is for, and what it sends where." });
    const list = Array.isArray(c.backends) ? c.backends : null;
    if (list) {
      const found = list.filter((b) => b.found || b.in_use).length;
      items.push({ id: "backends", label: "Analysis backends", ok: found > 0, optional: true, action: "Choose backends",
        missing: "", detail: `${found} of ${list.length} found. Optional: each one unlocks more of REA’s tools.` });
    }
    return items;
  }
  // Draw (or update in place) the checklist into `box`. `rows` is the caller's Map of rows by id;
  // `go(id)` is what an item's button does.
  function drawSetup(box, rows, items, go) {
    for (const item of items) {
      let row = rows.get(item.id);
      if (!row) {
        const mark = h("span", { class: "setup-mark", "aria-hidden": "true" });
        const label = h("b", null, item.label);
        const detail = h("span", { class: "setup-detail" });
        const said = h("span", { class: "sr-only" });
        const fix = h("button", { class: "btn small", type: "button", "aria-label": `${item.action} (${item.label.toLowerCase()})` }, item.action);
        fix.addEventListener("click", () => go(item.id));
        row = { li: h("li", { class: "setup-row" }, mark, h("div", { class: "setup-text" }, h("span", null, label, said), detail), fix),
          mark, detail, said, fix, key: "" };
        rows.set(item.id, row);
        box.append(row.li);
      }
      const status = item.pending ? "pending" : item.ok ? "ok" : item.optional ? "optional" : "bad";
      if (row.key !== status) {
        row.key = status;
        row.li.dataset.status = status;
        put(row.mark, icon(status === "ok" ? "check" : status === "pending" ? "clock" : status === "optional" ? "info" : "alert", 15));
        row.said.textContent = status === "ok" ? ": done" : status === "pending" ? ": checking" : status === "optional" ? ": optional" : ": needs attention";
      }
      if (row.detail.textContent !== item.detail) row.detail.textContent = item.detail;
      const want = status === "bad" || status === "optional";
      if (row.fix.hidden === want) {
        if (!want && document.activeElement === row.fix) { row.li.tabIndex = -1; row.li.focus({ preventScroll: true }); }
        row.fix.hidden = !want;
      }
    }
  }
  // What is still missing before the studio is ready (required items only, once checked).
  function setupMissing(items) {
    return items.filter((i) => !i.optional && !i.ok && !i.pending);
  }

  // ---------- search ranking (the Ctrl+K palette and the Tools page) ----------
  // -1 when a word of the search is missing from `hay` (lower case); else lower is better: the
  // label or name starts with the search, a word of the label starts with its first word, the
  // label holds the search, the name holds it, it is somewhere else.
  function matchScore({ label = "", name = "", hay = "" }, words, query) {
    for (const w of words) if (!hay.includes(w)) return -1;
    const l = String(label).toLowerCase();
    const n = String(name).toLowerCase();
    const asName = query.replace(/\s+/g, "_");
    if (l.startsWith(query) || (n && n.startsWith(asName))) return 0;
    if (words.length && l.split(/[\s_\-·]+/).some((part) => part.startsWith(words[0]))) return 1;
    if (l.includes(query)) return 2;
    if (n && n.includes(asName)) return 2.5;
    return 3;
  }

  Object.assign(RE, {
    h, put, icon, addIcons, api, post, on, emit, announce, toast, copy, copyButton,
    dialog: { open: openDialog }, confirm: confirmDialog, store, fmt, state, current, catalog, toolInfo, newRunId,
    runTool, confirmRun, cancelTool, matchScore, setupItems, drawSetup, setupMissing, focusables, visible,
    // app.js replaces these two once it starts; until then they do the obvious thing.
    refresh: RE.refresh || (() => Promise.resolve()),
    go: RE.go || ((hash) => { location.hash = String(hash || "").replace(/^#/, ""); }),
  });
})();
