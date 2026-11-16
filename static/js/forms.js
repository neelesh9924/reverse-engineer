/* Schema-built forms (RE.forms). One builder for every form made from a JSON Schema: the
   Tools page, and the REA workflow dialog. Built from REA's real input schemas, which use
   type lists (["integer","null"]), anyOf with a {"type":"null"} branch, anyOf of enum/const
   strings, allOf patterns, compact-profile objects with no properties, and maps.

   RE.forms.shape(spec)   -> a normalised field description (pure; no DOM)
   RE.forms.build(schema, {prefill, idPrefix, marks, templates, onChange})
       -> {el, read(), setErrors(errors), focusFirstError(), fill(values, {mark}), reset(),
           touched(key), controls}
   RE.forms.promptDialog(prompt, {slug, prefill}) -> Promise<{name, arguments} | null>
*/
(() => {
  "use strict";
  const RE = (window.RE = window.RE || {});
  const h = (...args) => RE.h(...args);

  const MAX_SAFE = 9007199254740991;
  const LONG_TEXT_KEYS = new Set(["question", "source", "script", "code", "comment", "text"]);
  let counter = 0;

  // ---------- shape: JSON Schema -> one of a few field kinds ----------
  // kinds: enum · boolean · number · string · list (array of scalars) · multi (array of enum) · json
  function isNullBranch(b) {
    if (!b || typeof b !== "object") return false;
    if (b.type === "null") return true;
    return Array.isArray(b.type) && b.type.length === 1 && b.type[0] === "null";
  }
  function uniq(values) {
    const out = [];
    for (const v of values) if (!out.some((o) => o === v)) out.push(v);
    return out;
  }
  function patternsOf(spec) {
    const out = [];
    if (typeof spec.pattern === "string") out.push(spec.pattern);
    for (const part of Array.isArray(spec.allOf) ? spec.allOf : []) {
      if (part && typeof part.pattern === "string") out.push(part.pattern);
    }
    return out;
  }

  function shape(input) {
    const spec = input && typeof input === "object" ? input : {};
    const base = { description: typeof spec.description === "string" ? spec.description : "",
      default: spec.default, nullable: false };
    let types = spec.type === undefined ? [] : [].concat(spec.type);
    if (types.includes("null")) { base.nullable = true; types = types.filter((t) => t !== "null"); }

    const branches = Array.isArray(spec.anyOf) ? spec.anyOf : Array.isArray(spec.oneOf) ? spec.oneOf : null;
    if (branches) {
      const live = branches.filter((b) => !isNullBranch(b));
      if (live.length < branches.length) base.nullable = true;
      if (live.length && live.every((b) => b && (Array.isArray(b.enum) || "const" in b))) {
        const options = uniq(live.flatMap((b) => (Array.isArray(b.enum) ? b.enum : [b.const])).filter((v) => v !== null));
        return { ...base, kind: "enum", options };
      }
      if (live.length === 1) {
        const inner = shape({ ...live[0], description: base.description || live[0].description,
          default: base.default !== undefined ? base.default : live[0].default });
        inner.nullable = inner.nullable || base.nullable;
        return inner;
      }
      if (live.length && live.every((b) => b && (b.type === "integer" || b.type === "number"))) {
        return { ...base, kind: "number", integer: live.every((b) => b.type === "integer") };
      }
      return { ...base, kind: "json", json: "any" };
    }
    if (Array.isArray(spec.enum)) {
      if (spec.enum.includes(null)) base.nullable = true;
      return { ...base, kind: "enum", options: uniq(spec.enum.filter((v) => v !== null)) };
    }
    if ("const" in spec) return { ...base, kind: "enum", options: [spec.const] };
    if (types.length > 1) {
      if (types.every((t) => t === "integer" || t === "number")) {
        return { ...base, kind: "number", integer: types.every((t) => t === "integer") };
      }
      return { ...base, kind: "json", json: "any" };
    }
    const type = types[0];
    if (type === "boolean") return { ...base, kind: "boolean" };
    if (type === "integer" || type === "number") {
      return { ...base, kind: "number", integer: type === "integer",
        minimum: num(spec.minimum), maximum: num(spec.maximum),
        exclusiveMinimum: num(spec.exclusiveMinimum), exclusiveMaximum: num(spec.exclusiveMaximum) };
    }
    if (type === "string") {
      return { ...base, kind: "string", minLength: num(spec.minLength), maxLength: num(spec.maxLength),
        patterns: patternsOf(spec), format: typeof spec.format === "string" ? spec.format : "" };
    }
    if (type === "array") {
      const item = shape(spec.items || {});
      const bounds = { minItems: num(spec.minItems), maxItems: num(spec.maxItems), uniqueItems: spec.uniqueItems === true };
      if (item.kind === "enum") return { ...base, ...bounds, kind: "multi", options: item.options };
      if (item.kind === "string" || item.kind === "number") return { ...base, ...bounds, kind: "list", item };
      return { ...base, ...bounds, kind: "json", json: "array", schema: spec };
    }
    if (type === "object") return { ...base, kind: "json", json: "object", schema: spec };
    // No type at all (REA's compact profile, e.g. "Evidence or exact retained reference"): any JSON.
    return { ...base, kind: "json", json: "any", schema: spec };
  }
  function num(v) { return typeof v === "number" && Number.isFinite(v) ? v : undefined; }

  // A starting JSON value for an object/array schema that names its properties.
  function skeleton(spec, depth = 0) {
    if (!spec || typeof spec !== "object" || depth > 3) return null;
    const s = shape(spec);
    if (s.kind === "json" && s.json === "object") {
      const props = spec.properties;
      if (!props || typeof props !== "object" || !Object.keys(props).length) return null;
      const required = new Set(spec.required || []);
      const keys = Object.keys(props);
      const chosen = keys.length <= 8 ? keys : keys.filter((k) => required.has(k));
      const out = {};
      for (const k of chosen) out[k] = sample(props[k], depth + 1);
      return out;
    }
    if (s.kind === "json" && s.json === "array") {
      const inner = skeleton(spec.items, depth + 1);
      return inner === null ? null : [inner];
    }
    return null;
  }
  function sample(spec, depth) {
    const s = shape(spec);
    if (s.default !== undefined) return s.default;
    if (s.kind === "enum") return s.options.length ? s.options[0] : null;
    if (s.nullable) return null;
    if (s.kind === "boolean") return false;
    if (s.kind === "number") return s.minimum !== undefined ? s.minimum : 0;
    if (s.kind === "string") return "";
    if (s.kind === "list" || s.kind === "multi") return [];
    const inner = skeleton(spec, depth);
    if (inner !== null) return inner;
    return s.json === "array" ? [] : {};
  }

  // ---------- plain-words rules shown under a field ----------
  function fmtNum(n) { return typeof n === "number" ? n.toLocaleString("en-US") : String(n); }
  function showValue(v) { return typeof v === "string" ? v : JSON.stringify(v); }
  function bounds(s) {
    let lo = s.minimum, hi = s.maximum;
    if (s.exclusiveMinimum !== undefined) lo = s.integer ? s.exclusiveMinimum + 1 : undefined;
    if (s.exclusiveMaximum !== undefined) hi = s.integer ? s.exclusiveMaximum - 1 : undefined;
    if (hi !== undefined && hi >= MAX_SAFE) hi = undefined;
    return { lo, hi };
  }
  function emptyListOk(s) {
    const list = s.kind === "list" || s.kind === "multi" || (s.kind === "json" && s.json === "array");
    return list && !(s.minItems > 0);
  }
  function ruleText(s, required) {
    const bits = [];
    if (s.kind === "number") {
      const { lo, hi } = bounds(s);
      let t = s.integer ? "Whole number" : "Number";
      if (lo !== undefined && hi !== undefined) t += `, ${fmtNum(lo)} to ${fmtNum(hi)}`;
      else if (lo !== undefined) t += `, ${fmtNum(lo)} or more`;
      else if (hi !== undefined) t += `, at most ${fmtNum(hi)}`;
      if (s.exclusiveMinimum !== undefined && !s.integer) t += `, more than ${fmtNum(s.exclusiveMinimum)}`;
      bits.push(t);
    } else if (s.kind === "string") {
      if (s.format === "uuid") bits.push("A UUID");
      if (s.maxLength !== undefined && s.maxLength < 100000) bits.push(`Up to ${fmtNum(s.maxLength)} characters`);
      if (s.minLength > 1) bits.push(`At least ${fmtNum(s.minLength)} characters`);
      const p = s.patterns.map(patternWords).find(Boolean);
      if (p) bits.push(p);
    } else if (s.kind === "list") {
      bits.push(s.item.kind === "number" ? (s.item.integer ? "Whole numbers, one per line" : "Numbers, one per line") : "One value per line");
    } else if (s.kind === "multi") {
      bits.push("Pick any");
    } else if (s.kind === "json") {
      bits.push(s.json === "object" ? "JSON object" : s.json === "array" ? "JSON array" : "JSON value");
    }
    if (s.kind === "list" || s.kind === "multi" || (s.kind === "json" && s.json === "array")) {
      if (s.minItems > 0) bits.push(`at least ${s.minItems}`);
      if (s.maxItems !== undefined) bits.push(`at most ${s.maxItems}`);
    }
    if (s.default !== undefined) bits.push(`default ${showValue(s.default)}`);
    if (required && emptyListOk(s)) bits.push("left blank, sends an empty list");
    if (s.nullable) bits.push("null allowed");
    return bits.join(" · ");
  }
  function patternWords(p) {
    if (/\[A-Za-z\]:/.test(p)) return "An absolute path";
    if (p === "^[^\\x00]*$") return "";
    if (p.length <= 48) return `Form: ${p}`;
    return "";
  }

  // ---------- validation ----------
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  function regex(p) {
    try { return new RegExp(p, "u"); } catch { try { return new RegExp(p); } catch { return null; } }
  }
  function checkString(s, text) {
    if (s.minLength !== undefined && text.length < s.minLength) {
      return s.minLength === 1 ? "Can’t be empty." : `Needs at least ${s.minLength} characters.`;
    }
    if (s.maxLength !== undefined && text.length > s.maxLength) return `Use at most ${fmtNum(s.maxLength)} characters.`;
    if (s.format === "uuid" && !UUID.test(text)) return "Enter a UUID, like 123e4567-e89b-12d3-a456-426614174000.";
    for (const p of s.patterns) {
      const re = regex(p);
      if (re && !re.test(text)) {
        if (/\[A-Za-z\]:/.test(p)) return "Enter an absolute path, like C:\\folder\\file.";
        if (p === "^[^\\x00]*$") return "Remove the NUL character.";
        return p.length <= 80 ? `Doesn’t match the expected form ${p}.` : "Doesn’t have the form REA expects.";
      }
    }
    return "";
  }
  function parseNumber(s, text) {
    const t = text.trim();
    if (!/^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(t)) return { error: s.integer ? "Enter a whole number." : "Enter a number." };
    const n = Number(t);
    if (!Number.isFinite(n)) return { error: "Enter a number." };
    if (s.integer && !Number.isInteger(n)) return { error: "Enter a whole number." };
    if (s.integer && Math.abs(n) > MAX_SAFE) return { error: "That number is too large." };
    if (s.minimum !== undefined && n < s.minimum) return { error: `Use ${fmtNum(s.minimum)} or more.` };
    if (s.exclusiveMinimum !== undefined && n <= s.exclusiveMinimum) return { error: `Use more than ${fmtNum(s.exclusiveMinimum)}.` };
    if (s.maximum !== undefined && n > s.maximum) return { error: `Use ${fmtNum(s.maximum)} or less.` };
    if (s.exclusiveMaximum !== undefined && n >= s.exclusiveMaximum) return { error: `Use less than ${fmtNum(s.exclusiveMaximum)}.` };
    return { value: n };
  }
  function checkCount(s, count) {
    if (s.minItems !== undefined && count < s.minItems) return `Give at least ${s.minItems}.`;
    if (s.maxItems !== undefined && count > s.maxItems) return `Give at most ${s.maxItems}.`;
    return "";
  }
  function checkJson(s, value) {
    if (s.json === "object" && (value === null || typeof value !== "object" || Array.isArray(value))) {
      return value === null && s.nullable ? "" : "Must be a JSON object: { … }.";
    }
    if (s.json === "array") {
      if (!Array.isArray(value)) return "Must be a JSON array: [ … ].";
      const n = checkCount(s, value.length);
      if (n) return n;
    }
    const spec = s.schema;
    if (s.json === "object" && spec && Array.isArray(spec.required)) {
      const missing = spec.required.filter((k) => !(k in value));
      if (missing.length) return `Missing ${missing.map((k) => `“${k}”`).join(", ")}.`;
    }
    return "";
  }

  // ---------- fields ----------
  function safeId(text) { return String(text).replace(/[^A-Za-z0-9_-]/g, "_"); }

  function makeField(key, spec, required, ctx) {
    const s = shape(spec);
    const id = `${ctx.idPrefix}-${safeId(key)}`;
    const hintId = `${id}-hint`, ruleId = `${id}-rule`, errId = `${id}-err`, markId = `${id}-mark`;
    const f = { key, shape: s, required, id, touched: false, marked: false, controls: [], focusEl: null };
    const touch = () => { f.touched = true; f.clearError(); if (ctx.onChange) ctx.onChange(key); };

    // the main control
    let control;
    let nullBox = null;
    const rules = ruleText(s, required);
    const describe = [s.description ? hintId : null, rules ? ruleId : null].filter(Boolean);
    // A required list left blank is sent as [] (REA's "required" lists are often empty), unless
    // the schema needs at least one item.
    const blank = required && emptyListOk(s) ? { set: true, value: [] } : { set: false };

    if (s.kind === "enum" || s.kind === "boolean") {
      const options = s.kind === "boolean" ? [true, false] : s.options;
      const unsetLabel = s.default !== undefined ? `Not set (REA default: ${showValue(s.default)})`
        : required && !s.nullable ? "Choose…" : "Not set";
      control = h("select", { class: "input f-select", id },
        h("option", { value: "" }, unsetLabel),
        s.nullable ? h("option", { value: "null" }, "null") : null,
        options.map((o, i) => h("option", { value: `o${i}` }, showValue(o))));
      control.addEventListener("change", touch);
      f.read = () => {
        if (control.value === "") return { set: false };
        if (control.value === "null") return { set: true, value: null };
        return { set: true, value: options[Number(control.value.slice(1))] };
      };
      f.set = (v) => {
        if (v === undefined) control.value = "";
        else if (v === null) control.value = s.nullable ? "null" : "";
        else {
          const i = options.findIndex((o) => o === v || String(o) === String(v));
          control.value = i >= 0 ? `o${i}` : "";
        }
      };
      f.controls = [control];
    } else if (s.kind === "multi") {
      const boxes = s.options.map((o, i) => {
        const cb = h("input", { type: "checkbox", id: `${id}-o${i}`, value: String(i) });
        cb.addEventListener("change", touch);
        return { cb, value: o, el: h("label", { class: "f-check", for: `${id}-o${i}` }, cb, h("span", null, showValue(o))) };
      });
      control = h("div", { class: `f-checks${s.options.length > 6 ? " many" : ""}` }, boxes.map((b) => b.el));
      f.read = () => {
        const picked = boxes.filter((b) => b.cb.checked).map((b) => b.value);
        if (!picked.length) return blank;
        const err = checkCount(s, picked.length);
        return err ? { error: err } : { set: true, value: picked };
      };
      f.set = (v) => {
        const list = Array.isArray(v) ? v : [];
        for (const b of boxes) b.cb.checked = list.some((x) => x === b.value);
      };
      f.controls = boxes.map((b) => b.cb);
    } else {
      const multiline = s.kind === "list" || s.kind === "json" || (s.kind === "string" && LONG_TEXT_KEYS.has(key));
      const attrs = { class: `input${multiline ? " area" : ""}${s.kind === "json" || /path|directory|(^|_)dir$|endpoint|address|_id$/.test(key) ? " mono" : ""}`,
        id, autocomplete: "off", spellcheck: "false" };
      // Numbers are text inputs: type=number turns "12a" into "" (silently unset) and REA's
      // bounds go up to 2^53-1. The rule line states the bounds; read() enforces them.
      if (s.kind === "number") Object.assign(attrs, { type: "text", inputmode: s.integer ? "numeric" : "decimal" });
      if (s.kind === "string" && s.maxLength !== undefined && !multiline) attrs.maxlength = s.maxLength;
      if (s.kind === "json") {
        const sk = ctx.templates && ctx.templates[key] !== undefined ? ctx.templates[key] : skeleton(s.schema || {});
        f.template = sk;
        const pretty = sk ? JSON.stringify(sk, null, 2) : "";
        attrs.rows = sk ? Math.min(8, pretty.split("\n").length) : 3;
        attrs.placeholder = sk ? pretty : s.json === "object" ? "{ }" : s.json === "array" ? "[ ]" : "JSON";
      } else if (s.kind === "list") {
        attrs.rows = 2;
        attrs.placeholder = "One per line";
      } else if (s.default !== undefined) {
        attrs.placeholder = `Default: ${showValue(s.default)}`;
      }
      if (multiline && s.kind === "string") attrs.rows = 3;
      control = h(multiline ? "textarea" : "input", attrs);
      control.addEventListener("input", touch);
      f.controls = [control];

      f.read = () => {
        if (nullBox && nullBox.checked) return { set: true, value: null };
        const raw = control.value;
        if (s.kind === "string") {
          if (raw === "") return { set: false };
          const err = checkString(s, raw);
          return err ? { error: err } : { set: true, value: raw };
        }
        if (s.kind === "number") {
          if (raw.trim() === "") return { set: false };
          const r = parseNumber(s, raw);
          return r.error ? { error: r.error } : { set: true, value: r.value };
        }
        if (s.kind === "list") {
          const lines = raw.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
          if (!lines.length) return blank;
          const out = [];
          for (const [i, line] of lines.entries()) {
            if (s.item.kind === "number") {
              const r = parseNumber(s.item, line);
              if (r.error) return { error: `Line ${i + 1}: ${r.error}` };
              out.push(r.value);
            } else {
              const err = checkString(s.item, line);
              if (err) return { error: `Line ${i + 1}: ${err}` };
              out.push(line);
            }
          }
          if (s.uniqueItems && new Set(out.map(String)).size !== out.length) return { error: "Each value can appear only once." };
          const n = checkCount(s, out.length);
          return n ? { error: n } : { set: true, value: out };
        }
        // json
        if (raw.trim() === "") return s.json === "array" ? blank : { set: false };
        let value;
        try { value = JSON.parse(raw); } catch (e) {
          return { error: `Not valid JSON${jsonWhere(e, raw)}.` };
        }
        const err = checkJson(s, value);
        return err ? { error: err } : { set: true, value };
      };
      f.set = (v) => {
        if (nullBox) nullBox.checked = v === null;
        if (v === undefined || v === null) control.value = "";
        else if (s.kind === "list") control.value = Array.isArray(v) ? v.join("\n") : String(v);
        else if (s.kind === "json") control.value = typeof v === "string" ? v : JSON.stringify(v, null, 2);
        else control.value = String(v);
        syncNull();
      };
    }
    f.focusEl = f.controls[0];

    // "Send null" for nullable text-like fields (enum/boolean carry null in their select)
    if (s.nullable && s.kind !== "enum" && s.kind !== "boolean") {
      nullBox = h("input", { type: "checkbox", id: `${id}-null`, "aria-label": `Send null for ${key}` });
      nullBox.addEventListener("change", () => { syncNull(); touch(); if (!nullBox.checked) control.focus(); });
    }
    function syncNull() {
      if (!nullBox) return;
      for (const c of f.controls) c.disabled = nullBox.checked;
    }

    const err = h("p", { class: "f-error", id: errId, hidden: true });
    const mark = h("span", { class: "f-mark", id: markId, hidden: true });
    const reqMark = required ? h("span", { class: "f-req", "aria-hidden": "true" }, "*") : null;
    const group = s.kind === "multi";
    const labelEl = group
      ? h("legend", { class: "f-label" }, h("span", { class: "f-key" }, key), reqMark, required ? h("span", { class: "sr-only" }, " (required)") : null)
      : h("label", { class: "f-label", for: id }, h("span", { class: "f-key" }, key), reqMark);
    const hint = s.description ? h("p", { class: `f-hint${s.description.length > 240 ? " long" : ""}`, id: hintId }, s.description) : null;
    let more = null;
    if (hint && s.description.length > 240) {
      more = h("button", { class: "f-more-btn", type: "button", "aria-expanded": "false", "aria-controls": hintId,
        "aria-label": `Show the full description of ${key}` }, "More");
      more.addEventListener("click", () => {
        const open = hint.classList.toggle("open");
        more.setAttribute("aria-expanded", String(open));
        more.textContent = open ? "Less" : "More";
      });
    }
    const rule = rules ? h("p", { class: "f-rule", id: ruleId }, rules) : null;
    let insert = null;
    if (s.kind === "json") {
      const tools = [];
      if (f.template) {
        const b = h("button", { class: "f-mini", type: "button", "aria-label": `Insert an example for ${key}` }, "Insert example");
        b.addEventListener("click", async () => {
          if (control.value.trim()) {
            const ok = await RE.confirm({ title: `Replace ${key}?`, message: "The example replaces what is in this field now.",
              confirmLabel: "Replace" });
            if (!ok) return;
          }
          control.value = JSON.stringify(f.template, null, 2);
          touch(); control.focus();
        });
        tools.push(b);
      }
      const fmt = h("button", { class: "f-mini", type: "button", "aria-label": `Format the JSON in ${key}` }, "Format");
      fmt.addEventListener("click", () => {
        try { control.value = JSON.stringify(JSON.parse(control.value), null, 2); f.clearError(); }
        catch (e) { f.showError(`Not valid JSON${jsonWhere(e, control.value)}.`); }
        control.focus();
      });
      tools.push(fmt);
      insert = h("span", { class: "f-tools" }, tools);
    }

    f.el = group
      ? h("fieldset", { class: "f-field f-group", "data-key": key }, labelEl, mark, control, hint, more, rule, err)
      : h("div", { class: `f-field f-${s.kind}`, "data-key": key },
          h("div", { class: "f-head" }, labelEl, mark, insert,
            nullBox ? h("label", { class: "f-null", for: `${id}-null` }, nullBox, h("span", null, "null")) : null),
          control, hint, more, rule, err);

    const applyDescribe = () => {
      const ids = [...describe];
      if (f.marked) ids.push(markId);
      if (!err.hidden) ids.push(errId);
      const target = group ? f.el : f.focusEl;
      if (ids.length) target.setAttribute("aria-describedby", ids.join(" "));
      else target.removeAttribute("aria-describedby");
    };
    if (required && !group) { control.required = true; control.setAttribute("aria-required", "true"); }
    f.showError = (message) => {
      err.textContent = message; err.hidden = false;
      for (const c of f.controls) c.setAttribute("aria-invalid", "true");
      if (group) f.el.setAttribute("aria-invalid", "true");
      f.el.classList.add("invalid");
      applyDescribe();
    };
    f.clearError = () => {
      if (err.hidden) return;
      err.hidden = true; err.textContent = "";
      for (const c of f.controls) c.removeAttribute("aria-invalid");
      f.el.removeAttribute("aria-invalid");
      f.el.classList.remove("invalid");
      applyDescribe();
    };
    f.setMark = (text) => {
      f.marked = !!text;
      mark.hidden = !text; mark.textContent = text || "";
      applyDescribe();
    };
    f.defaultNull = required && s.nullable;
    applyDescribe();
    return f;
  }
  function jsonWhere(e, raw) {
    const m = /position (\d+)/.exec(String(e && e.message));
    if (!m) return "";
    const upto = raw.slice(0, Number(m[1]));
    const line = upto.split("\n").length;
    return ` (line ${line})`;
  }

  // ---------- build ----------
  function build(schema, options = {}) {
    const sch = schema && typeof schema === "object" ? schema : {};
    const props = sch.properties && typeof sch.properties === "object" ? sch.properties : {};
    const required = new Set(Array.isArray(sch.required) ? sch.required : []);
    const ctx = { idPrefix: safeId(options.idPrefix || `form${++counter}`), templates: options.templates || {},
      onChange: options.onChange || null };
    const keys = Object.keys(props);
    const fields = keys.map((k) => makeField(k, props[k], required.has(k), ctx));
    const byKey = new Map(fields.map((f) => [f.key, f]));
    const reqFields = fields.filter((f) => f.required);
    const optFields = fields.filter((f) => !f.required);
    const formError = h("p", { class: "f-form-error", id: `${ctx.idPrefix}--form-err`, hidden: true });

    let moreBox = null;
    const flat = !reqFields.length || optFields.length <= 4;
    const pieces = [];
    if (!fields.length) pieces.push(h("p", { class: "f-none" }, "This tool takes no arguments."));
    if (reqFields.length) pieces.push(h("p", { class: "f-legend" }, h("span", { class: "f-req", "aria-hidden": "true" }, "*"), " marks a required field."));
    pieces.push(formError);
    if (flat) pieces.push(h("div", { class: "f-fields" }, fields.map((f) => f.el)));
    else {
      pieces.push(h("div", { class: "f-fields" }, reqFields.map((f) => f.el)));
      moreBox = h("details", { class: "f-optional" },
        h("summary", null, `${optFields.length} optional field${optFields.length === 1 ? "" : "s"}`),
        h("div", { class: "f-fields" }, optFields.map((f) => f.el)));
      pieces.push(moreBox);
    }
    const el = h("div", { class: "f-form" }, pieces);

    for (const f of fields) if (f.defaultNull) f.set(null);

    function fill(values, opts = {}) {
      for (const [k, v] of Object.entries(values || {})) {
        const f = byKey.get(k);
        if (!f) continue;
        if (opts.onlyUntouched && f.touched) continue;
        f.set(v === undefined && f.defaultNull ? null : v);
        f.setMark(v === undefined ? "" : opts.mark || "");
        if (opts.touch) f.touched = true;
        if (v !== undefined && !f.required && moreBox) moreBox.open = true;
      }
    }
    if (options.prefill) fill(options.prefill, { mark: options.mark || "" });
    if (options.marks) for (const [k, text] of Object.entries(options.marks)) { const f = byKey.get(k); if (f) f.setMark(text); }

    function read() {
      const value = {};
      const errors = [];
      for (const f of fields) {
        const r = f.read();
        if (r.error) errors.push({ field: f.key, message: r.error });
        else if (r.set) value[f.key] = r.value;
        else if (f.required) errors.push({ field: f.key, message: "Required." });
      }
      const min = typeof sch.minProperties === "number" ? sch.minProperties : 0;
      if (!errors.length && Object.keys(value).length < min) {
        errors.push({ field: null, message: `Fill in at least ${min} field${min === 1 ? "" : "s"}.` });
      }
      return { ok: !errors.length, value, errors };
    }
    function setErrors(errors) {
      for (const f of fields) f.clearError();
      formError.hidden = true; formError.textContent = "";
      const general = [];
      for (const e of errors || []) {
        const f = e && e.field ? byKey.get(e.field) : null;
        if (f) f.showError(e.message || "Check this field.");
        else if (e) general.push(e.field ? `${e.field}: ${e.message}` : e.message);
      }
      if (general.length) { formError.textContent = general.join(" "); formError.hidden = false; }
    }
    function focusFirstError() {
      const f = fields.find((x) => x.el.classList.contains("invalid"));
      if (!f) { if (!formError.hidden) formError.scrollIntoView({ block: "nearest" }); return false; }
      if (moreBox && moreBox.contains(f.el)) moreBox.open = true;
      const target = f.controls.find((c) => !c.disabled) || f.controls[0];
      target.focus({ preventScroll: true });
      target.scrollIntoView({ block: "center", behavior: "smooth" });
      return true;
    }
    function reset() {
      for (const f of fields) { f.set(f.defaultNull ? null : undefined); f.setMark(""); f.clearError(); f.touched = false; }
      formError.hidden = true;
    }
    return {
      el, read, setErrors, focusFirstError, fill, reset,
      touched: (k) => !!(byKey.get(k) && byKey.get(k).touched),
      controls: Object.fromEntries(fields.map((f) => [f.key, f.focusEl])),
      keys,
    };
  }

  // ---------- REA workflow (prompt) dialog ----------
  function promptDialog(prompt, options = {}) {
    const p = prompt || {};
    const args = Array.isArray(p.arguments) ? p.arguments.filter((a) => a && a.name) : [];
    const slug = options.slug || (RE.state && RE.state.slug) || null;
    const subject = slug && RE.state && Array.isArray(RE.state.subjects) ? RE.state.subjects.find((s) => s.slug === slug) : null;
    const auto = {};
    if (subject && subject.kind !== "website" && subject.path) {
      for (const a of args) if (a.name === "target_path") auto[a.name] = subject.path;
    }
    const given = options.prefill || {};
    const prefill = { ...auto, ...given };
    const properties = {};
    for (const a of args) properties[a.name] = { type: "string", description: a.description || "" };
    const schema = { type: "object", properties, required: args.filter((a) => a.required).map((a) => a.name) };
    const form = build(schema, { idPrefix: `prompt-${safeId(p.name || "x")}-${++counter}` });
    form.fill(given);
    form.fill(Object.fromEntries(Object.entries(auto).filter(([k]) => !(k in given) || given[k] === auto[k])), { mark: "from subject" });

    // completion: which arguments REA completes (when the catalogue says), else try all
    const params = p.parameters && typeof p.parameters === "object" ? p.parameters : null;
    const completes = (name) => (params ? !!(params[name] && params[name].completion) : true);
    const timers = {};
    const seqs = {};
    for (const a of args) {
      const input = form.controls[a.name];
      if (!input || !slug || !completes(a.name)) continue;
      const listId = `${input.id}-list`;
      const list = h("datalist", { id: listId });
      input.setAttribute("list", listId);
      input.after(list);
      const ask = () => {
        clearTimeout(timers[a.name]);
        timers[a.name] = setTimeout(async () => {
          const seq = (seqs[a.name] = (seqs[a.name] || 0) + 1);
          const current = form.read().value;
          const reply = await RE.post("/api/prompt/complete", { slug, name: p.name, argument: a.name, value: input.value,
            context: { arguments: current } });
          if (seq !== seqs[a.name] || !reply || !Array.isArray(reply.values)) return;
          list.replaceChildren(...reply.values.slice(0, 50).map((v) => h("option", { value: String(v) })));
        }, 250);
      };
      input.addEventListener("input", ask);
      input.addEventListener("focus", ask, { once: true });
    }

    return new Promise((resolve) => {
      let done = false;
      const finish = (value) => { if (!done) { done = true; resolve(value); } };
      const submit = h("button", { class: "btn primary", type: "submit" }, options.submitLabel || "Ask Claude");
      const cancel = h("button", { class: "btn", type: "button" }, "Cancel");
      const intro = p.description ? h("p", { class: "f-intro" }, p.description) : null;
      const note = slug ? null : h("p", { class: "f-intro" }, "Open a subject first: suggestions come from its engine.");
      const formEl = h("form", { class: "f-dialog-form", novalidate: true }, intro, note,
        args.length ? form.el : h("p", { class: "f-none" }, "This workflow takes no arguments."),
        h("div", { class: "modal-foot" }, cancel, submit));
      let handle = null;
      formEl.addEventListener("submit", (e) => {
        e.preventDefault();
        const r = form.read();
        form.setErrors(r.errors);
        if (!r.ok) {
          form.focusFirstError();
          if (RE.announce) RE.announce(`${r.errors.length} field${r.errors.length === 1 ? " needs" : "s need"} attention.`, "assertive");
          return;
        }
        finish({ name: p.name, arguments: r.value });
        if (handle) handle.close();
      });
      cancel.addEventListener("click", () => { finish(null); if (handle) handle.close(); });
      handle = RE.dialog.open({ title: p.title || p.name || "REA workflow", body: formEl, onClose: () => finish(null) });
      const first = args.length ? form.controls[(args.find((a) => !prefill[a.name]) || args[0]).name] : submit;
      if (first) setTimeout(() => { if (document.contains(first)) first.focus(); }, 0);
    });
  }

  RE.forms = { shape, build, promptDialog, skeleton, ruleText };
})();
