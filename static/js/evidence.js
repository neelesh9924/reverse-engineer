// RE.evidence: how a tool result looks wherever it shows (a Findings card, a Tools-page run, a
// follow-up page inside a card). Built from DOM nodes and textContent only: results carry text
// from the target, and none of it may become markup. Shapes REA returns get their own view
// (images, hex dumps, call graphs, comparisons, line diffs, paged summaries); anything else falls
// to a key/value view. No field is dropped without saying so: REA treats missing data as unknown,
// never as absent, and the views must not suggest otherwise.
(() => {
  "use strict";
  const RE = window.RE;
  const h = (...args) => RE.h(...args);

  const TABLE_PAGE = 200;     // rows drawn at a time
  const TABLE_COLS = 8;       // columns shown before "Show all columns"
  const TABLE_FILTER = 20;    // rows before a table gets a filter box
  const INLINE_LIST = 16;     // short scalar lists show inline; longer ones become a table
  const OBJECT_KEYS = 60;     // fields drawn before "Show more fields"
  const MAX_DEPTH = 6;        // nesting drawn as key/value before falling back to JSON
  const HEX_SHOWN = 4096;     // bytes in a hex dump before "Show all"
  const GRAPH_NODES = 2000;   // call-graph rows drawn
  const JSON_SHOWN = 200000;  // characters of JSON drawn in a raw view (copying takes all)
  const DIFF_CELLS = 4e6;     // LCS work before a diff falls back to removed-then-added

  // ---------- small helpers ----------
  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  const isScalar = (v) => v === null || ["string", "number", "boolean"].includes(typeof v);
  const fmtN = (n) => Number(n).toLocaleString();
  const plural = (n, one, many = `${one}s`) => `${fmtN(n)} ${n === 1 ? one : many}`;
  const shortId = (id) => (typeof id === "string" && id.length > 18 ? `${id.slice(0, 14)}…` : String(id));
  const shortHash = (s) => (typeof s === "string" && s.length > 16 ? `${s.slice(0, 12)}…` : String(s));
  const humanKey = (k) => String(k || "").replace(/[_-]+/g, " ").trim();
  const fmtBytes = (n) => (RE.fmt && RE.fmt.bytes ? RE.fmt.bytes(n) : plural(n, "byte"));
  const fmtTime = (iso) => { try { return RE.fmt.time(iso); } catch { return iso || ""; } };
  const fullTime = (iso) => { try { return RE.fmt.full ? RE.fmt.full(iso) : iso; } catch { return iso || ""; } };
  const COLLATOR = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
  let seq = 0;
  const uid = (p) => `re-ev-${p}-${++seq}`;

  function icon(name, size = 14, cls) {
    const svg = RE.icon(name, size);
    if (cls) svg.classList.add(cls);
    return svg;
  }
  function compactJson(v) {
    try { return JSON.stringify(v, b64Replacer) ?? String(v); } catch { return String(v); }
  }
  function label(text) { return h("div", { class: "ev-section-label" }, text); }
  function slugOf(opts) { return (opts && opts.slug) || (RE.state && RE.state.slug) || null; }

  function copyButton(getText, name, text = "Copy") {
    return h("button", { type: "button", class: "btn ghost small copy-btn", "aria-label": name, title: name,
      onclick: (e) => RE.copy(typeof getText === "function" ? getText() : getText, e.currentTarget) }, icon("re-copy", 14), text);
  }

  // ---------- status colours ----------
  // REA's own posture: "unknown" is never folded into "unchanged" or "ok", so it always reads as a caution.
  const STATUS = {
    ok: ["ok", "complete", "completed", "matched", "unchanged", "pass", "passed", "observed", "present", "identical", "resolved",
      "closed", "success", "succeeded", "available", "complete-within-view", "complete-within-inputs", "complete_within_window",
      "aligned", "selected", "observed_unchanged", "running", "done"],
    accent: ["changed", "modified", "added", "removed", "different", "derived", "split", "merged", "duplicated", "history_advanced",
      "observed_change", "observed_changed", "derived_relationship"],
    warn: ["unknown", "partial", "ambiguous", "truncated", "inferred", "unresolved", "mismatched", "page", "limited", "policy_filtered",
      "attach_limited", "dimension_mismatch", "missing", "unavailable", "not_observed", "pending", "history_diverged",
      "unresolved_branch", "open", "starting", "empty", "cancelled"],
    bad: ["error", "fail", "failed", "contradiction", "crashed", "invalid", "rejected"],
  };
  const STATUS_OF = new Map(Object.entries(STATUS).flatMap(([kind, list]) => list.map((s) => [s, kind])));
  const statusKind = (v) => STATUS_OF.get(String(v).toLowerCase()) || "";
  const STATUS_KEYS = /^(status|state|classification|verdict|conclusion_kind|overall_status|behavior_status|goal_status|outcome|confidence|metadata_status|inventory_state)$/;
  function chip(text, kind, title) { return h("span", { class: `chip ${kind || ""}`.trim(), title }, String(text)); }
  const statusChip = (v) => chip(v, statusKind(v));

  const CONFIDENCE = {
    observed: ["ok", "seen directly in the target"],
    derived: ["accent", "computed from observed facts"],
    inferred: ["warn", "a likely reading, not proven"],
  };
  function confidenceOf(ev) {
    if (!ev || ev.confidence === undefined || ev.confidence === null) return null;
    const c = ev.confidence;
    return isObj(c) ? String(c.level ?? c.value ?? compactJson(c)) : String(c);
  }
  const AUTHORITY = {
    "shipped-artifact": "the shipped artifact itself",
    "controlled-replay": "a controlled replay",
    "historical-reference": "a historical reference",
    "external-service": "an external service",
    "analyst-inference": "analyst inference",
  };

  // ---------- base64 images ----------
  const B64 = /^[A-Za-z0-9+/]+={0,2}$/;
  const RASTER = /^image\/(png|jpeg|jpg|webp|gif)$/i;
  const MAGIC = /^(iVBORw0KGgo|\/9j\/|R0lGOD|UklGR)/;
  const cleanB64 = (s) => (typeof s === "string" ? s.replace(/\s+/g, "") : "");
  const isB64 = (s) => s.length >= 16 && s.length % 4 !== 1 && B64.test(s);

  function sniffImage(b64) {
    try {
      const head = atob(b64.slice(0, 24));
      if (head.charCodeAt(0) === 0x89 && head.slice(1, 4) === "PNG") return "image/png";
      if (head.charCodeAt(0) === 0xff && head.charCodeAt(1) === 0xd8) return "image/jpeg";
      if (head.startsWith("GIF8")) return "image/gif";
      if (head.startsWith("RIFF") && head.slice(8, 12) === "WEBP") return "image/webp";
    } catch { /* not base64 */ }
    return null;
  }
  // A bare string that is a whole base64 raster image (PNG, JPEG, GIF or WebP), or null.
  function imageFromString(s) {
    if (typeof s !== "string" || s.length < 64 || !MAGIC.test(s)) return null;
    const b64 = cleanB64(s);
    if (!isB64(b64)) return null;
    const mime = sniffImage(b64);
    return mime ? { b64, mime } : null;
  }
  const IMAGE_DATA_KEYS = ["data_base64", "content_base64", "bytes_base64", "base64", "image", "data", "png", "screenshot"];
  const MEDIA_KEYS = ["media_type", "mime", "mime_type", "mimeType", "content_type"];
  // An object carrying a base64 raster image under a data key with a media type beside it.
  function imageInObject(obj) {
    const media = MEDIA_KEYS.map((k) => obj[k]).find((v) => typeof v === "string");
    for (const key of IMAGE_DATA_KEYS) {
      const v = obj[key];
      if (typeof v !== "string" || v.length < 16) continue;
      const b64 = cleanB64(v);
      if (!isB64(b64)) continue;
      const mime = media && RASTER.test(media) ? media.toLowerCase().replace("jpg", "jpeg") : sniffImage(b64);
      if (mime && RASTER.test(mime) && sniffImage(b64)) return { key, b64, mime };
    }
    return null;
  }
  function b64Replacer(key, v) {
    if (typeof v === "string" && v.length >= 64 && (key === "data_base64" || imageFromString(v))) {
      return `‹base64 image, about ${fmtBytes(Math.floor((v.length * 3) / 4))}›`;
    }
    return v;
  }

  // ---------- JSON and code ----------
  function jsonBlock(value, name) {
    let text;
    try { text = JSON.stringify(value, b64Replacer, 2) ?? String(value); } catch { text = String(value); }
    const cut = text.length > JSON_SHOWN;
    const pre = h("pre", { class: "json", tabindex: "0", role: "region", "aria-label": `${humanKey(name) || "Result"} as JSON` },
      cut ? text.slice(0, JSON_SHOWN) : text);
    const full = () => { try { return JSON.stringify(value, null, 2); } catch { return text; } };
    return h("div", { class: "code-block" },
      h("div", { class: "code-head" }, h("span", { class: "code-lang" }, "json"),
        cut ? h("span", { class: "ev-null" }, `showing the first ${fmtBytes(JSON_SHOWN)}; Copy takes all of it`) : null,
        copyButton(full, `Copy ${humanKey(name) || "result"} as JSON`)),
      pre);
  }
  const codeBlock = (text, name) => RE.markdown.codeBlock(text, name);

  // ---------- generic values ----------
  function renderValue(value, ctx) {
    const c = Object.assign({ depth: 0, key: "", root: value, opts: {}, tool: "" }, ctx || {});
    for (const r of RENDERERS) {
      if (r.match(value, c)) return r.render(value, c);
    }
    return generic(value, c);
  }
  const child = (ctx, key, parent) => ({ ...ctx, depth: ctx.depth + 1, key, parent });

  function generic(value, ctx) {
    if (value === undefined) return h("span", { class: "ev-null" }, "not given");
    if (value === null) return h("span", { class: "ev-null mono" }, "null");
    if (typeof value === "boolean") return h("span", { class: "mono ev-bool" }, String(value));
    if (typeof value === "number") return h("span", { class: "mono" }, String(value));
    if (typeof value === "string") return stringView(value, ctx);
    if (Array.isArray(value)) return arrayView(value, ctx);
    if (isObj(value)) return objectView(value, ctx);
    return h("span", null, String(value));
  }

  function stringView(s, ctx) {
    if (s === "") return h("span", { class: "ev-null" }, "empty text");
    if (s.includes("\n") || s.length > 200) {
      const kind = /pseudo/.test(ctx.tool) ? "pseudocode" : /assembl|disassembl|instruction/.test(ctx.tool) ? "assembly" : "text";
      return codeBlock(s, ctx.key || kind);
    }
    if (STATUS_KEYS.test(ctx.key) && statusKind(s)) return statusChip(s);
    return h("span", { class: /\s/.test(s) ? "ev-str" : "ev-str mono" }, s);
  }

  const BULLET_KEYS = /(^|_)(limitations|warnings|messages|unknowns|notes|diagnostics|reasons|caveats)$/;
  function arrayView(arr, ctx) {
    if (!arr.length) return h("span", { class: "ev-null" }, "empty list");
    if (arr.every((v) => typeof v === "string") && BULLET_KEYS.test(ctx.key)) return bulletList(arr);
    if (arr.every(isScalar)) {
      const short = arr.length <= INLINE_LIST && arr.every((v) => String(v).length <= 80 && !String(v).includes("\n"));
      if (short) return h("ul", { class: "ev-inline-list" }, arr.map((v) => h("li", null, generic(v, child(ctx, ctx.key, arr)))));
      return tableView(arr.map((v) => ({ value: v })), ctx);
    }
    if (arr.every(isObj)) return tableView(arr, ctx);
    if (arr.every((v) => Array.isArray(v) && v.every(isScalar))) {
      return tableView(arr.map((row) => Object.fromEntries(row.map((v, k) => [`#${k + 1}`, v]))), ctx);
    }
    const shown = arr.slice(0, TABLE_PAGE);
    return h("ol", { class: "ev-mixed", start: "0" },
      shown.map((v, k) => h("li", null, renderValue(v, child(ctx, `${ctx.key}[${k}]`, arr)))),
      arr.length > shown.length ? h("li", { class: "ev-null" }, `${plural(arr.length - shown.length, "more item")}: copy the JSON or see the raw result`) : null);
  }

  function bulletList(lines) {
    const ul = h("ul", { class: "ev-bullets", tabindex: "-1" });
    const draw = (n) => ul.replaceChildren(...lines.slice(0, n).map((l) => h("li", null, l)));
    draw(10);
    if (lines.length <= 10) return ul;
    const more = h("button", { type: "button", class: "btn ghost small", onclick: () => { draw(lines.length); more.remove(); ul.focus(); } },
      `Show all ${fmtN(lines.length)}`);
    return h("div", null, ul, more);
  }

  // `skip` names fields a specific view already showed; the rest are drawn here.
  function objectView(obj, ctx, skip) {
    const entries = Object.entries(obj).filter(([k]) => !(skip && skip.has(k)));
    if (!entries.length) return skip ? null : h("span", { class: "ev-null" }, "empty object");
    if (ctx.depth >= MAX_DEPTH) return jsonBlock(Object.fromEntries(entries), ctx.key);
    const dl = h("dl", { class: ctx.depth ? "ev-kv nested" : "ev-kv" });
    const draw = (list) => {
      for (const [k, v] of list) dl.append(h("dt", { class: "mono" }, k), h("dd", null, renderValue(v, child(ctx, k, obj))));
    };
    draw(entries.slice(0, OBJECT_KEYS));
    if (entries.length <= OBJECT_KEYS) return dl;
    const more = h("button", { type: "button", class: "btn ghost small", onclick: () => {
      draw(entries.slice(OBJECT_KEYS)); more.remove();
      const next = dl.children[OBJECT_KEYS * 2];
      if (next) { next.tabIndex = -1; next.focus(); }
    } }, `Show ${plural(entries.length - OBJECT_KEYS, "more field")}`);
    return h("div", null, dl, more);
  }

  // A specific view followed by whatever fields it did not cover.
  function withRest(lead, obj, ctx, skip) {
    const rest = objectView(obj, ctx, new Set(skip));
    return rest ? h("div", { class: "ev-stack" }, lead, rest) : lead;
  }

  // ---------- tables ----------
  function cellText(v) {
    if (v === undefined) return "";
    if (v === null) return "null";
    if (typeof v === "object") return compactJson(v);
    return String(v);
  }
  function deltaText(d) {
    const bits = [`+${fmtN(d.added_lines)}`, `−${fmtN(d.removed_lines)} lines`];
    if (typeof d.hunks === "number") bits.push(plural(d.hunks, "hunk"));
    return bits.join(" ");
  }
  function cellNode(v, key) {
    if (v === undefined) return "";
    if (v === null) return h("span", { class: "ev-null" }, "null");
    if (typeof v === "string") {
      if (STATUS_KEYS.test(key) && statusKind(v)) return statusChip(v);
      if (imageFromString(v)) return h("span", { class: "ev-null" }, "‹base64 image›");
      return v.length > 300 ? h("span", { title: v.slice(0, 2000) }, `${v.slice(0, 300)}…`) : v;
    }
    if (typeof v !== "object") return String(v);
    if (isObj(v) && typeof v.added_lines === "number" && typeof v.removed_lines === "number") return deltaText(v);
    if (Array.isArray(v) && !v.length) return h("span", { class: "ev-null" }, "[]");
    if (Array.isArray(v) && v.length <= 6 && v.every((x) => isScalar(x) && String(x).length < 40)) return v.map(String).join(", ");
    const json = compactJson(v);
    return json.length > 120 ? h("span", { class: "rt-json", title: json.slice(0, 2000) }, `${json.slice(0, 117)}…`) : h("span", { class: "rt-json" }, json);
  }
  function sortKey(v) {
    if (typeof v === "number") return v;
    if (typeof v === "boolean") return v ? 1 : 0;
    if (typeof v === "string") {
      const t = v.trim();
      if (/^0x[0-9a-f]+$/i.test(t)) { try { return BigInt(t); } catch { /* fall through */ } }
      if (/^-?\d+(\.\d+)?$/.test(t)) return Number(t);
      return t;
    }
    return compactJson(v);
  }
  function compareCells(a, b) {
    const x = sortKey(a), y = sortKey(b);
    const xn = typeof x === "number" || typeof x === "bigint", yn = typeof y === "number" || typeof y === "bigint";
    if (xn && yn) return x < y ? -1 : x > y ? 1 : 0;
    if (xn !== yn) return xn ? -1 : 1;
    return COLLATOR.compare(x, y);
  }
  function toCsv(cols, rows) {
    const esc = (s) => (/[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
    return [cols.map(esc).join(","), ...rows.map((r) => cols.map((c) => esc(cellText(r[c]))).join(","))].join("\n");
  }

  function tableView(input, ctx) {
    // inspect_analysis_view pages wrap each row as {source_index, record}; show the record's fields
    let rows = input;
    if (rows.length && rows.every((r) => isObj(r.record) && Object.keys(r).every((k) => k === "record" || k === "source_index"))) {
      rows = rows.map((r) => ({ source_index: r.source_index, ...r.record }));
    }
    const columns = [];
    const seen = new Set();
    for (const row of rows) for (const k of Object.keys(row)) if (!seen.has(k)) { seen.add(k); columns.push(k); }
    // Columns empty in every row, then digests and ids, go last, so the first eight carry the content.
    const isEmpty = (v) => v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length) || (isObj(v) && !Object.keys(v).length);
    const rank = (c) => (rows.every((r) => isEmpty(r[c])) ? 2 : /(digest|sha256|^id$|_id$)/.test(c) ? 1 : 0);
    const ranks = new Map(columns.map((c) => [c, rank(c)]));
    columns.sort((a, b) => ranks.get(a) - ranks.get(b));
    const st = { sort: null, dir: 1, filter: "", limit: TABLE_PAGE, allCols: columns.length <= TABLE_COLS };
    const name = humanKey(ctx.key) || "Result";
    const id = uid("table");
    const texts = new WeakMap();
    let view = rows;

    const status = h("p", { class: "rt-status", id: `${id}-status` });
    const thead = h("thead");
    const tbody = h("tbody");
    const table = h("table", { class: "rtable", "aria-describedby": `${id}-status` }, thead, tbody);
    const wrap = h("div", { class: "table-wrap", role: "region", "aria-label": `${name} table`, tabindex: "0" }, table);
    const moreBtn = h("button", { type: "button", class: "btn small", onclick: () => { st.limit += TABLE_PAGE; afterMore(); } });
    const allBtn = h("button", { type: "button", class: "btn ghost small", onclick: () => { st.limit = Infinity; afterMore(); } });
    const more = h("div", { class: "rt-more" }, moreBtn, allBtn);
    const colsBtn = columns.length > TABLE_COLS
      ? h("button", { type: "button", class: "btn ghost small", "aria-pressed": "false", onclick: () => {
          st.allCols = !st.allCols; colsBtn.setAttribute("aria-pressed", String(st.allCols)); drawHead(); drawBody();
        } }, `Show all ${columns.length} columns`)
      : null;

    function rowText(row) {
      let t = texts.get(row);
      if (t === undefined) { t = columns.map((c) => cellText(row[c])).join("\u0001").toLowerCase(); texts.set(row, t); }
      return t;
    }
    function compute() {
      const f = st.filter.trim().toLowerCase();
      view = f ? rows.filter((r) => rowText(r).includes(f)) : rows.slice();
      if (st.sort !== null) {
        const c = st.sort;
        view.sort((a, b) => {
          const am = a[c] === undefined, bm = b[c] === undefined;
          if (am || bm) return am === bm ? 0 : am ? 1 : -1; // missing values always last
          return st.dir * compareCells(a[c], b[c]);
        });
      }
    }
    const shownCols = () => (st.allCols ? columns : columns.slice(0, TABLE_COLS));
    function drawHead() {
      thead.replaceChildren(h("tr", null, shownCols().map((c) => {
        const th = h("th", { scope: "col", "data-col": c },
          h("button", { type: "button", class: "rt-sort", title: `Sort by ${c}`, onclick: () => sortBy(c) },
            h("span", null, c), h("span", { class: "rt-arrow", "aria-hidden": "true" })));
        return th;
      })));
      markSort();
    }
    function markSort() {
      for (const th of thead.querySelectorAll("th")) {
        const on = th.dataset.col === st.sort;
        if (on) th.setAttribute("aria-sort", st.dir > 0 ? "ascending" : "descending");
        else th.removeAttribute("aria-sort");
        th.querySelector(".rt-arrow").textContent = on ? (st.dir > 0 ? "▲" : "▼") : "";
      }
    }
    function sortBy(c) {
      if (st.sort === c) st.dir = -st.dir; else { st.sort = c; st.dir = 1; }
      compute(); markSort(); drawBody();
    }
    function drawBody() {
      const cols = shownCols();
      const slice = view.slice(0, st.limit);
      tbody.replaceChildren(...slice.map((row) => h("tr", null, cols.map((c) => h("td", null, cellNode(row[c], c))))));
      if (!slice.length) tbody.append(h("tr", null, h("td", { colspan: String(cols.length || 1), class: "ev-null" }, "No rows match the filter.")));
      const filtered = st.filter.trim() ? ` matching the filter (${fmtN(rows.length)} in all)` : "";
      const colText = cols.length < columns.length ? ` · ${cols.length} of ${columns.length} columns` : "";
      status.textContent = `Showing ${fmtN(slice.length)} of ${plural(view.length, "row")}${filtered}${colText}`;
      const left = view.length - slice.length;
      more.hidden = left <= 0;
      moreBtn.textContent = `Show ${fmtN(Math.min(TABLE_PAGE, left))} more`;
      allBtn.hidden = left <= TABLE_PAGE;
      allBtn.textContent = `Show all ${fmtN(view.length)}`;
    }
    function afterMore() {
      const hadFocus = more.contains(document.activeElement);
      drawBody();
      if (hadFocus && more.hidden) wrap.focus();
    }

    const tools = h("div", { class: "rt-bar" });
    if (rows.length > TABLE_FILTER) {
      const fid = `${id}-filter`;
      const input = h("input", { class: "input rt-filter", type: "search", id: fid, placeholder: "Filter rows", autocomplete: "off",
        spellcheck: "false", "aria-describedby": `${id}-status` });
      let timer = 0;
      input.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(() => { st.filter = input.value; st.limit = TABLE_PAGE; compute(); drawBody(); }, rows.length > 2000 ? 200 : 60);
      });
      tools.append(h("label", { class: "sr-only", for: fid }, `Filter the ${name} rows`), input);
    }
    tools.append(...[h("span", { class: "grow" }), colsBtn,
      copyButton(() => toCsv(columns, view), `Copy ${name} as CSV`, "CSV"),
      copyButton(() => JSON.stringify(view, null, 2), `Copy ${name} as JSON`, "JSON")].filter(Boolean));

    compute(); drawHead(); drawBody();
    return h("div", { class: "rt" }, tools, status, wrap, more);
  }

  // ---------- images ----------
  function altFor(ctx) {
    const root = isObj(ctx.root) ? ctx.root : {};
    const url = (isObj(root.target) && root.target.url) || root.url;
    const shot = /screen/.test(ctx.tool || "");
    if (url) return `${shot ? "Screenshot" : "Image"} of ${url}`;
    return shot ? "Screenshot from the result" : `Image from ${humanKey(ctx.key) || "the result"}`;
  }
  function saveImage(b64, mime, fileName) {
    try {
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let k = 0; k < bin.length; k++) bytes[k] = bin.charCodeAt(k);
      const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
      const a = h("a", { href: url, download: fileName, hidden: true });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      RE.toast(`Downloading ${fileName}.`, { kind: "ok" });
    } catch {
      RE.toast("This image could not be saved.", { kind: "bad" });
    }
  }
  function imageView(img, info, ctx) {
    const { b64, mime } = img;
    const root = isObj(ctx.root) ? ctx.root : {};
    const vp = isObj(root.viewport) ? root.viewport : {};
    const width = info.width ?? vp.width, height = info.height ?? vp.height;
    const pad = b64.endsWith("==") ? 2 : b64.endsWith("=") ? 1 : 0;
    const size = typeof info.bytes === "number" ? info.bytes : Math.floor((b64.length * 3) / 4) - pad;
    const sha = typeof info.sha256 === "string" ? info.sha256 : null;
    const facts = [];
    if (width && height) facts.push(`${width} × ${height}`);
    facts.push(fmtBytes(size), mime);
    const pic = h("img", { class: "ev-img", src: `data:${mime};base64,${b64}`, alt: altFor(ctx), decoding: "async" });
    const frame = h("div", { class: "ev-img-frame" }, pic);
    const actual = h("button", { type: "button", class: "btn small", "aria-pressed": "false", onclick: () => {
      const on = frame.classList.toggle("actual");
      actual.setAttribute("aria-pressed", String(on));
      if (on) { frame.tabIndex = 0; frame.setAttribute("role", "region"); frame.setAttribute("aria-label", "Image at actual size"); }
      else { frame.removeAttribute("tabindex"); frame.removeAttribute("role"); frame.removeAttribute("aria-label"); }
    } }, "Actual size");
    const ext = mime.split("/")[1].replace("jpeg", "jpg");
    const fileName = `${(ctx.tool || "image").replace(/[^\w-]+/g, "-")}-${sha ? sha.slice(0, 12) : Date.now()}.${ext}`;
    const save = h("button", { type: "button", class: "btn small", onclick: () => saveImage(b64, mime, fileName) }, icon("re-download"), "Save image");
    return h("figure", { class: "ev-figure" }, frame,
      h("figcaption", null, h("span", null, facts.join(" · ")), sha ? h("span", { class: "mono", title: sha }, ` · sha256 ${shortHash(sha)}`) : null),
      h("div", { class: "ev-actions" }, actual, save, sha ? copyButton(sha, "Copy the image's sha256", "sha256") : null));
  }

  // ---------- hex dumps ----------
  const DUMP_KEY = /(^|_)(bytes|bytecode|raw|data|memory|value|constant)_hex$|^hex$/;
  const ID_KEY = /(digest|hash|serial|crc|_id|identifier|key|sha)/;
  function hexKeys(obj) {
    const keys = [];
    for (const [k, v] of Object.entries(obj)) {
      if (!/(^|_)hex$/.test(k) || typeof v !== "string" || v.length < 2 || v.length % 2 || !/^[0-9a-fA-F]+$/.test(v)) continue;
      if (DUMP_KEY.test(k) || (v.length >= 64 && !ID_KEY.test(k))) keys.push(k);
    }
    if (Array.isArray(obj.bytes) && obj.bytes.length && obj.bytes.every((b) => Number.isInteger(b) && b >= 0 && b <= 255)) keys.push("bytes");
    return keys;
  }
  function parseAddress(v) {
    try {
      if (typeof v === "number" && Number.isSafeInteger(v) && v >= 0) return BigInt(v);
      if (typeof v === "string") {
        const t = v.trim();
        if (/^0x[0-9a-f]+$/i.test(t)) return BigInt(t);
        if (/^\d+$/.test(t)) return BigInt(t);
      }
    } catch { /* not an address */ }
    return null;
  }
  function toBytes(v) {
    if (Array.isArray(v)) return Uint8Array.from(v);
    const out = new Uint8Array(v.length / 2);
    for (let k = 0; k < out.length; k++) out[k] = parseInt(v.substr(k * 2, 2), 16);
    return out;
  }
  function dumpText(bytes, base, n) {
    const start = base ?? 0n;
    const width = Math.max(8, (start + BigInt(n)).toString(16).length);
    const lines = [];
    for (let off = 0; off < n; off += 16) {
      let hex = "", ascii = "";
      for (let k = 0; k < 16; k++) {
        if (k === 8) hex += " ";
        const b = off + k < n ? bytes[off + k] : undefined;
        if (b === undefined) { hex += "   "; continue; }
        hex += `${b.toString(16).padStart(2, "0")} `;
        ascii += b >= 0x20 && b < 0x7f ? String.fromCharCode(b) : ".";
      }
      lines.push(`${(start + BigInt(off)).toString(16).padStart(width, "0")}  ${hex} |${ascii}|`);
    }
    return lines.join("\n");
  }
  function hexView(obj, key, ctx) {
    const raw = obj[key];
    const bytes = toBytes(raw);
    const base = parseAddress(obj.address ?? obj.start ?? obj.offset);
    const total = bytes.length;
    let shown = Math.min(total, HEX_SHOWN);
    const where = base !== null ? ` from 0x${base.toString(16)}` : "";
    const pre = h("pre", { class: "code hexdump", tabindex: "0", role: "region", "aria-label": `Hex dump of ${humanKey(key)}, ${plural(total, "byte")}${where}` });
    const draw = () => { pre.textContent = dumpText(bytes, base, shown); };
    draw();
    const head = h("div", { class: "code-head" }, h("span", { class: "code-lang" }, `${humanKey(key)} · ${plural(total, "byte")}${where}`));
    const partial = obj.complete === false
      || (typeof obj.requested_bytes === "number" && typeof obj.returned_bytes === "number" && obj.returned_bytes < obj.requested_bytes);
    if (partial) {
      const of = typeof obj.requested_bytes === "number" ? ` of ${fmtN(obj.requested_bytes)}` : "";
      head.append(chip(`partial: ${fmtN(total)}${of} bytes`, "warn", "REA read fewer bytes than were asked for. The rest is unknown, not zero."));
    }
    const hexText = () => (typeof raw === "string" ? raw : Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(""));
    head.append(h("span", { class: "grow" }), copyButton(hexText, `Copy ${humanKey(key)} as hex`, "Hex"),
      copyButton(() => dumpText(bytes, base, total), `Copy the ${humanKey(key)} dump`, "Dump"));
    const box = h("div", { class: "code-block" }, head, pre);
    if (total > shown) {
      const all = h("button", { type: "button", class: "btn ghost small", onclick: () => { shown = total; draw(); all.remove(); pre.focus(); } },
        `Showing ${fmtN(shown)} of ${plural(total, "byte")}: show all`);
      box.append(h("div", { class: "rt-more" }, all));
    }
    return box;
  }

  // ---------- call graphs ----------
  function graphOf(v) {
    const nodes = new Map();
    const roots = [];
    const add = (n, isRoot) => {
      if (!isObj(n) || n.address === undefined || n.address === null) return;
      const a = String(n.address);
      if (!nodes.has(a)) nodes.set(a, n);
      if (isRoot && !roots.includes(a)) roots.push(a);
    };
    if (isObj(v.graph)) {
      const levels = Object.keys(v.graph).sort((a, b) => (parseFloat(a) - parseFloat(b)) || COLLATOR.compare(a, b));
      levels.forEach((lvl, k) => { for (const n of v.graph[lvl] || []) add(n, k === 0); });
    } else if (Array.isArray(v.edges)) {
      for (const [from, to] of v.edges) {
        const a = String(from);
        if (!nodes.has(a)) nodes.set(a, { address: a, calls: [] });
        nodes.get(a).calls.push(String(to));
      }
      roots.push(String(v.root));
    } else if (Array.isArray(v.rows)) {
      const rows = v.rows.filter((r) => isObj(r) && r.address !== undefined);
      const levels = [...new Set(rows.map((r) => String(r.level)))].sort((a, b) => (parseFloat(a) - parseFloat(b)) || COLLATOR.compare(a, b));
      for (const r of rows) add(r, String(r.level) === levels[0]);
    }
    if (!roots.length && nodes.size) roots.push(nodes.keys().next().value);
    return { nodes, roots };
  }
  function isGraph(v) {
    if (!isObj(v)) return false;
    if (isObj(v.graph)) {
      const lists = Object.values(v.graph);
      return lists.length > 0 && lists.every(Array.isArray) && lists.some((l) => l.some((n) => isObj(n) && "address" in n));
    }
    if (typeof v.root === "string" && Array.isArray(v.edges)) return v.edges.every((e) => Array.isArray(e) && e.length >= 2);
    return v.type === "graph-nodes" && Array.isArray(v.rows);
  }
  function graphView(v, ctx) {
    const g = graphOf(v);
    const expanded = new Set();
    let drawn = 0, cut = 0, edges = 0;
    const outline = [];
    for (const n of g.nodes.values()) edges += Array.isArray(n.calls) ? n.calls.length : 0;

    function badges(n) {
      const out = [];
      if (!n) return out;
      if (n.status === "error") out.push(chip("error", "bad", isObj(n.error) ? n.error.message : "REA could not read this function."));
      const cov = isObj(n.relationship_coverage) ? n.relationship_coverage.status : null;
      if (cov && cov !== "complete") out.push(chip(`coverage ${cov}`, "warn", n.relationship_coverage.reason || "Some calls may not be listed."));
      const lims = Array.isArray(n.limitations) ? n.limitations : Array.isArray(n.limitation_ids) ? n.limitation_ids : [];
      if (lims.length) out.push(chip(lims.length === 1 ? "1 limit" : `${lims.length} limits`, "warn", lims.map(String).join("\n")));
      return out;
    }
    function leafNote(n) {
      if (!n) return "not read";
      if (n.status === "error") return isObj(n.error) && n.error.message ? n.error.message : "";
      const cov = isObj(n.relationship_coverage) ? n.relationship_coverage.status : null;
      return cov === "complete" ? "calls nothing (coverage complete)" : "no calls observed; that does not prove there are none";
    }
    function nodeEl(addr, path, depth) {
      if (drawn >= GRAPH_NODES) { cut++; return null; }
      drawn++;
      const n = g.nodes.get(addr);
      const head = h("span", { class: "cg-node" }, h("span", { class: "mono cg-addr" }, addr), ...badges(n));
      const note = (text) => { outline.push(`${"  ".repeat(depth)}${addr}${text ? `  (${text})` : ""}`); return h("li", null, head, text ? h("span", { class: "cg-note" }, text) : null); };
      if (!n) return note("not read");
      if (path.has(addr)) return note("calls back up this path");
      if (expanded.has(addr)) return note("expanded above");
      expanded.add(addr);
      const calls = Array.isArray(n.calls) ? n.calls.map(String) : [];
      if (!calls.length) return note(leafNote(n));
      outline.push(`${"  ".repeat(depth)}${addr}`);
      const next = new Set(path).add(addr);
      const kids = calls.map((c) => nodeEl(c, next, depth + 1)).filter(Boolean);
      return h("li", null, h("details", { class: "cg-branch re-disc", open: depth < 2 },
        h("summary", null, icon("re-caret", 14, "caret"), head, h("span", { class: "cg-count" }, plural(calls.length, "call"))),
        h("ul", { class: "cg-list" }, kids)));
    }
    const tree = h("ul", { class: "cg-tree", "aria-label": "Call graph" }, g.roots.map((r) => nodeEl(r, new Set(), 0)).filter(Boolean));
    const head = h("div", { class: "code-head" },
      h("span", { class: "code-lang" }, `Call graph · ${plural(g.nodes.size, "function")} read · ${plural(edges, "call")}`),
      h("span", { class: "grow" }), copyButton(() => outline.join("\n"), "Copy the call graph as an outline", "Outline"));
    const box = h("div", { class: "code-block cg" }, head, h("div", { class: "cg-body" }, tree));
    if (cut) box.append(h("p", { class: "rt-status" }, `${plural(cut, "more row")} not drawn; the raw result and Copy JSON have them all.`));
    return withRest(box, v, ctx, ["graph", "edges", "root", "rows", "type"]);
  }

  // ---------- comparisons ----------
  const COMPARE_COUNTS = ["unchanged", "changed", "unknown", "added", "removed", "modified", "matched", "passed", "failed"];
  function isCompare(v) {
    if (!isObj(v) || !isObj(v.summary)) return false;
    const nums = Object.values(v.summary).filter((n) => typeof n === "number");
    return nums.length >= 2 && COMPARE_COUNTS.some((k) => k in v.summary);
  }
  function compareView(v, ctx) {
    const counts = Object.entries(v.summary).filter(([, n]) => typeof n === "number");
    const lead = h("div", { class: "cmp" });
    const overallKey = ["status", "overall_status", "behavior_status", "verdict"].find((k) => typeof v[k] === "string");
    if (overallKey) {
      const word = humanKey(overallKey);
      lead.append(h("p", { class: "cmp-overall" }, `${word[0].toUpperCase()}${word.slice(1)}: `, statusChip(v[overallKey])));
    }
    lead.append(h("ul", { class: "stats", "aria-label": "Summary counts" }, counts.map(([k, n]) => {
      const word = k.replace(/^(records|unknowns)_/, "$1 ").replace(/_/g, " ");
      return h("li", { class: `stat ${statusKind(k.replace(/^(records|unknowns)_/, ""))}` }, h("b", null, fmtN(n)), h("span", null, word));
    })));
    const unknown = v.summary.unknown;
    if (typeof unknown === "number" && unknown > 0) {
      lead.append(h("p", { class: "rt-status" }, `${plural(unknown, "item")} unknown: REA could not tell whether ${unknown === 1 ? "it" : "they"} changed. Unknown is not counted as unchanged.`));
    }
    const skip = [overallKey].filter(Boolean);
    if (Object.values(v.summary).every((n) => typeof n === "number")) skip.push("summary");
    return withRest(lead, v, ctx, skip);
  }

  // ---------- line diffs ----------
  const DIFF_PAIRS = [["left", "right"], ["before", "after"], ["old", "new"], ["expected", "actual"], ["original", "modified"]];
  function diffPair(v) {
    if (!isObj(v)) return null;
    for (const [a, b] of DIFF_PAIRS) {
      if (typeof v[a] === "string" && typeof v[b] === "string" && (v[a].includes("\n") || v[b].includes("\n"))) return [a, b];
    }
    return null;
  }
  function diffLines(a, b) {
    let s = 0;
    while (s < a.length && s < b.length && a[s] === b[s]) s++;
    let e = 0;
    while (e < a.length - s && e < b.length - s && a[a.length - 1 - e] === b[b.length - 1 - e]) e++;
    const A = a.slice(s, a.length - e), B = b.slice(s, b.length - e);
    const ops = a.slice(0, s).map((t) => [" ", t]);
    if (A.length * B.length > DIFF_CELLS) {
      for (const t of A) ops.push(["-", t]);
      for (const t of B) ops.push(["+", t]);
    } else {
      const n = A.length, m = B.length;
      const L = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
      for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
      let i = 0, j = 0;
      while (i < n && j < m) {
        if (A[i] === B[j]) { ops.push([" ", A[i]]); i++; j++; }
        else if (L[i + 1][j] >= L[i][j + 1]) ops.push(["-", A[i++]]);
        else ops.push(["+", B[j++]]);
      }
      while (i < n) ops.push(["-", A[i++]]);
      while (j < m) ops.push(["+", B[j++]]);
    }
    for (const t of a.slice(a.length - e)) ops.push([" ", t]);
    return ops;
  }
  function diffView(left, right, names) {
    const ops = diffLines(left.split("\n"), right.split("\n"));
    let an = 0, bn = 0;
    const numbered = ops.map(([op, text]) => {
      if (op !== "+") an++;
      if (op !== "-") bn++;
      return { op, text, a: op === "+" ? "" : an, b: op === "-" ? "" : bn };
    });
    const added = ops.filter((o) => o[0] === "+").length, removed = ops.filter((o) => o[0] === "-").length;
    const lineEl = (l) => {
      const tag = l.op === "+" ? "ins" : l.op === "-" ? "del" : "span";
      return h(tag, { class: `diff-line${l.op === "+" ? " add" : l.op === "-" ? " del" : ""}` },
        h("span", { class: "diff-no", "aria-hidden": "true" }, String(l.a)), h("span", { class: "diff-no", "aria-hidden": "true" }, String(l.b)),
        h("span", { class: "diff-mark", "aria-hidden": "true" }, l.op === " " ? "" : l.op === "+" ? "+" : "−"),
        h("span", { class: "diff-text" }, l.text));
    };
    const pre = h("pre", { class: "code diff", tabindex: "0", role: "region",
      "aria-label": `Line diff of ${names[0]} and ${names[1]}: ${plural(added, "line")} added, ${plural(removed, "line")} removed` });
    // long unchanged runs fold to a button, keeping three lines of context either side
    let k = 0;
    while (k < numbered.length) {
      if (numbered[k].op !== " ") { pre.append(lineEl(numbered[k++])); continue; }
      let end = k;
      while (end < numbered.length && numbered[end].op === " ") end++;
      const run = numbered.slice(k, end);
      const keepHead = k === 0 ? 0 : 3, keepTail = end === numbered.length ? 0 : 3;
      if (run.length > keepHead + keepTail + 4) {
        run.slice(0, keepHead).forEach((l) => pre.append(lineEl(l)));
        const hidden = run.slice(keepHead, run.length - keepTail);
        const fold = h("button", { type: "button", class: "diff-fold" }, `Show ${plural(hidden.length, "unchanged line")}`);
        fold.addEventListener("click", () => { fold.replaceWith(...hidden.map(lineEl)); pre.focus(); });
        pre.append(fold);
        run.slice(run.length - keepTail).forEach((l) => pre.append(lineEl(l)));
      } else run.forEach((l) => pre.append(lineEl(l)));
      k = end;
    }
    const unified = () => [`--- ${names[0]}`, `+++ ${names[1]}`, ...ops.map(([op, t]) => `${op}${t}`)].join("\n");
    return h("div", { class: "code-block" },
      h("div", { class: "code-head" }, h("span", { class: "code-lang" }, `${names[0]} → ${names[1]}`),
        chip(`+${fmtN(added)}`, "ok", `${plural(added, "line")} added`), chip(`−${fmtN(removed)}`, "bad", `${plural(removed, "line")} removed`),
        h("span", { class: "grow" }), copyButton(unified, "Copy the diff", "Diff")),
      pre);
  }

  // ---------- paged summaries (inspect_analysis_view) ----------
  const INVENTORY_PARENTS = new Set(["list_procedures", "search_procedures", "list_strings", "search_strings", "list_names"]);
  function summaryView(v, ctx) {
    const lead = h("div", { class: "ev-stack" });
    lead.append(h("p", { class: "ev-note" }, `A summary. REA keeps the complete ${v.parent_operation || "result"} in this engine session, and pages of it can be read without running the analysis again.`));
    const dl = h("dl", { class: "ev-kv" });
    const out = h("div", { class: "ev-pages" });
    dl.append(h("dt", null, "Complete result"), h("dd", null, h("span", { class: "mono", title: v.parent_evidence_id }, shortId(v.parent_evidence_id)), " ",
      copyButton(v.parent_evidence_id, "Copy the complete result's evidence id", "Id")));
    const cov = isObj(v.coverage) ? v.coverage : null;
    if (cov) {
      const bits = [];
      if (typeof cov.examined === "number" && typeof cov.total === "number") bits.push(`${fmtN(cov.examined)} of ${fmtN(cov.total)} examined`);
      if (typeof cov.next_offset === "number") bits.push(`next page from ${fmtN(cov.next_offset)}`);
      dl.append(h("dt", null, "Coverage"), h("dd", null, cov.status ? statusChip(cov.status) : null, bits.length ? ` ${bits.join(" · ")}` : ""));
    }
    lead.append(dl);
    if (INVENTORY_PARENTS.has(v.parent_operation)) {
      const args = { source: { kind: "retained-evidence", evidence_id: v.parent_evidence_id }, view: { kind: "native-inventory", offset: 0, limit: 100 } };
      lead.append(h("div", { class: "ev-actions" },
        actionButton("Show the first 100 rows", "inspect_analysis_view", args, out, ctx.opts),
        h("span", { class: "field-hint" }, "Runs inspect_analysis_view on this subject; each page is also kept in Findings.")), out);
    }
    return withRest(lead, v, ctx, ["kind", "view", "parent_evidence_id", "parent_operation", "coverage"]);
  }

  // Tried in order before the generic view; the first match draws the value.
  const RENDERERS = [
    { match: (v) => typeof v === "string" && !!imageFromString(v), render: (v, ctx) => imageView(imageFromString(v), {}, ctx) },
    { match: (v) => isObj(v) && !!imageInObject(v), render: (v, ctx) => { const img = imageInObject(v); return withRest(imageView(img, v, ctx), v, ctx, [img.key]); } },
    { match: (v) => isObj(v) && hexKeys(v).length > 0, render: (v, ctx) => {
      const keys = hexKeys(v);
      return withRest(h("div", { class: "ev-stack" }, keys.map((k) => hexView(v, k, ctx))), v, ctx, keys);
    } },
    { match: isGraph, render: graphView },
    { match: isCompare, render: compareView },
    { match: (v) => isObj(v) && v.kind === "summary" && typeof v.parent_evidence_id === "string", render: summaryView },
    { match: (v) => !!diffPair(v), render: (v, ctx) => { const pair = diffPair(v); return withRest(diffView(v[pair[0]], v[pair[1]], pair), v, ctx, pair); } },
  ];

  // ---------- errors ----------
  function fmtMs(ms) {
    if (typeof ms !== "number") return String(ms);
    return ms >= 1000 ? `${(ms / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 })} s` : `${fmtN(ms)} ms`;
  }
  function openToolButton(name) {
    return h("button", { type: "button", class: "btn small", onclick: () => {
      if (RE.tools && RE.tools.openTool) RE.tools.openTool(name);
      else RE.go(`#~tools/${encodeURIComponent(name)}`);
    } }, icon("re-tools"), `Open ${name} in Tools`);
  }
  function errorView(error, opts = {}) {
    const e = isObj(error) ? error : { code: "error", category: "unknown", message: String(error ?? "") };
    const cancelled = e.category === "cancelled" || e.code === "cancelled";
    const message = e.message || "REA reported an error without a message.";
    const box = h("div", { class: `ev-error${cancelled ? " soft" : ""}` });
    box.append(h("div", { class: "ev-error-head" }, icon("re-alert", 16),
      message.includes("\n") ? h("pre", { class: "ev-error-msg" }, message) : h("strong", { class: "ev-error-msg" }, message)));
    const chips = h("div", { class: "ev-chips" });
    if (e.code && !(e.code === "error" && (!e.category || e.category === "unknown"))) chips.append(h("span", { class: "chip mono", title: "Error code" }, e.code));
    if (e.category && e.category !== "unknown" && e.category !== e.code) chips.append(chip(humanKey(e.category), "", "Error category"));
    if (e.retryable === true) chips.append(chip("Safe to retry", "ok", "REA says the same call may succeed if tried again."));
    else if (e.retryable === false) chips.append(chip("Don’t repeat it unchanged", "warn", "REA says the same call will fail the same way."));
    if (chips.childNodes.length) box.append(chips);
    const action = isObj(e.remediation) ? e.remediation.action : typeof e.remediation === "string" ? e.remediation : null;
    if (action) box.append(h("p", { class: "ev-error-fix" }, h("b", null, "What to do: "), action));
    const rec = e.recovery;
    if (isObj(rec) && rec.kind === "inspect_input_contract") {
      box.append(h("p", null, `REA rejected the inputs of ${rec.operation}. Check them against that tool’s fields and correct them; the same call fails the same way.`),
        h("div", { class: "ev-actions" }, openToolButton(rec.operation)));
    } else if (isObj(rec) && rec.kind === "inspect_timeout_state") {
      box.append(h("p", null, `REA’s own deadline for ${rec.operation} was ${fmtMs(rec.timeout_ms)}. A longer wait in the studio does not extend it. Look at any partial result below, then follow the advice above.`));
    } else if (rec !== undefined && rec !== null) {
      box.append(h("div", null, label("Recovery"), renderValue(rec, { opts, key: "recovery" })));
    }
    const d = isObj(e.details) ? e.details : null;
    if (d) {
      const lims = isObj(d.reported_limits) ? d.reported_limits : null;
      const ref = lims ? lims.evidence_reference : null;
      const refId = typeof ref === "string" ? ref : isObj(ref) ? ref.evidence_id : null;
      if (refId) {
        box.append(h("p", null, "The complete result is kept in this engine session as ", h("span", { class: "mono", title: refId }, shortId(refId)), ". ",
          "Read a page of it with inspect_analysis_view, or export the session’s evidence bundle. ", copyButton(refId, "Copy the kept result's evidence id", "Id")));
      }
      const cont = lims && isObj(lims.continuation) ? lims.continuation : null;
      if (cont && cont.name && !opts.skipContinuation) {
        const out = h("div", { class: "ev-pages" });
        box.append(h("div", { class: "ev-actions" }, actionButton("Open the first page", cont.name, cont.arguments || {}, out, opts)), out);
      }
      if ("partial_observation" in d) {
        box.append(disclosure("Partial result REA kept", () => renderValue(d.partial_observation, { opts, key: "partial_observation" }), true));
      }
      const rest = Object.fromEntries(Object.entries(d).filter(([k]) => k !== "partial_observation"));
      if (Object.keys(rest).length) box.append(disclosure("Details from REA", () => renderValue(rest, { opts, key: "details" })));
    }
    return box;
  }

  // A <details> whose content is built the first time it opens.
  function disclosure(title, build, open = false) {
    const det = h("details", { class: "re-disc ev-disc" }, h("summary", null, icon("re-caret", 14, "caret"), h("span", null, title)));
    let built = false;
    const fill = () => { if (!built) { built = true; det.append(h("div", { class: "ev-disc-body" }, build())); } };
    det.addEventListener("toggle", () => { if (det.open) fill(); });
    if (open) { fill(); det.open = true; }
    return det;
  }

  // ---------- follow-up runs (continuations, pages, re-runs) ----------
  function actionButton(text, tool, args, out, opts, heading) {
    const btn = h("button", { type: "button", class: "btn small soft", title: `Runs ${tool} on this subject` }, icon("re-next"), text);
    btn.addEventListener("click", () => runAction(btn, tool, args, out, opts, heading));
    return btn;
  }
  async function runAction(button, tool, args, out, opts, heading) {
    if (button.getAttribute("aria-disabled") === "true") return;
    const slug = slugOf(opts);
    if (!slug) { RE.toast("Open a subject first, then run this.", { kind: "bad" }); return; }
    const kids = [...button.childNodes];
    button.setAttribute("aria-disabled", "true");
    button.classList.add("busy");
    button.replaceChildren(h("span", { class: "re-spin", "aria-hidden": "true" }), "Running…");
    RE.announce(`Running ${tool}.`, "polite");
    let reply;
    try { reply = await RE.runTool(slug, tool, args, { source: "panel" }); }
    catch (err) { reply = { error: (err && err.message) || "The run did not finish." }; }
    button.replaceChildren(...kids);
    button.removeAttribute("aria-disabled");
    button.classList.remove("busy");
    if (!reply || reply.declined || reply.cancelled) return;
    if (reply.error) {
      out.append(h("div", { class: "ev-run-error" }, icon("re-alert"), h("span", null, reply.error)));
      RE.toast(reply.error, { kind: "bad" });
      return;
    }
    const entry = reply.entry || fromResult(tool, args, reply.result);
    out.append(h("div", { class: "ev-page" }, label(heading || titleOf(entry)), body(entry, opts)));
    RE.announce(`${titleOf(entry)} ${entry.error ? "failed" : "finished"}.`, "polite");
    if (RE.refresh) RE.refresh();
  }
  // An inspect_analysis_view page whose coverage names a next offset, when the server sent no continuation.
  function derivedNext(entry) {
    const args = entry.arguments;
    const nr = isObj(entry.evidence) ? entry.evidence.normalized_result : null;
    const next = isObj(nr) && isObj(nr.coverage) ? nr.coverage.next_offset : null;
    if (entry.tool !== "inspect_analysis_view" || typeof next !== "number" || !isObj(args) || !isObj(args.view)) return null;
    return { tool: entry.tool, arguments: { ...args, view: { ...args.view, offset: next } }, label: `Next page (from ${fmtN(next)})` };
  }
  function followUps(entry, opts) {
    const c = isObj(entry.continuation) ? entry.continuation : derivedNext(entry);
    if (!c || !c.tool) return null;
    const out = h("div", { class: "ev-pages" });
    return h("div", { class: "ev-follow" },
      h("div", { class: "ev-actions" }, actionButton(c.label || "Load more", c.tool, c.arguments || {}, out, opts),
        h("span", { class: "field-hint" }, `REA left more to read. This runs ${c.tool}; the page shows here and in Findings.`)),
      out);
  }

  // ---------- evidence links and locations ----------
  function findCard(evidenceId) {
    try { return document.querySelector(`.ev[data-evidence-id="${CSS.escape(evidenceId)}"]`); } catch { return null; }
  }
  function reveal(target) {
    const card = typeof target === "string" ? (document.getElementById(`ev-${target}`) || findCard(target)) : target;
    if (!card) return false;
    card.open = true;
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    card.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
    const sum = card.querySelector(":scope > summary");
    if (sum) sum.focus({ preventScroll: true });
    card.classList.remove("ev-flash");
    void card.offsetWidth; // restart the highlight
    card.classList.add("ev-flash");
    setTimeout(() => card.classList.remove("ev-flash"), 1800);
    return true;
  }
  function linkButtons(ids, opts, ownId) {
    const out = h("div", { class: "ev-pages" });
    const list = h("div", { class: "ev-chips" });
    const draw = (n) => list.replaceChildren(...ids.slice(0, n).map((id) => {
      const btn = h("button", { type: "button", class: "chip-btn mono", "aria-label": `Show evidence ${shortId(id)}`,
        title: `${id}\nShows its card in Findings, or asks REA for a summary of it` }, icon("re-link", 12), shortId(id));
      btn.addEventListener("click", () => {
        const card = findCard(id);
        if (card && !card.contains(btn)) reveal(card);
        else runAction(btn, "inspect_analysis_view", { source: { kind: "retained-evidence", evidence_id: id }, view: { kind: "summary" } }, out, opts, `Summary of ${shortId(id)}`);
      });
      return btn;
    }));
    const usable = ids.filter((id) => typeof id === "string" && id !== ownId);
    ids = usable;
    draw(24);
    const more = ids.length > 24 ? h("button", { type: "button", class: "btn ghost small", onclick: () => { draw(ids.length); more.remove(); list.firstChild && list.firstChild.focus(); } }, `Show all ${ids.length}`) : null;
    return h("div", null, list, more, out);
  }
  function locationText(loc) {
    if (!isObj(loc)) return String(loc);
    switch (loc.kind) {
      case "address": return String(loc.address);
      case "address-range": return `${loc.start}–${loc.end}`;
      case "file-offset": return `offset ${loc.offset}`;
      case "file-offset-range": return `offset ${loc.start}–${loc.end}`;
      case "artifact-path": return String(loc.path);
      default: return compactJson(loc);
    }
  }
  function locationChips(locs) {
    const list = h("div", { class: "ev-chips" });
    const texts = locs.map(locationText);
    const draw = (n) => list.replaceChildren(...texts.slice(0, n).map((t) => h("button", { type: "button", class: "chip-btn mono", "aria-label": `Copy ${t}`,
      title: "Copy", onclick: (e) => RE.copy(t, e.currentTarget) }, t)));
    draw(40);
    if (texts.length <= 40) return list;
    const more = h("button", { type: "button", class: "btn ghost small", onclick: () => { draw(texts.length); more.remove(); list.firstChild.focus(); } }, `Show all ${fmtN(texts.length)}`);
    return h("div", null, list, more);
  }

  // ---------- an entry ----------
  const isEnvelope = (ev) => isObj(ev) && "normalized_result" in ev && ("evidence_id" in ev || "operation" in ev);
  const envelopeOf = (entry) => (isEnvelope(entry && entry.evidence) ? entry.evidence : null);
  function titleOf(entry) {
    const ev = envelopeOf(entry);
    return entry.title || (ev && (ev.operation || ev.predicate_type)) || entry.tool || "Result";
  }
  const ARG_KEYS = ["address", "procedure", "name", "function", "query", "pattern", "value", "url", "path", "input_path", "target_path",
    "session_id", "unknown_id", "feature", "offset", "detail", "limit"];
  function keyArgs(entry) {
    const args = isObj(entry.arguments) ? entry.arguments : {};
    const bits = [];
    for (const k of ARG_KEYS) {
      const v = args[k];
      if (!isScalar(v) || v === null || v === "") continue;
      let text = String(v);
      if (/path$/.test(k)) text = text.split(/[\\/]/).pop();
      bits.push(`${humanKey(k)} ${text.length > 40 ? `${text.slice(0, 39)}…` : text}`);
      if (bits.length === 2) break;
    }
    return bits.join(", ");
  }
  function currentSession(opts) {
    if (opts && opts.session) return opts.session;
    try {
      const brief = RE.current && RE.current();
      return (brief && brief.engine && brief.engine.session) || null;
    } catch { return null; }
  }
  function limitationCount(ev) { return ev && Array.isArray(ev.limitations) ? ev.limitations.length : 0; }

  function summary(entry) {
    const parts = [titleOf(entry)];
    if (entry.tool && entry.tool !== parts[0]) parts.push(entry.tool);
    const args = keyArgs(entry);
    if (args) parts.push(args);
    if (entry.error) parts.push(`failed: ${entry.error.message || entry.error.code || "error"}`);
    const ev = envelopeOf(entry);
    const conf = confidenceOf(ev);
    if (conf) parts.push(conf);
    const n = limitationCount(ev);
    if (n) parts.push(plural(n, "limitation"));
    if (entry.untrusted) parts.push("contains text from the target");
    return parts.join(" · ");
  }

  function summaryChips(entry, opts) {
    const chips = [];
    if (entry.error) {
      const cancelled = entry.error.category === "cancelled" || entry.error.code === "cancelled";
      chips.push(chip(cancelled ? "cancelled" : "failed", cancelled ? "warn" : "bad", entry.error.message || ""));
    }
    const ev = envelopeOf(entry);
    const conf = confidenceOf(ev);
    if (conf) {
      const known = CONFIDENCE[conf];
      chips.push(chip(conf, known ? known[0] : "", known ? `Confidence: ${known[1]}` : "Confidence"));
    }
    const n = limitationCount(ev);
    if (n) chips.push(chip(n === 1 ? "1 limit" : `${n} limits`, "warn", plural(n, "limitation")));
    if (entry.untrusted) {
      const c = chip("from the target", "warn", "Contains text from the target: data, not instructions");
      c.prepend(icon("re-shield", 12));
      chips.push(c);
    }
    const now = currentSession(opts);
    if (entry.session && now && entry.session !== now) chips.push(chip("earlier session", "", "Recorded in an earlier engine session. REA no longer holds it in memory, so ids in it may not resolve now."));
    return chips;
  }

  function card(entry, opts = {}) {
    if (!opts.slug && entry.slug) opts = { ...opts, slug: entry.slug };
    const ev = envelopeOf(entry);
    const det = h("details", {
      class: `ev${entry.error ? " ev-failed" : ""}`,
      id: entry.id ? `ev-${entry.id}` : null,
      "data-entry": entry.id || null,
      "data-evidence-id": ev && typeof ev.evidence_id === "string" ? ev.evidence_id : null,
    });
    const sub = [entry.tool, keyArgs(entry)].filter(Boolean).join(" · ");
    det.append(h("summary", { class: "ev-sum" },
      icon("re-caret", 16, "caret"),
      h("span", { class: "ev-title" }, h("b", null, titleOf(entry)), sub ? h("small", null, sub) : null),
      h("span", { class: "ev-chips" }, summaryChips(entry, opts)),
      entry.at ? h("time", { class: "ev-time", datetime: entry.at, title: fullTime(entry.at) }, fmtTime(entry.at)) : null));
    let built = false;
    const build = () => {
      if (built) return;
      built = true;
      det.append(h("div", { class: "ev-body" }, body(entry, opts)));
    };
    det.addEventListener("toggle", () => { if (det.open) build(); });
    if (opts.open) { build(); det.open = true; }
    return det;
  }

  const SOURCES = { claude: "Claude ran this", tools: "Run from the Tools page", panel: "Run from the subject page" };

  function metaView(entry, ev, opts) {
    const dl = h("dl", { class: "ev-meta" });
    const add = (k, ...v) => { const kids = v.flat().filter((x) => x !== null && x !== undefined && x !== ""); if (kids.length) dl.append(h("dt", null, k), h("dd", null, kids)); };
    add("Tool", h("span", { class: "mono" }, entry.tool || (ev && ev.operation) || "unknown"));
    if (ev) {
      const p = ev.provider;
      if (p) add("Provider", isObj(p) ? [p.name || p.id || compactJson(p), p.version ? ` ${p.version}` : ""] : String(p));
      if (ev.predicate_type) add("Predicate", h("span", { class: "mono" }, ev.predicate_type));
      const conf = confidenceOf(ev);
      if (conf) add("Confidence", chip(conf, CONFIDENCE[conf] ? CONFIDENCE[conf][0] : ""), CONFIDENCE[conf] ? ` ${CONFIDENCE[conf][1]}` : "");
      if (ev.authority) add("Authority", AUTHORITY[ev.authority] ? `From ${AUTHORITY[ev.authority]} ` : "", h("span", { class: "mono ev-null" }, `(${ev.authority})`));
      const s = ev.subject;
      if (isObj(s)) {
        const sha = isObj(s.digest) ? s.digest.sha256 : null;
        add("Subject", [s.name, s.format, s.architecture].filter(Boolean).join(" · "),
          sha ? [" · ", h("span", { class: "mono", title: sha }, `sha256 ${shortHash(sha)}`), " ", copyButton(sha, "Copy the subject's sha256", "sha256")] : null);
      } else if (s === null) add("Subject", h("span", { class: "ev-null" }, "not bound to a target"));
      const env = ev.environment;
      if (isObj(env)) add("Environment", [env.platform, env.architecture, env.isolation && `isolation: ${env.isolation}`].filter(Boolean).join(" · "));
      const cov = ev.relationship_coverage;
      if (isObj(cov)) add("Coverage", statusChip(cov.status || "unknown"), cov.reason ? ` ${cov.reason}` : "",
        cov.limited_directions ? ` · limited: ${[].concat(cov.limited_directions).map((d) => (isObj(d) ? compactJson(d) : d)).join(", ")}` : "");
      if (Array.isArray(ev.locations) && ev.locations.length) add("Locations", locationChips(ev.locations));
      if (Array.isArray(ev.evidence_links) && ev.evidence_links.filter((id) => id !== ev.evidence_id).length) {
        add("Links", linkButtons(ev.evidence_links, opts, ev.evidence_id));
      }
      if (ev.evidence_id) add("Evidence id", h("span", { class: "mono", title: ev.evidence_id }, shortId(ev.evidence_id)), " ", copyButton(ev.evidence_id, "Copy the evidence id", "Copy"));
    }
    const now = currentSession(opts);
    if (entry.session && now && entry.session !== now) add("Session", "An earlier engine session. REA no longer holds this record in memory; reopen or import its bundle to use its ids.");
    if (entry.at || entry.source) {
      add("Recorded", entry.at ? h("time", { datetime: entry.at }, fullTime(entry.at)) : null, entry.source ? ` · ${SOURCES[entry.source] || entry.source}` : "");
    }
    return dl;
  }

  function limitationsView(lims) {
    const list = Array.isArray(lims) ? lims : lims === undefined || lims === null ? [] : [lims];
    const lines = list.map((x) => (typeof x === "string" ? x : compactJson(x))).filter(Boolean);
    if (!lines.length) return h("p", { class: "ev-lims none" }, "REA listed no limitations for this result.");
    return h("div", { class: "ev-lims" }, h("b", null, `Limitations (${lines.length})`), bulletList(lines));
  }

  function argsView(entry, opts) {
    const args = isObj(entry.arguments) ? entry.arguments : {};
    const keys = Object.keys(args);
    const slug = slugOf(opts);
    const out = h("div", { class: "ev-pages" });
    const buttons = [];
    if (entry.tool && slug) {
      // REA's "retryable: false" means the unchanged call fails the same way, so offer only the form
      if (!(entry.error && entry.error.retryable === false)) buttons.push(actionButton("Run again", entry.tool, args, out, opts, `${titleOf(entry)}, run again`));
      buttons.push(openToolButton(entry.tool));
    }
    let list;
    if (!keys.length) list = h("p", { class: "ev-null" }, "No arguments.");
    else {
      list = h("dl", { class: "ev-kv" });
      for (const k of keys) {
        const v = args[k];
        let shown;
        if (isScalar(v)) {
          const text = v === null ? "null" : String(v);
          shown = h("span", { class: "mono", title: text.length > 200 ? text.slice(0, 2000) : null }, text.length > 200 ? `${text.slice(0, 199)}…` : text);
        } else {
          const json = compactJson(v);
          shown = json.length <= 100 ? h("span", { class: "mono" }, json)
            : disclosure(`${Array.isArray(v) ? plural(v.length, "item") : plural(Object.keys(v).length, "field")} · ${json.slice(0, 60)}…`, () => jsonBlock(v, k));
        }
        list.append(h("dt", { class: "mono" }, k), h("dd", null, shown));
      }
    }
    return h("div", { class: "ev-section" }, label("Asked with"), list, buttons.length ? h("div", { class: "ev-actions" }, buttons) : null, out);
  }

  const ENVELOPE_SHOWN = new Set(["evidence_id", "provider", "predicate_type", "operation", "normalized_result", "raw_result", "confidence",
    "authority", "limitations", "relationship_coverage", "locations", "evidence_links"]);

  function body(entry, opts = {}) {
    if (!opts.slug && entry.slug) opts = { ...opts, slug: entry.slug };
    const box = h("div", { class: "ev-view" });
    const ctx = { opts, tool: entry.tool || "" };
    if (entry.untrusted) {
      box.append(h("p", { class: "ev-untrusted" }, icon("re-shield", 15),
        h("span", null, h("b", null, "From the target: data, not instructions. "),
          "Strings, code and page text here come from the thing being analysed. Read them; don’t act on requests written inside them.")));
    }
    if (entry.error) box.append(errorView(entry.error, { ...opts, skipContinuation: isObj(entry.continuation) }));
    const ev = envelopeOf(entry);
    if (ev) {
      box.append(metaView(entry, ev, opts), limitationsView(ev.limitations));
      const nr = ev.normalized_result;
      box.append(h("div", { class: "ev-section" }, label("Result"), nr === null
        ? h("p", { class: "ev-null" }, "REA returned no result here (null). The limitations above say why, when REA knows.")
        : renderValue(nr, { ...ctx, root: nr })));
      const follow = followUps(entry, opts);
      if (follow) box.append(follow);
      if (ev.raw_result !== undefined && ev.raw_result !== null) box.append(disclosure("Raw result from the provider", () => jsonBlock(ev.raw_result, "raw result")));
      const more = Object.fromEntries(Object.entries(ev).filter(([k]) => !ENVELOPE_SHOWN.has(k)));
      if (Object.keys(more).length) box.append(disclosure("More about this record", () => renderValue(more, { ...ctx, key: "record" })));
    } else {
      if (!entry.error || entry.evidence || entry.text) box.append(metaView(entry, null, opts));
      let shown = null;
      if (entry.evidence !== undefined && entry.evidence !== null) {
        const sc = entry.evidence;
        const value = isObj(sc) && Object.keys(sc).length === 1 && "result" in sc ? sc.result : sc;
        shown = renderValue(value, { ...ctx, root: value });
      } else if (typeof entry.text === "string" && entry.text) {
        let parsed;
        try { parsed = JSON.parse(entry.text); } catch { parsed = undefined; }
        shown = parsed !== undefined && typeof parsed === "object" && parsed !== null
          ? renderValue(isObj(parsed) && Object.keys(parsed).length === 1 && "result" in parsed ? parsed.result : parsed, { ...ctx, root: parsed })
          : codeBlock(entry.text, "text");
      } else if (!entry.error) {
        shown = h("p", { class: "ev-null" }, "REA returned no content.");
      }
      if (shown) box.append(h("div", { class: "ev-section" }, label("Result"), shown));
      const follow = followUps(entry, opts);
      if (follow) box.append(follow);
    }
    box.append(argsView(entry, opts));
    box.append(h("div", { class: "ev-foot" },
      copyButton(() => JSON.stringify(entry, null, 2), "Copy JSON of this finding", "Copy JSON"),
      entry.id ? h("span", { class: "mono ev-null" }, entry.id) : null));
    return box;
  }

  // An entry-shaped object from a raw MCP result, for callers that have no stored entry.
  function fromResult(tool, args, result) {
    const r = isObj(result) ? result : {};
    const sc = r.structuredContent;
    const texts = (Array.isArray(r.content) ? r.content : []).filter((c) => c && c.type === "text" && typeof c.text === "string").map((c) => c.text);
    const meta = isObj(r._meta) ? r._meta : isObj(sc) && isObj(sc._meta) ? sc._meta : {};
    const untrusted = !!(isObj(meta.provenance) && meta.provenance.untrusted_content === true);
    let error = null;
    if (r.isError) {
      const text = texts.join("\n");
      try {
        const parsed = JSON.parse(texts[0] || "");
        error = isObj(parsed) && isObj(parsed.error) ? parsed.error : null;
      } catch { error = null; }
      if (!error) error = { code: "error", category: "unknown", message: text || "REA reported an error." };
    }
    return {
      id: null, tool, title: null, arguments: isObj(args) ? args : {}, at: new Date().toISOString(), source: "panel", untrusted, error,
      evidence: !error && sc !== undefined && sc !== null ? sc : null,
      text: !error && (sc === undefined || sc === null) && texts.length ? texts.join("\n") : null,
      continuation: null,
    };
  }

  RE.evidence = { card, body, renderValue, errorView, summary, fromResult, reveal, table: tableView, copyButton, chip, statusChip };
})();
