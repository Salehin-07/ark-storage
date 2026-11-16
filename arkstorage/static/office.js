/* Office viewer: Word (.docx), Excel/OpenDocument sheets (.xlsx .xls .ods ...) and PowerPoint (.pptx) drawn in the browser.
 * The libraries (/static/office/*, bundled, no CDN) are only downloaded the first time a file of that kind is opened.
 * Old formats (.doc, .ppt, .odt ...) are converted to PDF by the server and shown by the PDF viewer (see app.js).
 * The pure helpers at the top have no DOM so they can be unit-tested (tests/office_test.js). */
(function (root) {
  "use strict";

  const FAMILY = {
    word: ["docx", "docm"],
    sheet: ["xlsx", "xlsm", "xlsb", "xls", "ods"],
    slides: ["pptx", "pptm", "ppsx"],
    convert: ["doc", "dot", "dotx", "dotm", "rtf", "odt", "ppt", "pps", "odp"],
  };
  const EXT = {};
  Object.keys(FAMILY).forEach((k) => FAMILY[k].forEach((e) => { EXT[e] = k; }));
  const familyOf = (ext) => EXT[String(ext || "").toLowerCase()] || null;
  const extOf = (name) => (String(name).indexOf(".") >= 0 ? String(name).split(".").pop().toLowerCase() : "");

  /** Where a page of `natural` CSS px sits in a host of hostW x hostH at zoom z (1 = fit the width, never enlarged past 100%). */
  function layout(hostW, hostH, natural, z) {
    const fit = natural > 0 ? Math.min(1, hostW / natural) : 1;
    const k = Math.min(4, Math.max(0.2, fit * (z || 1)));
    return { k, width: Math.max(natural, hostW / k), height: hostH / k, percent: Math.round(k * 100) };
  }

  /** "A", "B", ... "Z", "AA" ... for a 0-based column. */
  function colName(c) { let s = ""; for (c += 1; c > 0; c = Math.floor((c - 1) / 26)) s = String.fromCharCode(65 + ((c - 1) % 26)) + s; return s; }

  /** Merged ranges ({s:{r,c},e:{r,c}}) -> lookup of what each cell does: {span:[rows,cols]} for the corner, {skip:true} for covered cells. */
  function mergeMap(merges) {
    const m = new Map();
    (merges || []).forEach((g) => {
      for (let r = g.s.r; r <= g.e.r; r++) for (let c = g.s.c; c <= g.e.c; c++) m.set(r + ":" + c, { skip: true });
      m.set(g.s.r + ":" + g.s.c, { span: [g.e.r - g.s.r + 1, g.e.c - g.s.c + 1] });
    });
    return m;
  }

  /** Remove everything that could run script from rendered document HTML (the iframe sandbox is the second line of defence). */
  function sanitize(node) {
    node.querySelectorAll("script, iframe, frame, object, embed, applet, base, meta, link, form").forEach((n) => n.remove());
    node.querySelectorAll("*").forEach((n) => {
      for (const a of Array.from(n.attributes)) {
        const name = a.name.toLowerCase(), val = a.value.trim().toLowerCase().replace(/[\u0000-\u0020]/g, "");
        if (name.startsWith("on") || (["href", "src", "xlink:href", "action", "formaction"].includes(name) && /^(javascript|vbscript|data:text\/html)/.test(val))) n.removeAttribute(a.name);
      }
      if (n.tagName === "A") { n.setAttribute("target", "_blank"); n.setAttribute("rel", "noopener noreferrer"); }
    });
    return node;
  }

  const api = { familyOf, extOf, layout, colName, mergeMap, sanitize, FAMILY };
  if (typeof document === "undefined") { if (typeof module !== "undefined" && module.exports) module.exports = api; return; }

  // ----------------------------------------------------------------------------------------------- DOM part
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = text; return e; };
  const libs = {};
  function loadScript(src) {
    if (!libs[src]) {
      libs[src] = new Promise((ok, bad) => {
        const s = document.createElement("script"); s.src = src; s.async = true;
        s.onload = ok; s.onerror = () => { delete libs[src]; s.remove(); bad(new Error("Could not load the viewer component")); };
        document.head.append(s);
      });
    }
    return libs[src];
  }
  // In order: docx-preview reads the JSZip global while it loads, so jszip must be running first.
  const lib = (names, ver) => names.reduce((p, n) => p.then(() => loadScript(`/static/office/${n}?v=${ver}`)), Promise.resolve());

  /** Download with progress; resolves an ArrayBuffer. */
  async function fetchBuffer(url, signal, onProgress) {
    const r = await fetch(url, { credentials: "same-origin", signal });
    if (!r.ok) { let m = "HTTP " + r.status; try { m = (await r.json()).error || m; } catch (e) { /* not JSON */ } throw new Error(m); }
    const total = +r.headers.get("Content-Length") || 0;
    if (!r.body || !total) return r.arrayBuffer();
    const rd = r.body.getReader(), out = new Uint8Array(total); let n = 0;
    for (;;) { const { done, value } = await rd.read(); if (done) break; out.set(value, n); n += value.length; onProgress(n, total); }
    return out.buffer;
  }

  function zoomBar(onZoom) {
    const lbl = el("span", "pvzoom muted", "100%"), bar = el("div", "pvbar oxbar");
    const mk = (t, title, fn) => { const b = el("button", "btn icon", t); b.type = "button"; b.title = title; b.setAttribute("aria-label", title); b.onclick = fn; return b; };
    bar.append(mk("−", "Zoom out", () => onZoom("out")), lbl, mk("+", "Zoom in", () => onZoom("in")), mk("↔", "Fit to width", () => onZoom("fit")));
    return { bar, set: (p) => { lbl.textContent = p + "%"; } };
  }

  /** A page-like document (Word, PowerPoint) shown in a script-free sandboxed frame, scaled to fit. */
  function pageView(host, html, natural, z0) {
    let z = z0 || 1;
    const zb = zoomBar((a) => { z = a === "fit" ? 1 : Math.min(8, Math.max(0.25, a === "in" ? z * 1.25 : z / 1.25)); place(); });
    const box = el("div", "oxframe"), f = document.createElement("iframe");
    f.setAttribute("sandbox", "allow-popups allow-popups-to-escape-sandbox");     // no scripts, no same-origin access
    f.setAttribute("referrerpolicy", "no-referrer"); f.title = "Document preview";
    f.srcdoc = html;
    box.append(f); host.append(zb.bar, box);
    function place() {
      const L = layout(box.clientWidth || 600, box.clientHeight || 600, natural, z);
      f.style.width = L.width + "px"; f.style.height = L.height + "px"; f.style.transform = `scale(${L.k})`; zb.set(L.percent);
    }
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(place) : null;
    if (ro) ro.observe(box); place();
    return () => { if (ro) ro.disconnect(); f.srcdoc = ""; };
  }
  const FRAME_CSS = "html,body{margin:0;background:#4b4f56;font-family:system-ui,sans-serif}";

  async function word(host, buf, ctx) {
    await lib(["jszip.min.js", "docx-preview.min.js"], ctx.version);
    const stash = el("div"); stash.style.cssText = "position:absolute;left:-99999px;top:0;width:1400px;visibility:hidden";
    const styles = el("div"); stash.append(styles); document.body.append(stash);
    try {
      const page = el("div"); stash.append(page);
      await window.docx.renderAsync(buf, page, styles, { className: "docx", inWrapper: true, ignoreLastRenderedPageBreak: true, useBase64URL: true, breakPages: true, renderHeaders: true, renderFooters: true, renderFootnotes: true, renderEndnotes: true });
      let natural = 0;
      page.querySelectorAll("section.docx").forEach((s) => { natural = Math.max(natural, s.offsetWidth); });
      natural = (natural || 816) + 60;
      sanitize(stash);
      const html = `<!doctype html><meta charset="utf-8"><base target="_blank"><style>${FRAME_CSS}</style>${stash.innerHTML}`;
      ctx.done();
      return pageView(host, html, natural, 1);
    } finally { stash.remove(); }
  }

  async function slides(host, buf, ctx) {
    await lib(["pptx-preview.umd.js"], ctx.version);
    const W = 960, H = 540;
    const stash = el("div"); stash.style.cssText = `position:absolute;left:-99999px;top:0;width:${W}px;visibility:hidden`;
    document.body.append(stash);
    try {
      const view = el("div"); stash.append(view);
      const p = window.pptxPreview.init(view, { width: W, height: H, mode: "list" });
      await p.preview(buf);
      sanitize(stash);
      const css = `${FRAME_CSS}.ox-slides{display:flex;flex-direction:column;align-items:center;gap:18px;padding:18px 0}.ox-slides>div{background:#fff;box-shadow:0 2px 12px rgba(0,0,0,.45)}`;
      const html = `<!doctype html><meta charset="utf-8"><base target="_blank"><style>${css}</style><div class="ox-slides">${view.innerHTML}</div>`;
      ctx.done();
      return pageView(host, html, W + 40, 1);
    } finally { stash.remove(); }
  }

  const CHUNK = 300, MAX_COLS = 300;
  async function sheet(host, buf, ctx) {
    await lib(["xlsx.full.min.js"], ctx.version);
    const XLSX = window.XLSX;
    const wb = XLSX.read(buf, { type: "array", cellDates: true, cellStyles: false, sheetStubs: false });
    ctx.done();
    const names = wb.SheetNames.filter((n) => wb.Sheets[n]);
    if (!names.length) throw new Error("This workbook has no sheets");
    const wrap = el("div", "oxsheet"), scroll = el("div", "oxscroll"), tabs = el("div", "oxtabs", null);
    wrap.append(scroll, tabs); host.append(wrap);
    let obs = null;
    function show(name) {
      if (obs) obs.disconnect();
      Array.from(tabs.children).forEach((b) => b.classList.toggle("on", b.dataset.name === name));
      const ws = wb.Sheets[name], ref = ws["!ref"];
      scroll.replaceChildren();
      if (!ref) { scroll.append(el("div", "oxempty muted", "This sheet is empty")); return; }
      const rg = XLSX.utils.decode_range(ref), cols = Math.min(rg.e.c, rg.s.c + MAX_COLS - 1), merges = mergeMap(ws["!merges"]);
      const table = el("table", "oxtable"), head = el("thead"), body = el("tbody"), hr = el("tr");
      hr.append(el("th", "corner"));
      for (let c = rg.s.c; c <= cols; c++) hr.append(el("th", "", colName(c)));
      head.append(hr); table.append(head, body); scroll.append(table);
      let next = rg.s.r;
      const more = el("div", "oxmore muted"); scroll.append(more);
      function rows() {
        const end = Math.min(rg.e.r, next + CHUNK - 1), frag = document.createDocumentFragment();
        for (; next <= end; next++) {
          const tr = el("tr"); tr.append(el("th", "", String(next + 1)));
          for (let c = rg.s.c; c <= cols; c++) {
            const m = merges.get(next + ":" + c);
            if (m && m.skip) continue;
            const cell = ws[XLSX.utils.encode_cell({ r: next, c })], td = el("td", cell && (cell.t === "n" || cell.t === "d") ? "num" : "");
            if (cell) td.textContent = cell.w !== undefined ? cell.w : cell.v === undefined || cell.v === null ? "" : String(cell.v);
            if (m && m.span) { if (m.span[0] > 1) td.rowSpan = m.span[0]; if (m.span[1] > 1) td.colSpan = m.span[1]; }
            tr.append(td);
          }
          frag.append(tr);
        }
        body.append(frag);
        const left = rg.e.r - next + 1;
        more.textContent = left > 0 ? `Loading more rows… (${left.toLocaleString()} left)` : rg.e.c > cols ? `Showing the first ${MAX_COLS} columns` : "";
        if (left <= 0 && obs) obs.disconnect();
      }
      rows();
      if (next <= rg.e.r && typeof IntersectionObserver !== "undefined") {
        obs = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) rows(); }, { root: scroll, rootMargin: "600px" });
        obs.observe(more);
      } else while (next <= rg.e.r) rows();
    }
    names.forEach((n) => { const b = el("button", "oxtab", n); b.type = "button"; b.dataset.name = n; b.onclick = () => show(n); tabs.append(b); });
    show(names[0]);
    return () => { if (obs) obs.disconnect(); };
  }

  /** Mount a viewer for `url` in `host`. ctx: { ext, version, ui: {set(frac,text), done()}, onError(err) }. Returns { destroy }. */
  function mount(host, url, ctx) {
    const fam = familyOf(ctx.ext), ctl = new AbortController();
    let stop = null, dead = false;
    const done = () => { if (ctx.ui) ctx.ui.done(); };
    (async () => {
      const buf = await fetchBuffer(url, ctl.signal, (n, t) => { if (ctx.ui) ctx.ui.set(n / t, "Loading… " + Math.round(n / t * 100) + "%"); });
      if (dead) return;
      if (ctx.ui) ctx.ui.set(null, "Opening…");
      const run = fam === "word" ? word : fam === "slides" ? slides : fam === "sheet" ? sheet : null;
      if (!run) throw new Error("Unsupported file type");
      const s = await run(host, buf, { version: ctx.version || "", done });
      if (dead) { if (s) s(); return; }
      stop = s;
    })().catch((e) => { if (dead || e.name === "AbortError") return; done(); if (ctx.onError) ctx.onError(e); });
    return { destroy() { dead = true; ctl.abort(); if (stop) { try { stop(); } catch (e) { /* already gone */ } stop = null; } host.replaceChildren(); } };
  }

  api.mount = mount;
  root.OfficeView = api;
})(typeof window !== "undefined" ? window : globalThis);
