// Attachments, in the page: a paperclip that opens a picker taking any kind of file, files dropped on the window or
// pasted (Ctrl+V of files or a screenshot), chips that show them (each with a × to take it off), sizes and counts
// checked in plain words, and what a screen reader hears when files come and go.
//
// SHARED, byte for byte, by four studios built alike, each with the same three files: app/attachments.py,
// static/attach.js (this) and static/attach.css. Change it in one studio, then copy it to all four; never edit one copy
// alone. It imports nothing: what a studio decides comes in through attacher()'s options. WCAG 2.2 AA: every control a
// real button with a name, its focus ring at 3:1, all of it by keyboard alone, every change said to a screen reader.
//
// attacher(options) -> { button, input, files, add(list), remove(i), set(list), clear(), open(), payload() }
//   button   the studio's own "Attach files" button (given none, one is made: .button); a paperclip is drawn in it
//   chips    where the chips are drawn (required)
//   zones    elements lit, with "Drop the files to attach them", while files are dragged over the window and this
//            attacher is the one active; a drop anywhere in the window is then its
//   paste    elements whose paste of files (or a screenshot) is taken; pasted words stay words
//   active   () => whether it takes what is dropped now (default: its button is shown); the newest active one does
//   limits   { file, message, count } or () => them (default LIMITS)
//   prepare  async (file, name) => { name, data, size } made ready (a picture made small), or null to send it as it is;
//            an Error thrown refuses it, in its own words
//   blocked  () => whether attaching is shut just now (the paperclip and a drop or paste do nothing)
//   files    the list it starts with (changed in place); onChange(files) after any change; onError(words) shows and says a
//            refusal (default: a line under the chips, role=alert); announce(words) (default: a live region of its own)
// chip(file, { remove, href, view, thumb }) draws one chip: with remove, one waiting to be sent; with href, one of a
// message's, a link that opens it (view: a picture or a PDF, in a new window) or downloads it.

export const MB = 1024 * 1024;
export const LIMITS = { file: 25 * MB, message: 70 * MB, count: 12 };

export function sizeWords(bytes) {
  if (bytes < 1024) return bytes === 1 ? "1 byte" : `${bytes} bytes`;
  if (bytes < MB) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(Math.ceil((bytes * 10) / MB) / 10).toFixed(1).replace(/\.0$/, "")} MB`;   // rounded up, as the server says it
}

export const KIND_WORDS = { picture: "a picture", pdf: "a PDF", text: "text", office: "an office file", audio: "a sound",
  video: "a video", archive: "an archive", other: "a file" };
const EXTENSIONS = {
  picture: "png jpg jpeg gif webp bmp tif tiff heic heif avif ico",
  pdf: "pdf",
  text: "txt md markdown csv tsv json xml yaml yml html htm svg rtf log ini js mjs ts css py sql sh bat ps1 ics vcf",
  office: "doc docx docm xls xlsx xlsm ppt pptx pptm odt ods odp",
  audio: "mp3 m4a aac wav ogg oga opus flac amr aiff aif mid midi wma",
  video: "mp4 m4v mov webm mkv avi wmv flv mpg mpeg 3gp ts mts",
  archive: "zip 7z rar gz tgz bz2 xz tar zst",
};
const BY_EXTENSION = Object.fromEntries(Object.entries(EXTENSIONS).flatMap(([kind, list]) => list.split(" ").map((ext) => [ext, kind])));

// A file's kind as the page guesses it, from its type and its name (the studio's server decides by its bytes).
export function kindOf(file) {
  const type = String(file.type || "").toLowerCase();
  if (type === "image/svg+xml") return "text";
  if (type.startsWith("image/")) return "picture";
  if (type === "application/pdf") return "pdf";
  if (type.startsWith("video/")) return "video";
  if (type.startsWith("audio/")) return "audio";
  if (/^text\/|\/(json|xml)$/.test(type)) return "text";
  if (/msword|ms-excel|ms-powerpoint|officedocument|opendocument/.test(type)) return "office";
  if (/zip|7z|rar|gzip|x-tar|bzip|x-xz|zstd/.test(type)) return "archive";
  const ext = String(file.name || "").toLowerCase().split(".").slice(1).pop() || "";
  return BY_EXTENSION[ext] || "other";
}

const SVG = "http://www.w3.org/2000/svg";
const PATHS = {
  clip: ["M20.4 11.1l-8.6 8.6a5.5 5.5 0 0 1-7.8-7.8l8.6-8.6a3.7 3.7 0 0 1 5.2 5.2l-8.6 8.6a1.8 1.8 0 0 1-2.6-2.6l7.9-7.9"],
  picture: ["M4 5h16v14H4z", "M4 16l5-5 4 4 2-2 5 5", "M15.5 8.5h.01"],
  pdf: ["M6 3h8l4 4v14H6z", "M14 3v4h4", "M9 13h6", "M9 17h4"],
  text: ["M6 3h8l4 4v14H6z", "M14 3v4h4", "M9 12h6", "M9 16h6"],
  office: ["M4 4h16v16H4z", "M4 10h16", "M4 15h16", "M10 4v16"],
  audio: ["M9 18V5l11-2v13", "M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6z", "M17 19a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"],
  video: ["M4 5h16v14H4z", "M8 5v14", "M16 5v14", "M4 9.5h4", "M4 14.5h4", "M16 9.5h4", "M16 14.5h4"],
  archive: ["M4 8h16v12H4z", "M3 4h18v4H3z", "M10 12h4"],
  other: ["M6 3h8l4 4v14H6z", "M14 3v4h4"],
  x: ["M6 6l12 12", "M18 6L6 18"],
};

export function icon(name, size = 16) {
  const svg = document.createElementNS(SVG, "svg");
  const attrs = { viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor", "stroke-width": 1.9,
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false", class: "att-icon" };
  for (const [key, value] of Object.entries(attrs)) svg.setAttribute(key, value);
  for (const d of PATHS[name] || PATHS.other) {
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    node.setAttribute(name, value === true ? "" : value);
  }
  node.append(...kids.flat().filter((kid) => kid !== null && kid !== undefined && kid !== false));
  return node;
}

const DRAWN = /^data:image\/(png|jpeg|gif|webp|bmp|avif)[;,]/;   // a picture an <img> draws, as the page holds it

// One chip: a picture's thumbnail or its kind's mark, its name and size; with `remove`, a 24 px × named "Remove
// <name>"; with `href`, a link that opens it (`view`) or downloads it.
export function chip(file, { remove = null, href = "", view = false, thumb = "" } = {}) {
  const kind = file.kind || kindOf(file);
  const name = String(file.name || "file");
  const size = sizeWords(file.size || 0);
  const said = `${name}, ${KIND_WORDS[kind] || KIND_WORDS.other}, ${size}`;
  const picture = thumb || (DRAWN.test(file.data || "") ? file.data : "");
  const mark = el("span", { class: "att-mark" }, icon(kind, 14));
  let lead = mark;
  if (picture) {
    lead = el("img", { class: "att-thumb", src: picture, alt: "" });
    lead.addEventListener("error", () => lead.replaceWith(mark), { once: true });
  }
  const parts = [lead, el("span", { class: "att-name" }, name), el("span", { class: "att-size" }, size)];
  if (href) {
    return el("a", { class: "att-chip", href, title: `${name} · ${size}`, ...(view
      ? { target: "_blank", rel: "noopener noreferrer", "aria-label": `Open ${said} (in a new window)` }
      : { download: name, "aria-label": `Download ${said}` }) }, parts);
  }
  const node = el("span", { class: "att-chip", role: "listitem", title: `${name} · ${size}` }, parts);
  if (remove) {
    const button = el("button", { class: "att-remove", type: "button", "aria-label": `Remove ${name}`, title: "Remove" }, icon("x", 12));
    button.addEventListener("click", remove);
    node.append(button);
  }
  return node;
}

function readAsData(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error(`${file.name || "That file"} could not be read.`));
    reader.readAsDataURL(file);
  });
}

// A screenshot pasted is "image.png" to the browser: it gets a name of its own, by the time it was pasted.
function pastedName(file) {
  const name = String(file.name || "");
  if (name && !/^image\.(png|jpe?g|gif|webp|bmp)$/i.test(name)) return name;
  const ext = (name.split(".").pop() || String(file.type || "").split("/").pop() || "png").toLowerCase().replace("jpeg", "jpg");
  const now = new Date();
  const two = (n) => String(n).padStart(2, "0");
  return `pasted-${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())}-${two(now.getHours())}${two(now.getMinutes())}${two(now.getSeconds())}.${ext}`;
}

const joined = (names) => (names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0] || "");

// ---- dropping on the window: one set of listeners for every attacher in the page ----

const attachers = new Set();
const drag = { depth: 0, lit: null, timer: 0 };
const carriesFiles = (event) => [...((event.dataTransfer && event.dataTransfer.types) || [])].includes("Files");
const taker = () => [...attachers].reverse().find((one) => one.active()) || null;

function light(on) {
  const next = on ? taker() : null;
  if (drag.lit === next) return;
  if (drag.lit) {
    for (const zone of drag.lit.zones) {
      zone.removeAttribute("data-att-drop");
      if (zone.hasAttribute("data-att-placed")) {
        zone.style.position = "";
        zone.removeAttribute("data-att-placed");
      }
    }
  }
  drag.lit = next;
  if (next) {
    for (const zone of next.zones) {
      if (getComputedStyle(zone).position === "static") {   // its overlay is laid over it
        zone.style.position = "relative";
        zone.setAttribute("data-att-placed", "");
      }
      zone.setAttribute("data-att-drop", next.dropWords);
    }
  }
}

function listen() {
  if (listen.done) return;
  listen.done = true;
  const off = () => {
    drag.depth = 0;
    light(false);
  };
  document.addEventListener("dragenter", (event) => {
    if (!carriesFiles(event)) return;
    drag.depth += 1;
    light(true);
  });
  document.addEventListener("dragleave", (event) => {
    if (!carriesFiles(event)) return;
    drag.depth = Math.max(0, drag.depth - 1);
    if (!drag.depth) off();
  });
  document.addEventListener("dragover", (event) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();   // dropped here, a file is attached, never opened in place of the page
    event.dataTransfer.dropEffect = taker() ? "copy" : "none";
    if (!drag.lit) light(true);
    clearTimeout(drag.timer);
    drag.timer = setTimeout(off, 400);   // gone from the window without a word
  });
  document.addEventListener("drop", (event) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    clearTimeout(drag.timer);
    off();
    const one = taker();
    if (one) one.add(event.dataTransfer.files);
  });
}

// ---- one attacher ----

export function attacher(options = {}) {
  const o = options;
  const chips = o.chips;
  let files = o.files || [];
  const button = o.button || el("button", { class: "att-button", type: "button" }, "Attach files");
  if (!button.querySelector("svg")) button.prepend(icon("clip", 16));
  if (!button.textContent.trim() && !button.hasAttribute("aria-label")) button.setAttribute("aria-label", "Attach files");
  const input = el("input", { type: "file", multiple: true, hidden: true, tabindex: "-1", "aria-hidden": "true", "data-att-input": "" });
  chips.classList.add("att-chips");
  chips.setAttribute("role", "list");
  if (!chips.hasAttribute("aria-label")) chips.setAttribute("aria-label", "Attached files");
  let errorLine = null;
  let live = null;

  const limits = () => ({ ...LIMITS, ...(typeof o.limits === "function" ? o.limits() : o.limits) });
  const total = () => files.reduce((sum, f) => sum + (f.size || 0), 0);
  const count = () => (files.length ? `${files.length} ${files.length === 1 ? "file" : "files"} attached, ${sizeWords(total())}.` : "Nothing attached.");
  const changed = () => o.onChange && o.onChange(files);

  function say(words, urgent = false) {
    if (o.announce) return void o.announce(words, { urgent });
    if (!live) {
      live = el("div", { class: "att-live", role: "status" });
      chips.after(live);
    }
    live.textContent = "";
    requestAnimationFrame(() => { live.textContent = words; });   // the same words twice are said twice
  }

  function refuse(words) {
    if (o.onError) return void o.onError(words);   // the studio shows it, and says it
    if (!errorLine) {
      errorLine = el("p", { class: "att-error", role: "alert" });
      (live || chips).after(errorLine);
    }
    errorLine.textContent = words;
  }

  function draw() {
    chips.replaceChildren(...files.map((file, i) => chip(file, { remove: () => remove(i) })));
  }

  function remove(i, { focus = true } = {}) {
    const [gone] = files.splice(i, 1);
    if (!gone) return;
    draw();
    changed();
    say(`${gone.name} removed. ${count()}`);
    if (focus) {
      const left = chips.querySelectorAll(".att-remove");
      (left[i] || left[i - 1] || button).focus();   // the × that took its place, else the one before, else the paperclip
    }
  }

  async function add(list, { pasted = false } = {}) {
    if (o.blocked && o.blocked()) return;
    const most = limits();
    const incoming = [...(list || [])];
    const added = [];
    const refused = [];
    if (errorLine) errorLine.textContent = "";
    for (let i = 0; i < incoming.length; i += 1) {
      const file = incoming[i];
      const name = pasted ? pastedName(file) : String(file.name || "file");
      if (files.length >= most.count) {
        const left = incoming.length - i;
        refused.push(`Attach up to ${most.count} files at a time: ${left === 1 ? `${name} was` : `${name} and ${left - 1} more were`} not added.`);
        break;
      }
      if (!file.size) {
        refused.push(`${name} is empty: there is nothing in it to attach.`);
        continue;
      }
      if (file.size > most.file) {
        refused.push(`${name} is ${sizeWords(file.size)}: a file can be up to ${sizeWords(most.file)}.`);
        continue;
      }
      let made;
      try {
        made = (o.prepare && (await o.prepare(file, name))) || { name, data: await readAsData(file), size: file.size };
      } catch (error) {
        refused.push(error && error.message ? error.message : `${name} could not be read.`);
        continue;
      }
      made = { type: file.type || "", kind: kindOf({ type: file.type, name }), ...made };
      if (total() + made.size > most.message) {
        refused.push(`${name} would take this message past ${sizeWords(most.message)}: send it with the next one.`);
        continue;
      }
      files.push(made);
      added.push(made);
    }
    if (added.length) {
      draw();
      changed();
      say(`${joined(added.map((f) => f.name))} attached. ${count()}`);
    }
    if (refused.length) refuse(refused.join(" "));
  }

  function open() {
    if (o.blocked && o.blocked()) return;
    if (input.parentNode !== button.parentNode && button.parentNode) button.after(input);
    input.click();
  }

  input.addEventListener("change", async () => {
    const picked = [...input.files];
    input.value = "";
    await add(picked);
  });
  button.addEventListener("click", open);
  for (const area of [].concat(o.paste || [])) {
    area.addEventListener("paste", (event) => {
      const data = event.clipboardData;
      if (!data || !data.files || !data.files.length) return;
      if ((data.getData("text/plain") || "").trim()) return;   // words (Excel and Word put a picture of them beside them)
      event.preventDefault();
      add(data.files, { pasted: true });
    });
  }

  const self = {
    button, input, add, remove, open,
    get files() { return files; },
    set(list) {
      files = list || [];
      draw();
    },
    clear() {
      files.splice(0, files.length);
      draw();
    },
    payload: () => files.map(({ name, data }) => ({ name, data })),
    zones: [].concat(o.zones || []),
    dropWords: o.dropWords || "Drop the files to attach them",
    active: () => (o.active ? !!o.active() : button.isConnected && button.getClientRects().length > 0),
  };
  attachers.add(self);
  listen();
  draw();
  return self;
}
