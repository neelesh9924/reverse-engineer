// RE.markdown(text) -> DocumentFragment. Claude's replies as Markdown, built from DOM nodes only
// (never innerHTML), so nothing in a reply can become markup or script. Supports paragraphs,
// headings, bold/italic/strikethrough, inline and fenced code (with a copy button), nested lists,
// task items, block quotes, pipe tables, links (http, https and mailto only) and rules.
// Headings start at h3: the page owns its one h1 and the panels use h2.
(() => {
  "use strict";
  const RE = window.RE;
  const h = (...args) => RE.h(...args);

  // Icons markdown.js and evidence.js draw, under their own names so the shell's set can change freely.
  RE.addIcons({
    "re-copy": ["M9 9h11v11H9z", "M5 15H4V4h11v1"],
    "re-caret": ["M9 6l6 6-6 6"],
    "re-alert": ["M12 4l9 16H3z", "M12 10v4", "M12 17.2v.3"],
    "re-play": ["M8 5l11 7-11 7z"],
    "re-tools": ["M14 7a3 3 0 0 1-4 4L6 15l3 3 4-4a3 3 0 0 0 4-4z", "M14.5 6.5l3-3 2 2-3 3"],
    "re-download": ["M12 4v11", "M7 10l5 5 5-5", "M5 20h14"],
    "re-link": ["M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1", "M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"],
    "re-shield": ["M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z", "M12 8v5", "M12 16v.5"],
    "re-next": ["M5 12h13", "M13 6l6 6-6 6"],
  });

  const FENCE = /^( {0,3})(`{3,}|~{3,})[ \t]*([^\s`]*)[^`]*$/;
  const HEADING = /^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$/;
  const RULE = /^ {0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$/;
  const QUOTE = /^ {0,3}>[ \t]?(.*)$/;
  const ITEM = /^([ \t]*)([-*+]|\d{1,9}[.)])([ \t]+|$)(.*)$/;
  const TABLE_SEP = /^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$/;
  const SAFE_PROTOCOLS = ["http:", "https:", "mailto:"];
  const PUNCT = /[!-/:-@[-`{-~]/;
  const EMPHASIS_REACH = 4000; // how far an opening * or _ looks for its closer

  // Leading whitespace in columns (a tab counts as 4).
  function cols(text) {
    let n = 0;
    for (const ch of text) { if (ch === " ") n++; else if (ch === "\t") n += 4; else break; }
    return n;
  }
  // Remove up to `n` columns of leading whitespace.
  function dedent(line, n) {
    let i = 0, c = 0;
    while (i < line.length && c < n && (line[i] === " " || line[i] === "\t")) { c += line[i] === "\t" ? 4 : 1; i++; }
    return line.slice(i);
  }
  const blank = (line) => !line.trim();

  function safeHref(raw) {
    try {
      const url = new URL(raw);
      return SAFE_PROTOCOLS.includes(url.protocol) ? url.href : null;
    } catch { return null; }
  }

  // ---------- blocks ----------
  function startsBlock(line) {
    return FENCE.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line) || ITEM.test(line);
  }

  function blocks(lines) {
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (blank(line)) { i++; continue; }
      let m;
      if ((m = FENCE.exec(line))) { i = fence(lines, i, m, out); continue; }
      if ((m = HEADING.exec(line))) { out.push(heading(m)); i++; continue; }
      if (RULE.test(line)) { out.push(h("hr", { class: "md-hr" })); i++; continue; }
      if (QUOTE.test(line)) { i = quote(lines, i, out); continue; }
      if (ITEM.test(line)) { i = list(lines, i, out); continue; }
      if (tableStart(lines, i)) { i = table(lines, i, out); continue; }
      i = paragraph(lines, i, out);
    }
    return out;
  }

  function heading(m) {
    const level = Math.min(6, m[1].length + 2);
    return h(`h${level}`, { class: `md-h md-h${m[1].length}` }, inline(m[2] || ""));
  }

  function fence(lines, start, m, out) {
    const indent = m[1].length, mark = m[2], lang = m[3] || "";
    const close = new RegExp(`^ {0,3}${mark[0] === "`" ? "`" : "~"}{${mark.length},}[ \\t]*$`);
    const body = [];
    let i = start + 1;
    while (i < lines.length && !close.test(lines[i])) { body.push(dedent(lines[i], indent)); i++; }
    out.push(codeBlock(body.join("\n"), lang));
    return i < lines.length ? i + 1 : i;
  }

  function codeBlock(text, lang) {
    const label = lang ? `${lang} code` : "Code";
    const copy = h("button", { type: "button", class: "btn ghost small copy-btn", "aria-label": `Copy ${label.toLowerCase()}`,
      title: "Copy to the clipboard", onclick: (e) => RE.copy(text, e.currentTarget) }, RE.icon("re-copy", 14), "Copy");
    return h("div", { class: "code-block md-code" },
      h("div", { class: "code-head" }, h("span", { class: "code-lang" }, lang || "code"), copy),
      h("pre", { class: "code", tabindex: "0", role: "region", "aria-label": label }, h("code", null, text)));
  }

  function quote(lines, start, out) {
    const body = [];
    let i = start;
    while (i < lines.length) {
      const m = QUOTE.exec(lines[i]);
      if (m) { body.push(m[1]); i++; continue; }
      // a lazy continuation line belongs to the quote's last paragraph
      if (!blank(lines[i]) && body.length && !blank(body[body.length - 1]) && !startsBlock(lines[i])) { body.push(lines[i]); i++; continue; }
      break;
    }
    out.push(h("blockquote", { class: "md-quote" }, blocks(body)));
    return i;
  }

  function list(lines, start, out) {
    const first = ITEM.exec(lines[start]);
    const base = cols(first[1]);
    const ordered = /\d/.test(first[2]);
    const el = h(ordered ? "ol" : "ul", { class: "md-list" });
    if (ordered) {
      const n = parseInt(first[2], 10);
      if (n !== 1) el.setAttribute("start", String(n));
    }
    // a different bullet or number delimiter (". " vs ") ") starts a new list, as in CommonMark
    const marker = ordered ? first[2].slice(-1) : first[2];
    const sameKind = (m) => m && cols(m[1]) <= base + 1 && /\d/.test(m[2]) === ordered && (ordered ? m[2].slice(-1) : m[2]) === marker;
    const items = [];
    let tight = true;
    let i = start;
    while (i < lines.length) {
      const m = ITEM.exec(lines[i]);
      if (!sameKind(m)) break;
      const contentIndent = m[3].length > 4 || !m[4] ? cols(m[1]) + m[2].length + 1 : cols(m[1]) + m[2].length + m[3].length;
      const body = [m[4]];
      i++;
      let sawBlank = false;
      while (i < lines.length) {
        const line = lines[i];
        if (blank(line)) { body.push(""); sawBlank = true; i++; continue; }
        const indent = cols(line);
        const sub = ITEM.exec(line);
        if (indent >= contentIndent) { body.push(dedent(line, contentIndent)); sawBlank = false; i++; continue; }
        if (sub && indent > base + 1) { body.push(dedent(line, indent)); sawBlank = false; i++; continue; }
        if (sub || sawBlank || startsBlock(line)) break;
        body.push(line.trim()); // lazy paragraph continuation
        i++;
      }
      // trailing blank lines end the item; a blank line before the next item makes the list loose
      while (body.length && blank(body[body.length - 1])) body.pop();
      if (sawBlank && i < lines.length && sameKind(ITEM.exec(lines[i]))) tight = false;
      if (body.some((l, k) => blank(l) && k > 0 && k < body.length - 1)) tight = false;
      items.push(body);
    }
    for (const body of items) el.append(listItem(body, tight));
    out.push(el);
    return i;
  }

  function listItem(body, tight) {
    let task = null;
    const t = /^\[([ xX])\][ \t]+/.exec(body[0] || "");
    if (t) {
      body = [body[0].slice(t[0].length), ...body.slice(1)];
      const done = t[1] !== " ";
      task = h("input", { type: "checkbox", class: "md-check", disabled: true, checked: done,
        "aria-label": done ? "Done" : "Not done" });
    }
    const kids = blocks(body);
    const li = h("li", { class: task ? "md-task" : null }, task);
    for (const kid of kids) {
      if (tight && kid.nodeName === "P") li.append(...kid.childNodes);
      else li.append(kid);
    }
    return li;
  }

  function splitRow(line) {
    let text = line.trim();
    if (text.startsWith("|")) text = text.slice(1);
    if (text.endsWith("|") && !text.endsWith("\\|")) text = text.slice(0, -1);
    const cells = [];
    let cell = "", inCode = false;
    for (let k = 0; k < text.length; k++) {
      const ch = text[k];
      if (ch === "\\" && text[k + 1] === "|") { cell += "|"; k++; continue; }
      if (ch === "`") inCode = !inCode;
      if (ch === "|" && !inCode) { cells.push(cell.trim()); cell = ""; continue; }
      cell += ch;
    }
    cells.push(cell.trim());
    return cells;
  }

  function tableStart(lines, i) {
    if (i + 1 >= lines.length || !lines[i].includes("|")) return false;
    const sep = lines[i + 1];
    if (!sep.includes("|") || !sep.includes("-") || !TABLE_SEP.test(sep)) return false;
    return splitRow(lines[i]).length === splitRow(sep).length;
  }

  function table(lines, start, out) {
    const head = splitRow(lines[start]);
    const align = splitRow(lines[start + 1]).map((c) => {
      const left = c.startsWith(":"), right = c.endsWith(":");
      return left && right ? "center" : right ? "right" : left ? "left" : "";
    });
    const rows = [];
    let i = start + 2;
    while (i < lines.length && !blank(lines[i]) && lines[i].includes("|")) { rows.push(splitRow(lines[i])); i++; }
    const cell = (tag, text, k) => h(tag, { class: align[k] ? `md-al-${align[k]}` : null, scope: tag === "th" ? "col" : null }, inline(text || ""));
    out.push(h("div", { class: "table-wrap md-table-wrap", role: "region", "aria-label": "Table", tabindex: "0" },
      h("table", { class: "md-table" },
        h("thead", null, h("tr", null, head.map((c, k) => cell("th", c, k)))),
        h("tbody", null, rows.map((r) => h("tr", null, head.map((_, k) => cell("td", r[k], k))))))));
    return i;
  }

  function paragraph(lines, start, out) {
    const body = [lines[start].trim()];
    let i = start + 1;
    while (i < lines.length && !blank(lines[i]) && !startsBlock(lines[i]) && !tableStart(lines, i)) {
      body.push(lines[i].trim());
      i++;
    }
    // a trailing backslash is an explicit line break; every newline is one anyway
    out.push(h("p", { class: "md-p" }, inline(body.map((l) => l.replace(/\\$/, "")).join("\n"))));
    return i;
  }

  // ---------- inline ----------
  const isWord = (ch) => !!ch && /[\p{L}\p{N}]/u.test(ch);
  const isSpace = (ch) => !ch || /\s/.test(ch);

  function runLength(text, at, ch) {
    let n = 0;
    while (text[at + n] === ch) n++;
    return n;
  }

  // Index past a code span starting at `at`, or -1 when the backticks are unmatched.
  function codeSpanEnd(text, at) {
    const n = runLength(text, at, "`");
    let k = at + n;
    while (k < text.length) {
      const next = text.indexOf("`", k);
      if (next < 0) return -1;
      const m = runLength(text, next, "`");
      if (m === n) return next + m;
      k = next + m;
    }
    return -1;
  }

  // The closing delimiter run for an emphasis opener, or -1.
  function findCloser(text, from, ch, n) {
    const stop = Math.min(text.length, from + EMPHASIS_REACH);
    let k = from;
    while (k < stop) {
      const c = text[k];
      if (c === "\\") { k += 2; continue; }
      if (c === "`") { const end = codeSpanEnd(text, k); if (end > 0) { k = end; continue; } }
      if (c === ch) {
        const m = runLength(text, k, ch);
        const before = text[k - 1], after = text[k + m];
        const closes = !isSpace(before) && (ch !== "_" || !isWord(after));
        if (m === n && closes && k > from) return k;
        k += m;
        continue;
      }
      k++;
    }
    return -1;
  }

  function parseLink(text, at) {
    // `at` points at "["; returns {label, href, end} or null
    let depth = 0, k = at;
    for (; k < text.length; k++) {
      const c = text[k];
      if (c === "\\") { k++; continue; }
      if (c === "`") { const end = codeSpanEnd(text, k); if (end > 0) { k = end - 1; continue; } }
      if (c === "[") depth++;
      else if (c === "]") { depth--; if (!depth) break; }
    }
    if (k >= text.length || text[k + 1] !== "(") return null;
    const label = text.slice(at + 1, k);
    let p = k + 2;
    while (text[p] === " ") p++;
    let href = "";
    if (text[p] === "<") {
      const close = text.indexOf(">", p);
      if (close < 0) return null;
      href = text.slice(p + 1, close);
      p = close + 1;
    } else {
      let parens = 0;
      for (; p < text.length; p++) {
        const c = text[p];
        if (/\s/.test(c)) break;
        if (c === "(") parens++;
        else if (c === ")") { if (!parens) break; parens--; }
        href += c;
      }
    }
    while (text[p] === " ") p++;
    const title = /^(?:"[^"]*"|'[^']*')/.exec(text.slice(p));
    if (title) { p += title[0].length; while (text[p] === " ") p++; }
    if (text[p] !== ")") return null;
    return { label, href, end: p + 1 };
  }

  function link(href, kids) {
    const safe = safeHref(href);
    if (!safe) return h("span", { class: "md-link-off", title: "Not opened: only web and mail links are allowed" }, kids);
    return h("a", { class: "md-link", href: safe, target: "_blank", rel: "noopener noreferrer", title: safe },
      kids, h("span", { class: "sr-only" }, " (opens in a new window)"));
  }

  const BARE_URL = /^(?:https?:\/\/|mailto:)[^\s<>"]+/;

  function inline(text, noLinks = false) {
    const out = [];
    let buf = "";
    const flush = () => { if (buf) { pushText(out, buf); buf = ""; } };
    let i = 0;
    while (i < text.length) {
      const c = text[i];
      if (c === "\\" && i + 1 < text.length && PUNCT.test(text[i + 1])) { buf += text[i + 1]; i += 2; continue; }
      if (c === "\n") { flush(); out.push(h("br")); i++; continue; }
      if (c === "`") {
        const end = codeSpanEnd(text, i);
        const n = runLength(text, i, "`");
        if (end < 0) { buf += text.slice(i, i + n); i += n; continue; }
        let code = text.slice(i + n, end - n).replace(/\n/g, " ");
        if (code.length > 2 && code.startsWith(" ") && code.endsWith(" ") && code.trim()) code = code.slice(1, -1);
        flush(); out.push(h("code", { class: "md-code-inline" }, code));
        i = end; continue;
      }
      if (!noLinks && (c === "[" || (c === "!" && text[i + 1] === "["))) {
        const image = c === "!";
        const got = parseLink(text, image ? i + 1 : i);
        if (got) {
          flush();
          out.push(image ? link(got.href, [`Image: ${got.label || got.href}`]) : link(got.href, inline(got.label, true)));
          i = got.end; continue;
        }
      }
      if (!noLinks && c === "<") {
        const auto = /^<((?:https?:\/\/|mailto:)[^\s<>]+)>/.exec(text.slice(i, i + 2048));
        if (auto) { flush(); out.push(link(auto[1], [auto[1]])); i += auto[0].length; continue; }
      }
      if (!noLinks && (c === "h" || c === "m") && !isWord(text[i - 1])) {
        const bare = BARE_URL.exec(text.slice(i, i + 2048));
        if (bare) {
          let url = bare[0].replace(/[.,:;!?*_~'"]+$/, "");
          // keep a closing parenthesis only when the URL opened one
          while (url.endsWith(")") && (url.match(/\(/g) || []).length < (url.match(/\)/g) || []).length) url = url.slice(0, -1);
          if (url.length > 8) { flush(); out.push(link(url, [url])); i += url.length; continue; }
        }
      }
      if (c === "~" && text[i + 1] === "~") {
        const close = text.indexOf("~~", i + 2);
        if (close > i + 2 && !isSpace(text[i + 2]) && !isSpace(text[close - 1])) {
          flush(); out.push(h("del", { class: "md-del" }, inline(text.slice(i + 2, close), noLinks)));
          i = close + 2; continue;
        }
      }
      if (c === "*" || c === "_") {
        const n = runLength(text, i, c);
        const opens = !isSpace(text[i + n]) && (c !== "_" || !isWord(text[i - 1]));
        if (opens && n <= 3) {
          const close = findCloser(text, i + n, c, n);
          if (close > 0) {
            const inner = inline(text.slice(i + n, close), noLinks);
            flush();
            out.push(n === 1 ? h("em", null, inner) : n === 2 ? h("strong", null, inner) : h("strong", null, h("em", null, inner)));
            i = close + n; continue;
          }
        }
        buf += text.slice(i, i + n); i += n; continue;
      }
      buf += c; i++;
    }
    flush();
    return out;
  }

  function pushText(out, text) { out.push(document.createTextNode(text)); }

  RE.markdown = function markdown(text) {
    const source = String(text ?? "").replace(/\r\n?/g, "\n");
    const frag = document.createDocumentFragment();
    frag.append(...blocks(source.split("\n")));
    return frag;
  };
  RE.markdown.inline = (text) => { const f = document.createDocumentFragment(); f.append(...inline(String(text ?? ""))); return f; };
  RE.markdown.codeBlock = codeBlock;
})();
