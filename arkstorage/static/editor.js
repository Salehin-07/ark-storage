/* Text editor: textarea + EditorCore (auto-indent, auto-closing pairs, comment toggle, find/replace, font size). */
(function () {
  "use strict";
  const { h, icon, toast, modal, api, enc } = window.UI;
  const EC = window.EditorCore;

  const LANGS = {
    py: "python", js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript",
    json: "json", css: "css", scss: "css", html: "html", htm: "html", xml: "xml", svg: "xml", md: "markdown", yml: "yaml", yaml: "yaml",
    toml: "toml", ini: "ini", sh: "bash", bash: "bash", zsh: "bash", c: "c", h: "c", cpp: "cpp", hpp: "cpp", cs: "csharp", java: "java",
    kt: "kotlin", go: "go", rs: "rust", rb: "ruby", php: "php", sql: "sql", lua: "lua", swift: "swift", dart: "dart", ps1: "powershell",
    bat: "batch", tex: "latex", r: "r", pl: "perl", dockerfile: "docker", makefile: "make", mk: "make", vue: "html", txt: "text", log: "text",
  };
  const langOf = (name) => {
    const low = name.toLowerCase();
    if (low === "dockerfile") return "docker";
    if (low === "makefile") return "make";
    const i = low.lastIndexOf(".");
    return LANGS[i >= 0 ? low.slice(i + 1) : ""] || "text";
  };

  const size = () => Math.max(10, Math.min(28, parseInt(localStorage.getItem("ark.edsize") || "14", 10)));

  async function open(path, name, onSaved) {
    let data;
    try { data = await api("/api/text?path=" + enc(path)); }
    catch (e) { toast(e.message, "bad"); return false; }

    const lang = langOf(name);
    let text = data.content, sha = data.sha, saved = text, unit = EC.detectIndent(text, lang);
    const ta = h("textarea", { id: "ta", spellcheck: false, autocapitalize: "none", autocomplete: "off", autocorrect: "off", value: text, "aria-label": "Editor" });
    if (localStorage.getItem("ark.edwrap") === "1") ta.classList.add("wrap");
    const gutter = h("div", { id: "gutter", "aria-hidden": "true" });
    const dot = h("span", { class: "dot", hidden: true, title: "Unsaved changes" });
    const pos = h("span"), info = h("span", null, `${lang} · ${unit === "\t" ? "tabs" : unit.length + " spaces"} · UTF-8`);
    const findCount = h("span", { class: "cnt" });
    const fIn = h("input", { class: "inp", placeholder: "Find", "aria-label": "Find" });
    const rIn = h("input", { class: "inp", placeholder: "Replace", "aria-label": "Replace" });
    let fCase = false, fRegex = false, matches = [], cur = -1;
    const mk = (label, fn, cls) => h("button", { class: "btn " + (cls || ""), type: "button", onclick: fn }, label);
    const caseBtn = mk("Aa", () => { fCase = !fCase; caseBtn.classList.toggle("primary", fCase); search(); }, "");
    const reBtn = mk(".*", () => { fRegex = !fRegex; reBtn.classList.toggle("primary", fRegex); search(); }, "");
    const findBar = h("div", { id: "efind", hidden: true }, fIn, mk("↑", () => step(true)), mk("↓", () => step(false)), caseBtn, reBtn, rIn,
      mk("Replace", replaceOne), mk("All", replaceAllFn), findCount, mk("✕", () => { findBar.hidden = true; ta.focus(); }, "ghost"));
    const sizeLbl = h("span", { class: "muted", style: { minWidth: "28px", textAlign: "center" } });
    const saveBtn = h("button", { class: "btn primary", type: "button", onclick: save }, icon("save"), h("span", { class: "lb" }, "Save"));
    const wrapBtn = h("button", { class: "btn icon ghost", type: "button", title: "Toggle word wrap", onclick: () => { ta.classList.toggle("wrap"); localStorage.setItem("ark.edwrap", ta.classList.contains("wrap") ? "1" : "0"); } }, icon("wrap"));
    const root = h("div", { id: "editor", role: "dialog", "aria-label": "Editor: " + name },
      h("div", { class: "ebar" },
        h("div", { class: "t" }, name, dot, h("span", { class: "muted", style: { fontWeight: 400, marginLeft: "10px" } }, path)),
        h("button", { class: "btn icon", type: "button", title: "Smaller text", onclick: () => setSize(size() - 1) }, "−"), sizeLbl,
        h("button", { class: "btn icon", type: "button", title: "Larger text", onclick: () => setSize(size() + 1) }, "+"),
        wrapBtn, h("button", { class: "btn icon ghost", type: "button", title: "Find (Ctrl+F)", onclick: toggleFind }, icon("find")),
        saveBtn, h("button", { class: "btn", type: "button", onclick: () => close() }, "Close")),
      findBar, h("div", { class: "ewrap" }, gutter, ta), h("div", { class: "estat" }, pos, info));
    document.body.append(root);

    function setSize(n) { n = Math.max(10, Math.min(28, n)); localStorage.setItem("ark.edsize", String(n)); root.style.setProperty("--ed-size", n + "px"); sizeLbl.textContent = n; }
    setSize(size());

    const dirty = () => ta.value !== saved;
    function sync() {
      const n = ta.value.split("\n").length;
      if (gutter.dataset.n !== String(n)) { gutter.dataset.n = n; let s = ""; for (let i = 1; i <= n; i++) s += i + "\n"; gutter.textContent = s; }
      gutter.scrollTop = ta.scrollTop;
      const lc = EC.lineCol(ta.value, ta.selectionStart);
      pos.textContent = `Ln ${lc.line}, Col ${lc.col}` + (ta.selectionEnd !== ta.selectionStart ? ` (${ta.selectionEnd - ta.selectionStart} selected)` : "");
      dot.hidden = !dirty();
    }
    function apply(ed) {
      if (!ed) return false;
      ta.focus();
      if (ed.insert === "" && ed.from === ed.to) { ta.setSelectionRange(ed.selStart, ed.selEnd); sync(); return true; }
      ta.setSelectionRange(ed.from, ed.to);
      const ok = ed.insert === "" ? document.execCommand("delete") : document.execCommand("insertText", false, ed.insert);
      if (!ok) ta.setRangeText(ed.insert, ed.from, ed.to, "end");
      ta.setSelectionRange(ed.selStart, ed.selEnd);
      sync();
      return true;
    }
    const o = () => ({ lang, unit });
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
            try { const r = await api("/api/text", { method: "PUT", body: { path, content: ta.value, force: true } }); sha = r.sha; saved = ta.value; sync(); toast("Saved"); return true; }
            catch (e2) { toast(e2.message, "bad"); }
          }
        } else toast(e.message, "bad");
        return false;
      } finally { saveBtn.disabled = false; }
    }
    async function close() {
      if (dirty()) {
        const v = await modal({ title: "Unsaved changes", body: h("p", null, `Save changes to ${name}?`),
          buttons: [{ label: "Cancel", value: null }, { label: "Discard", kind: "danger", value: "discard" }, { label: "Save", kind: "primary", value: "save" }] });
        if (!v) return;
        if (v === "save" && !(await save())) return;
      }
      window.removeEventListener("beforeunload", guard);
      root.remove();
    }
    const guard = (e) => { if (dirty()) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", guard);

    // ---- find / replace ----------------------------------------------------
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
      const lc = EC.lineCol(ta.value, m.index);
      ta.scrollTop = Math.max(0, (lc.line - 4) * parseFloat(getComputedStyle(ta).lineHeight));
      findCount.textContent = `${i + 1} / ${matches.length}`; sync();
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
      ta.select(); document.execCommand("insertText", false, r.text); sync(); search(); toast(`Replaced ${r.count}`);
    }
    function toggleFind() { findBar.hidden = !findBar.hidden; if (!findBar.hidden) { const s = ta.value.slice(ta.selectionStart, ta.selectionEnd); if (s && !s.includes("\n")) fIn.value = s; fIn.focus(); fIn.select(); search(); } else ta.focus(); }
    fIn.addEventListener("input", search);
    fIn.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); step(e.shiftKey); } });
    rIn.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); replaceOne(); } });

    // ---- keyboard ----------------------------------------------------------
    ta.addEventListener("keydown", (e) => {
      const s = ta.selectionStart, en = ta.selectionEnd, t = ta.value, mod = e.ctrlKey || e.metaKey;
      let ed = null;
      if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); save(); return; }
      if (mod && e.key.toLowerCase() === "f") { e.preventDefault(); toggleFind(); return; }
      if (e.isComposing) return;
      if (e.key === "Enter" && !mod && !e.shiftKey) ed = EC.enter(t, s, en, o());
      else if (e.key === "Tab" && !mod) ed = EC.tab(t, s, en, e.shiftKey, o());
      else if (e.key === "Backspace" && !mod && !e.altKey) ed = EC.backspace(t, s, en, o());
      else if (mod && e.key === "/") ed = EC.toggleComment(t, s, en, o());
      else if (mod && e.shiftKey && e.key.toLowerCase() === "k") ed = EC.deleteLines(t, s, en);
      else if (mod && e.key.toLowerCase() === "d") ed = EC.duplicateLines(t, s, en, 1);
      else if (e.altKey && e.key === "ArrowUp") ed = EC.moveLines(t, s, en, -1);
      else if (e.altKey && e.key === "ArrowDown") ed = EC.moveLines(t, s, en, 1);
      else if (mod && e.key === "Enter") ed = EC.insertLine(t, s, en, !e.shiftKey, o());
      else if (!mod && !e.altKey && e.key.length === 1) ed = EC.typeChar(t, s, en, e.key, o());
      if (ed) { e.preventDefault(); apply(ed); }
    });
    root.addEventListener("keydown", (e) => { if (e.key === "Escape" && !findBar.contains(document.activeElement)) { e.stopPropagation(); close(); } else if (e.key === "Escape") { findBar.hidden = true; ta.focus(); } });
    ta.addEventListener("input", sync);
    ta.addEventListener("scroll", () => { gutter.scrollTop = ta.scrollTop; });
    ta.addEventListener("keyup", sync); ta.addEventListener("click", sync); ta.addEventListener("select", sync);
    sync();
    ta.focus();
    ta.setSelectionRange(0, 0);
    return true;
  }

  window.Editor = { open, langOf };
})();
