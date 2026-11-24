/* Ark Storage: public share page. Shows a preview of the shared file; the buttons and commands are already in the HTML. */
(function () {
  "use strict";
  const view = document.getElementById("sh-view");
  if (!view) return;
  const { kind, ext, name } = view.dataset, size = Number(view.dataset.size || 0), raw = view.dataset.raw;
  const ver = (document.querySelector("meta[name=ark-version]") || {}).content || "";
  const TEXT_MAX = 512 * 1024;
  const LANG = { sh: "bash", bash: "bash", zsh: "bash", ksh: "bash", py: "python", js: "javascript", mjs: "javascript", cjs: "javascript", ts: "typescript",
    tsx: "typescript", jsx: "javascript", json: "json", css: "css", scss: "scss", html: "xml", htm: "xml", xml: "xml", svg: "xml", vue: "xml", md: "markdown",
    yml: "yaml", yaml: "yaml", sql: "sql", go: "go", rs: "rust", java: "java", kt: "kotlin", c: "c", h: "c", cpp: "cpp", cc: "cpp", hpp: "cpp", cs: "csharp",
    php: "php", rb: "ruby", ini: "ini", toml: "ini", conf: "ini", cfg: "ini", ps1: "powershell", bat: "dos", cmd: "dos", diff: "diff", patch: "diff",
    lua: "lua", pl: "perl", swift: "swift", dockerfile: "dockerfile", makefile: "makefile" };

  const mode = () => document.documentElement.setAttribute("data-mode", window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  mode(); if (window.matchMedia) matchMedia("(prefers-color-scheme: dark)").addEventListener("change", mode);

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
  function fallback(msg) {
    const c = el("div", "sh-card");
    const big = el("div", "big", (ext || "file").slice(0, 4).toUpperCase());
    const dl = el("a", "btn primary", "Download"); dl.href = raw.replace("raw=1", "download=1");
    c.append(big, el("div", "", msg || "No preview for this kind of file."), dl);
    view.replaceChildren(c);
  }

  // copy buttons (command boxes and "Copy link")
  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-copy]"); if (!b) return;
    const old = b.textContent;
    try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Copied"; }
    catch (x) {
      const t = el("textarea"); t.value = b.dataset.copy; document.body.append(t); t.select();
      try { document.execCommand("copy"); b.textContent = "Copied"; } catch (y) { b.textContent = "Press Ctrl+C"; }
      t.remove();
    }
    setTimeout(() => { b.textContent = old; }, 1400);
  });

  async function showText() {
    let res;
    try { res = await fetch(raw, { headers: size > TEXT_MAX ? { Range: "bytes=0-" + (TEXT_MAX - 1) } : {} }); } catch (e) { return fallback("Couldn't load the file."); }
    if (!res.ok && res.status !== 206) return fallback("Couldn't load the file (" + res.status + ").");
    const buf = await res.arrayBuffer();
    const bytes = new Uint8Array(buf);
    if (bytes.subarray(0, 8192).includes(0)) return fallback("This file contains binary data, so it can't be shown as text.");
    const text = new TextDecoder("utf-8").decode(bytes);
    const lang = LANG[ext] || (name.toLowerCase() === "dockerfile" ? "dockerfile" : name.toLowerCase() === "makefile" ? "makefile" : "");
    let html = null;
    if (window.hljs && lang && window.hljs.getLanguage(lang) && text.length < 300000) {
      try { html = window.hljs.highlight(text, { language: lang, ignoreIllegals: true }).value; } catch (e) { html = null; }
    }
    if (html === null) html = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    // one span per line for line numbers; highlight.js spans that cross a newline are closed and re-opened on each line
    const bal = []; const out = []; html.split("\n").forEach((ln) => {
      const prefix = bal.join(""); const re = /<span[^>]*>|<\/span>/g; let m;
      while ((m = re.exec(ln))) { if (m[0] === "</span>") bal.pop(); else bal.push(m[0]); }
      out.push('<span class="ln">' + prefix + ln + "</span>".repeat(bal.length) + "</span>");
    });
    const pre = el("pre", "code"); pre.innerHTML = out.join("\n");
    const box = el("div", "sh-code"); box.append(pre);
    view.replaceChildren(box);
    if (size > TEXT_MAX) view.append(el("div", "sh-trunc", "Showing the first " + Math.round(TEXT_MAX / 1024) + " KB. Use Raw or Download for the whole file."));
  }

  function showImage() {
    const img = el("img"); img.alt = name; img.src = raw; img.onerror = () => fallback("Couldn't load the image.");
    const box = el("div", "sh-img"); box.append(img); view.replaceChildren(box);
  }
  function showMedia() {
    const m = el(kind === "video" ? "video" : "audio"); m.controls = true; m.preload = "metadata"; m.src = raw; m.setAttribute("playsinline", "");
    m.onerror = () => fallback("This browser can't play this file.");
    const box = el("div", "sh-media"); box.append(m); view.replaceChildren(box);
  }
  function showPdf(url) {
    const host = el("div", "pvhost"); view.replaceChildren(host);
    window.PdfView.mount(host, url, { version: ver, onError: () => fallback("Couldn't open this PDF.") });
  }
  function showOffice() {
    const fam = window.OfficeView.familyOf(ext);
    if (fam === "convert") {
      if (view.dataset.convert !== "1") return fallback("Previewing ." + ext + " files needs LibreOffice on the server. You can download the file instead.");
      return showPdf(raw.replace("raw=1", "pdf=1"));
    }
    const host = el("div", "pvhost"), status = el("div", "sh-loading"); status.append(el("span", "vspin"), el("span", "", "Loading…"));
    view.replaceChildren(host); host.append(status);
    const ui = { set(f, t) { status.lastChild.textContent = t || "Loading…"; }, done() { status.remove(); } };
    window.OfficeView.mount(host, raw, { ext, version: ver, ui, onError: (e) => fallback(e && e.message ? "Can't open this file: " + e.message : null) });
  }

  try {
    if (kind === "image") showImage();
    else if (kind === "video" || kind === "audio") showMedia();
    else if (kind === "pdf") showPdf(raw);
    else if (kind === "office" && window.OfficeView) showOffice();
    else if (kind === "text") showText();
    else if (kind === "file" && size <= TEXT_MAX) showText();       // no extension: try it as text, binary data falls back to Download
    else fallback();
  } catch (e) { fallback(); }
})();
