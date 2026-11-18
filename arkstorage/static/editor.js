/* Text editor: a transparent <textarea> on top of a syntax-coloured layer (highlight.js), with EditorCore doing the
 * smart editing (auto-indent, auto-closing pairs, comment toggle, find/replace).
 * Typing is handled on `beforeinput`, not `keydown`: phone keyboards send key "Unidentified" on keydown, which is
 * why auto-pairs and auto-indent used to be dead on mobile. Tab and shortcuts stay on keydown; a symbol bar
 * (Tab, brackets, quotes...) shows on touch screens. */
(function () {
  "use strict";
  const { h, icon, toast, modal, api, enc } = window.UI;
  const EC = window.EditorCore;

  const LANGS = {
    py: "python", pyw: "python", js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript",
    json: "json", jsonc: "json", css: "css", scss: "css", less: "css", html: "html", htm: "html", xml: "xml", svg: "xml", md: "markdown", markdown: "markdown",
    yml: "yaml", yaml: "yaml", toml: "toml", ini: "ini", cfg: "ini", conf: "ini", env: "ini", sh: "bash", bash: "bash", zsh: "bash",
    c: "c", h: "c", cpp: "cpp", cc: "cpp", hpp: "cpp", cs: "csharp", java: "java", kt: "kotlin", go: "go", rs: "rust", rb: "ruby", php: "php",
    sql: "sql", lua: "lua", swift: "swift", dart: "dart", ps1: "powershell", bat: "batch", cmd: "batch", tex: "latex", r: "r", pl: "perl",
    dockerfile: "docker", makefile: "make", mk: "make", vue: "html", txt: "text", log: "text", diff: "diff", patch: "diff",
  };
  // editor-core language id -> highlight.js language
  const HL = { python: "python", javascript: "javascript", typescript: "typescript", json: "json", css: "css", html: "xml", xml: "xml", markdown: "markdown",
    yaml: "yaml", toml: "ini", ini: "ini", bash: "bash", c: "c", cpp: "cpp", csharp: "csharp", java: "java", kotlin: "kotlin", go: "go", rust: "rust",
    ruby: "ruby", php: "php", sql: "sql", lua: "lua", swift: "swift", dart: "dart", powershell: "powershell", batch: "dos", latex: "latex", r: "r",
    perl: "perl", docker: "dockerfile", make: "makefile", diff: "diff" };
  const LABEL = { text: "Plain text", python: "Python", javascript: "JavaScript", typescript: "TypeScript", json: "JSON", css: "CSS / SCSS", html: "HTML / Vue", xml: "XML / SVG",
    markdown: "Markdown", yaml: "YAML", toml: "TOML", ini: "INI / .env", bash: "Shell", c: "C", cpp: "C++", csharp: "C#", java: "Java", kotlin: "Kotlin", go: "Go",
    rust: "Rust", ruby: "Ruby", php: "PHP", sql: "SQL", lua: "Lua", swift: "Swift", dart: "Dart", powershell: "PowerShell", batch: "Batch", latex: "LaTeX", r: "R",
    perl: "Perl", docker: "Dockerfile", make: "Makefile", diff: "Diff" };
  const langOf = (name) => {
    const low = name.toLowerCase();
    if (low === "dockerfile") return "docker";
    if (low === "makefile") return "make";
    const i = low.lastIndexOf(".");
    return LANGS[i >= 0 ? low.slice(i + 1) : low.startsWith(".") ? low.slice(1) : ""] || "text";
  };

  const MAX_HL = 600000;          // above this the colour layer is switched off (the textarea alone stays fast)
  const SYNC_HL = 150000;         // up to this size colours update on every keystroke; bigger files update ~120 ms after typing stops
  const SYMBOLS = ["{", "}", "(", ")", "[", "]", "<", ">", "\"", "'", "`", ";", ":", "=", "/", "\\", "|", "&", "!", "?", "#", "$", "_", "-", "+", "*", "%", "@", "~", "^"];
  const size = () => Math.max(10, Math.min(28, parseInt(localStorage.getItem("ark.edsize") || "14", 10) || 14));

  /** highlight.js HTML -> one HTML string per line, re-opening spans that continue across a line break. */
  function splitLines(html) {
    const out = [], re = /<span[^>]*>|<\/span>|\n|[^<\n]+/g, stack = [];
    let cur = "", m;
    while ((m = re.exec(html))) {
      const t = m[0];
      if (t === "\n") { out.push(cur + "</span>".repeat(stack.length)); cur = stack.join(""); }
      else if (t.charCodeAt(1) === 115 /* s */ && t.startsWith("<span")) { stack.push(t); cur += t; }
      else if (t === "</span>") { stack.pop(); cur += t; }
      else cur += t;
    }
    out.push(cur + "</span>".repeat(stack.length));
    return out;
  }
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  async function open(path, name, onSaved) {
    let data;
    try { data = await api("/api/text?path=" + enc(path)); }
    catch (e) { toast(e.message, "bad"); return false; }

    let lang = langOf(name);
    let text = data.content, sha = data.sha, saved = text;
    let unit = EC.detectIndent(text, lang).unit;      // detectIndent returns {unit, detected}; the string is what the editing code needs
    const o = () => ({ lang, unit });
    const hasHL = () => !!(window.hljs && HL[lang] && window.hljs.getLanguage(HL[lang]));

    // ---- DOM ----------------------------------------------------------------
    const ta = h("textarea", { id: "ta", spellcheck: false, autocapitalize: "none", autocomplete: "off", autocorrect: "off", wrap: "off", value: text, "aria-label": "Editor", enterkeyhint: "enter" });
    const pre = h("pre", { id: "hl", "aria-hidden": "true" });
    const gin = h("div", { class: "gin" });
    const gutter = h("div", { id: "gutter", "aria-hidden": "true" }, gin);
    const dot = h("span", { class: "dot", hidden: true, title: "Unsaved changes" });
    const pos = h("span", { class: "pos" });
    const findCount = h("span", { class: "cnt" });
    const fIn = h("input", { class: "inp", placeholder: "Find", "aria-label": "Find" });
    const rIn = h("input", { class: "inp", placeholder: "Replace", "aria-label": "Replace" });
    let fCase = false, fRegex = false, matches = [], cur = -1;
    const mk = (label, fn, cls, title) => h("button", { class: "btn " + (cls || ""), type: "button", title: title || null, onclick: fn }, label);
    const caseBtn = mk("Aa", () => { fCase = !fCase; caseBtn.classList.toggle("primary", fCase); search(); }, "", "Match case");
    const reBtn = mk(".*", () => { fRegex = !fRegex; reBtn.classList.toggle("primary", fRegex); search(); }, "", "Regular expression");
    const findBar = h("div", { id: "efind", hidden: true }, fIn, mk("↑", () => step(true), "", "Previous"), mk("↓", () => step(false), "", "Next"), caseBtn, reBtn, rIn,
      mk("Replace", replaceOne), mk("All", replaceAllFn), findCount, mk("✕", () => { findBar.hidden = true; ta.focus(); }, "ghost icon", "Close find"));
    const sizeLbl = h("span", { class: "muted esize" });
    const saveBtn = h("button", { class: "btn primary", type: "button", onclick: save }, icon("save"), h("span", { class: "lb" }, "Save"));
    const wrapBtn = h("button", { class: "btn icon ghost", type: "button", title: "Toggle word wrap", "aria-label": "Word wrap", onclick: () => setWrap(!ta.classList.contains("wrap")) }, icon("wrap"));
    const indentBtn = h("button", { class: "estat-btn", type: "button", title: "Change indentation (click to cycle)", onclick: cycleIndent });
    const langSel = h("select", { class: "estat-sel", "aria-label": "Language", title: "Language (syntax colours)", onchange: () => { lang = langSel.value; indentLabel(); paint(true); } },
      ...Object.keys(LABEL).sort((a, b) => (a === "text" ? -1 : b === "text" ? 1 : LABEL[a].localeCompare(LABEL[b]))).map((k) => h("option", { value: k }, LABEL[k])));
    langSel.value = lang in LABEL ? lang : "text";
    const keyBtn = (label, fn, title, cls) => h("button", { class: "ekey " + (cls || ""), type: "button", tabindex: -1, title: title || null, onmousedown: (e) => e.preventDefault(), onclick: () => { ta.focus(); fn(); } }, label);
    const keys = h("div", { class: "ekeys", role: "toolbar", "aria-label": "Symbols" },
      keyBtn("Tab", () => run(EC.tab(ta.value, ta.selectionStart, ta.selectionEnd, false, o())), "Indent", "wide"),
      keyBtn("⇤", () => run(EC.tab(ta.value, ta.selectionStart, ta.selectionEnd, true, o())), "Outdent"),
      keyBtn("↶", () => cmd("undo"), "Undo"), keyBtn("↷", () => cmd("redo"), "Redo"),
      ...SYMBOLS.map((c) => keyBtn(c, () => typeSymbol(c))));
    const root = h("div", { id: "editor", role: "dialog", "aria-label": "Editor: " + name },
      h("div", { class: "ebar" },
        h("div", { class: "t" }, h("span", { class: "nm" }, name), dot, h("span", { class: "muted pth" }, path)),
        h("div", { class: "esz" },
          h("button", { class: "btn icon ghost", type: "button", title: "Smaller text", "aria-label": "Smaller text", onclick: () => setSize(size() - 1) }, "−"), sizeLbl,
          h("button", { class: "btn icon ghost", type: "button", title: "Larger text", "aria-label": "Larger text", onclick: () => setSize(size() + 1) }, "+")),
        wrapBtn, h("button", { class: "btn icon ghost", type: "button", title: "Find (Ctrl+F)", "aria-label": "Find", onclick: toggleFind }, icon("find")),
        saveBtn, h("button", { class: "btn", type: "button", onclick: () => close() }, "Close")),
      findBar, h("div", { class: "ewrap" }, gutter, h("div", { class: "ecode" }, pre, ta)), keys,
      h("div", { class: "estat" }, pos, h("span", { class: "spacer" }), indentBtn, langSel, h("span", { class: "enc" }, "UTF-8")));
    document.body.append(root);
    let release = window.UI.pushOverlay ? window.UI.pushOverlay(() => close(true)) : () => {};

    // ---- layout settings --------------------------------------------------------
    function setSize(n) {
      n = Math.max(10, Math.min(28, n)); localStorage.setItem("ark.edsize", String(n));
      root.style.setProperty("--ed-size", n + "px"); root.style.setProperty("--ed-lh", Math.round(n * 1.6) + "px"); sizeLbl.textContent = n;
      layout();
    }
    function setWrap(on) {
      ta.classList.toggle("wrap", on); pre.classList.toggle("wrap", on); ta.setAttribute("wrap", on ? "soft" : "off");
      wrapBtn.classList.toggle("on", on); localStorage.setItem("ark.edwrap", on ? "1" : "0"); layout();
    }
    function indentLabel() {
      root.style.setProperty("--ed-tab", unit === "\t" ? 4 : unit.length || 4);
      indentBtn.textContent = unit === "\t" ? "Tabs" : "Spaces: " + unit.length;
    }
    function cycleIndent() {
      const seq = ["\t", "  ", "    ", "        "], i = seq.indexOf(unit);
      unit = seq[(i + 1) % seq.length]; indentLabel(); layout(); ta.focus();
    }
    // wrapped lines make a line taller than one row: the pre needs the textarea's text width, the gutter each line's height
    function layout() {
      pre.style.width = ta.classList.contains("wrap") ? ta.clientWidth + "px" : "";
      if (!ta.classList.contains("wrap") || !pre.children.length || pre.children.length !== gin.children.length) {
        for (const n of gin.children) n.style.height = "";
        return;
      }
      const hs = []; for (const c of pre.children) hs.push(c.offsetHeight);
      for (let i = 0; i < hs.length; i++) gin.children[i].style.height = hs[i] + "px";
    }
    if (window.ResizeObserver) new ResizeObserver(() => layout()).observe(ta); else window.addEventListener("resize", layout);

    // ---- painting ----------------------------------------------------------------
    let curLine = 0, curPre = null, curGut = null, hlTimer = 0, lastN = -1;
    function paintHighlight() {
      const t = ta.value;
      if (!hasHL() || t.length > MAX_HL) { pre.hidden = true; ta.classList.add("plain"); pre.replaceChildren(); return; }
      pre.hidden = false; ta.classList.remove("plain");
      let html;
      try { html = window.hljs.highlight(t, { language: HL[lang], ignoreIllegals: true }).value; } catch (e) { html = esc(t); }
      const lines = splitLines(html);
      pre.innerHTML = '<div class="l">' + lines.join('</div><div class="l">') + "</div>";
      curPre = null; layout(); markLine(true);
    }
    function paint(force) {
      const t = ta.value;
      if (force || t.length <= SYNC_HL) { clearTimeout(hlTimer); paintHighlight(); }
      else {
        pre.hidden = false; pre.textContent = t;      // never leave typed text invisible while colours catch up
        clearTimeout(hlTimer); hlTimer = setTimeout(paintHighlight, 120);
      }
      buildGutter();
    }
    function buildGutter() {
      const n = ta.value.split("\n").length;
      if (n === lastN && gin.children.length === n) return;
      lastN = n;
      let s = ""; for (let i = 1; i <= n; i++) s += '<div class="n">' + i + "</div>";
      gin.innerHTML = s; curGut = null; layout(); markLine(true);
    }
    function markLine(force) {
      const lc = EC.lineCol(ta.value, ta.selectionStart).line;
      if (!force && lc === curLine && curPre) return;
      curLine = lc;
      if (curPre) curPre.classList.remove("cur");
      if (curGut) curGut.classList.remove("cur");
      curPre = pre.firstElementChild && pre.firstElementChild.classList.contains("l") ? pre.children[lc - 1] || null : null;
      curGut = gin.children[lc - 1] || null;
      if (curPre) curPre.classList.add("cur");
      if (curGut) curGut.classList.add("cur");
    }
    const dirty = () => ta.value !== saved;
    function sync() {
      const lc = EC.lineCol(ta.value, ta.selectionStart);
      pos.textContent = `Ln ${lc.line}, Col ${lc.col}` + (ta.selectionEnd !== ta.selectionStart ? ` (${ta.selectionEnd - ta.selectionStart} selected)` : "");
      dot.hidden = !dirty();
      markLine();
    }
    function scrolled() {
      pre.style.transform = `translate(${-ta.scrollLeft}px, ${-ta.scrollTop}px)`;
      gin.style.transform = `translateY(${-ta.scrollTop}px)`;
    }

    // ---- applying edits (through the browser's editing stack, so Ctrl+Z keeps working) --------
    let applying = false;
    function apply(ed) {
      if (!ed) return false;
      applying = true;
      try {
        ta.focus();
        if (ed.insert === "" && ed.from === ed.to) { ta.setSelectionRange(ed.selStart, ed.selEnd); return true; }
        ta.setSelectionRange(ed.from, ed.to);
        let ok = false;
        try {
          if (typeof document.execCommand === "function") ok = ed.insert === "" ? document.execCommand("delete") : document.execCommand("insertText", false, ed.insert);
        } catch (e) { ok = false; }
        if (!ok) { ta.setRangeText(ed.insert, ed.from, ed.to, "end"); ta.dispatchEvent(new Event("input", { bubbles: true })); }
        ta.setSelectionRange(ed.selStart, ed.selEnd);
      } finally { applying = false; paint(); sync(); scrolled(); }
      return true;
    }
    const run = (ed) => apply(ed);
    function cmd(name) { try { document.execCommand(name); } catch (e) { /* unsupported */ } paint(); sync(); }
    function typeSymbol(ch) {
      const s = ta.selectionStart, e = ta.selectionEnd;
      apply(EC.typeChar(ta.value, s, e, ch, o()) || { from: s, to: e, insert: ch, selStart: s + 1, selEnd: s + 1 });
    }

    // ---- save / close ----------------------------------------------------------------
    async function save() {
      try {
        saveBtn.disabled = true;
        const r = await api("/api/text", { method: "PUT", body: { path, content: ta.value, sha } });
        sha = r.sha; saved = ta.value; sync(); toast("Saved");
        if (onSaved) onSaved();
        return true;
      } catch (e) {
        if (e.status === 409) {
          const v = await modal({ title: "File changed elsewhere", body: h("p", null, e.message), buttons: [{ label: "Cancel", value: null }, { label: "Overwrite", kind: "primary", value: "force" }] });
          if (v === "force") {
            try { const r = await api("/api/text", { method: "PUT", body: { path, content: ta.value, force: true } }); sha = r.sha; saved = ta.value; sync(); toast("Saved"); if (onSaved) onSaved(); return true; }
            catch (e2) { toast(e2.message, "bad"); }
          }
        } else toast(e.message, "bad");
        return false;
      } finally { saveBtn.disabled = false; }
    }
    let closing = false;
    async function close() {
      if (!root.isConnected) return true;
      if (closing) return false;
      closing = true;
      try {
        if (dirty()) {
          const v = await modal({ title: "Unsaved changes", body: h("p", null, `Save changes to ${name}?`),
            buttons: [{ label: "Cancel", value: null }, { label: "Discard", kind: "danger", value: "discard" }, { label: "Save", kind: "primary", value: "save" }] });
          if (!v) return false;
          if (v === "save" && !(await save())) return false;
        }
        window.removeEventListener("beforeunload", guard); document.removeEventListener("selectionchange", onSel);
        clearTimeout(hlTimer); root.remove(); release();
        return true;
      } finally { closing = false; }
    }
    const guard = (e) => { if (dirty()) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", guard);

    // ---- find / replace -------------------------------------------------------------
    function search() {
      const r = EC.findAll(ta.value, fIn.value, { regex: fRegex, caseSensitive: fCase, matchCase: fCase });
      matches = r.matches; cur = -1;
      findCount.textContent = r.error ? "bad regex" : fIn.value ? `${matches.length} found` : "";
    }
    function step(back) {
      if (!matches.length) search();
      const i = EC.nextIndex(matches, back ? ta.selectionStart : ta.selectionEnd, back);
      if (i < 0) return;
      cur = i;
      const m = matches[i];
      ta.focus(); ta.setSelectionRange(m.index, m.index + m.length);
      const row = pre.firstElementChild && pre.firstElementChild.classList.contains("l") ? pre.children[EC.lineCol(ta.value, m.index).line - 1] : null;
      const top = row ? row.offsetTop : (EC.lineCol(ta.value, m.index).line - 1) * parseFloat(getComputedStyle(ta).lineHeight);
      if (top < ta.scrollTop + 20 || top > ta.scrollTop + ta.clientHeight - 60) ta.scrollTop = Math.max(0, top - ta.clientHeight / 3);
      findCount.textContent = `${i + 1} / ${matches.length}`; sync(); scrolled();
    }
    function replaceOne() {
      const m = matches[cur];
      if (!m || ta.selectionStart !== m.index) { step(false); return; }
      apply({ from: m.index, to: m.index + m.length, insert: EC.replacementFor(m, fIn.value, rIn.value, { regex: fRegex }), selStart: m.index, selEnd: m.index });
      search(); step(false);
    }
    function replaceAllFn() {
      const r = EC.replaceAll(ta.value, fIn.value, rIn.value, { regex: fRegex, caseSensitive: fCase, matchCase: fCase });
      if (!r.count) return;
      apply({ from: 0, to: ta.value.length, insert: r.text, selStart: 0, selEnd: 0 }); search(); toast(`Replaced ${r.count}`);
    }
    function toggleFind() { findBar.hidden = !findBar.hidden; if (!findBar.hidden) { const s = ta.value.slice(ta.selectionStart, ta.selectionEnd); if (s && !s.includes("\n")) fIn.value = s; fIn.focus(); fIn.select(); search(); } else ta.focus(); layout(); }
    fIn.addEventListener("input", search);
    fIn.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); step(e.shiftKey); } });
    rIn.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); replaceOne(); } });

    // ---- typing: beforeinput (works with desktop AND phone keyboards) ----------------
    const bi = { seen: false };
    ta.addEventListener("beforeinput", (e) => {
      if (applying || e.isComposing) return;
      const s = ta.selectionStart, en = ta.selectionEnd, t = ta.value;
      let ed = null;
      bi.seen = e.inputType === "insertText";
      if (e.inputType === "insertLineBreak" || e.inputType === "insertParagraph") ed = EC.enter(t, s, en, o());
      else if (e.inputType === "insertText" && e.data && e.data.length === 1) ed = EC.typeChar(t, s, en, e.data, o());
      else if (e.inputType === "deleteContentBackward") ed = EC.backspace(t, s, en, o());
      if (ed && e.cancelable) { e.preventDefault(); apply(ed); }
    });
    // Some phone keyboards commit symbols as composition text, which cannot be cancelled: pair them after the fact.
    ta.addEventListener("input", (e) => {
      if (!applying && e.data && e.data.length === 1 && !bi.seen && (e.inputType === "insertCompositionText" || e.inputType === "insertText")) {
        const p = ta.selectionStart, t = ta.value;
        if (p === ta.selectionEnd && t.charAt(p - 1) === e.data) {
          const base = t.slice(0, p - 1) + t.slice(p), ed = EC.typeChar(base, p - 1, p - 1, e.data, o());
          if (ed && ed.insert.length === 2) apply({ from: p, to: p, insert: ed.insert.slice(1), selStart: p, selEnd: p });
          else if (ed && ed.insert === "" && ed.from === ed.to) apply({ from: p - 1, to: p, insert: "", selStart: p, selEnd: p });
        }
      }
      bi.seen = false;
      if (!applying) { paint(); sync(); scrolled(); }
    });

    ta.addEventListener("keydown", (e) => {
      const s = ta.selectionStart, en = ta.selectionEnd, t = ta.value, mod = e.ctrlKey || e.metaKey;
      let ed = null;
      if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); save(); return; }
      if (mod && e.key.toLowerCase() === "f") { e.preventDefault(); toggleFind(); return; }
      if (e.isComposing) return;
      if (e.key === "Tab" && !mod && !e.altKey) ed = EC.tab(t, s, en, e.shiftKey, o()) || { from: s, to: s, insert: "", selStart: s, selEnd: s };
      else if (mod && e.key === "/") ed = EC.toggleComment(t, s, en, o());
      else if (mod && e.shiftKey && e.key.toLowerCase() === "k") ed = EC.deleteLines(t, s, en);
      else if (mod && !e.shiftKey && e.key.toLowerCase() === "d") ed = EC.duplicateLines(t, s, en, 1);
      else if (e.altKey && !mod && e.key === "ArrowUp") ed = EC.moveLines(t, s, en, -1);
      else if (e.altKey && !mod && e.key === "ArrowDown") ed = EC.moveLines(t, s, en, 1);
      else if (mod && e.key === "Enter") ed = EC.insertLine(t, s, en, !e.shiftKey, o());
      if (ed) { e.preventDefault(); apply(ed); }
    });
    root.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (findBar.contains(document.activeElement)) { findBar.hidden = true; ta.focus(); layout(); e.stopPropagation(); }
      else if (!document.querySelector(".scrim")) { e.stopPropagation(); close(); }
    });
    ta.addEventListener("scroll", scrolled, { passive: true });
    ta.addEventListener("keyup", sync); ta.addEventListener("click", sync); ta.addEventListener("select", sync); ta.addEventListener("focus", sync);
    const onSel = () => { if (document.activeElement === ta) sync(); };
    document.addEventListener("selectionchange", onSel);

    // ---- start -------------------------------------------------------------------
    setSize(size());
    indentLabel();
    setWrap(localStorage.getItem("ark.edwrap") === "1");
    paint(true); sync(); scrolled();
    ta.focus();
    ta.setSelectionRange(0, 0);
    return true;
  }

  window.Editor = { open, langOf, splitLines };
})();
