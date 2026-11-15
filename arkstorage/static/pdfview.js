/* Ark Storage: PDF viewer.
 *
 * The browser's own PDF viewer is not an option here: Chrome refuses to show it inside our frame ("This content is
 * blocked"), and Android browsers have no inline PDF viewer at all. So PDFs are drawn with PDF.js (bundled, no CDN).
 *
 * Big files: nothing waits for the whole file. PDF.js fetches byte ranges (the server answers them straight from the
 * pieces it holds), so the first page appears as soon as its bytes arrive; pages are then drawn lazily, the one
 * the reader is looking at first, then the neighbours. A progress bar shows what has been fetched. The pure helpers
 * at the top have no DOM so they can be unit-tested (tests/pdfview_test.js). */
(function (root) {
  "use strict";

  /** Scale that makes a page `pageW` wide fill `boxW` (minus padding), times the user's zoom. */
  function fitScale(boxW, pageW, zoom, pad) {
    const w = Math.max(80, boxW - (pad === undefined ? 24 : pad));
    return (w / Math.max(1, pageW)) * (zoom || 1);
  }

  /** Which page to draw next. `visible` are page numbers intersecting the viewport, `focus` the one nearest the
   *  middle of the screen, `done` the pages already drawn, `count` the page total. Visible pages come first, nearest
   *  to the focus first; when everything visible is drawn, the page just below, then just above, is warmed up. */
  function pickNext(visible, focus, done, count) {
    const todo = visible.filter((n) => !done.has(n)).sort((a, b) => Math.abs(a - focus) - Math.abs(b - focus) || a - b);
    if (todo.length) return todo[0];
    const top = visible.length ? Math.max.apply(null, visible) : focus;
    const low = visible.length ? Math.min.apply(null, visible) : focus;
    for (const n of [top + 1, low - 1]) if (n >= 1 && n <= count && !done.has(n)) return n;
    return 0;
  }

  /** Pages far from the viewport give their canvas back, so a 2000-page file doesn't fill the phone's memory. */
  function shouldRelease(n, focus, keep) { return Math.abs(n - focus) > keep; }

  const fmt = (b) => (b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB");

  let libP = null;
  function loadLib(ver) {
    if (!libP) {
      libP = import("/static/pdfjs/pdf.min.mjs?v=" + ver).then((m) => {
        m.GlobalWorkerOptions.workerSrc = "/static/pdfjs/pdf.worker.min.mjs?v=" + ver;
        return m;
      });
      libP.catch(() => { libP = null; });
    }
    return libP;
  }

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }

  /** Mount a viewer for `url` inside `host`. Returns { destroy }. `opts`: { version, onError(err) }. */
  function mount(host, url, opts) {
    opts = opts || {};
    const bar = el("div", "pvbar"), pageInfo = el("span", "pvinfo", "…");
    const mk = (label, title, fn) => { const b = el("button", "btn icon", label); b.type = "button"; b.title = title; b.setAttribute("aria-label", title); b.onclick = fn; return b; };
    const zoomLbl = el("span", "pvzoom muted", "100%");
    bar.append(pageInfo, mk("−", "Zoom out", () => setZoom(zoom / 1.25)), zoomLbl, mk("+", "Zoom in", () => setZoom(zoom * 1.25)), mk("↔", "Fit to width", () => setZoom(1)));
    const prog = el("div", "pvprog"), progFill = el("i"); prog.append(progFill);
    const status = el("div", "pvstatus"), statusText = el("span", "", "Opening…"); status.append(el("span", "vspin"), statusText);
    const scroller = el("div", "pvscroll"), pagesBox = el("div", "pvpages");
    scroller.append(pagesBox);
    host.replaceChildren(bar, prog, scroller, status);

    let pdf = null, task = null, dead = false, zoom = 1, count = 0, base = null;
    const pages = [], done = new Set(), visible = new Set(), rendering = { n: 0, task: null };
    let focus = 1, io = null, raf = 0, pumping = false;

    function progress(loaded, total) {
      if (dead) return;
      const f = total ? Math.min(1, loaded / total) : 0;
      progFill.style.width = (f * 100).toFixed(1) + "%";
      prog.hidden = f >= 1;
      if (!pdf) statusText.textContent = total ? "Loading… " + fmt(loaded) + " of " + fmt(total) : "Loading…";
    }

    function fail(e) {
      if (dead) return;
      status.hidden = false; status.className = "pvstatus bad"; status.replaceChildren(el("span", "", (e && e.message) || "This PDF can't be shown."));
      if (opts.onError) opts.onError(e);
    }

    function scaleNow() { return fitScale(scroller.clientWidth, base.width, zoom); }

    function layout() {
      const s = scaleNow();
      for (let i = 1; i <= count; i++) {
        const p = pages[i];
        const vw = (p.w || base.width) * s, vh = (p.h || base.height) * s;
        p.box.style.width = Math.floor(vw) + "px"; p.box.style.height = Math.floor(vh) + "px";
      }
    }

    function release(n) {
      const p = pages[n];
      if (!p || !done.has(n)) return;
      done.delete(n);
      p.box.querySelector("canvas") && p.box.querySelector("canvas").remove();
      p.box.classList.remove("ready"); p.box.classList.add("loading");
      if (p.page) { try { p.page.cleanup(); } catch (e) { /* already gone */ } }
    }

    async function draw(n) {
      const p = pages[n];
      rendering.n = n;
      try {
        const page = p.page || (p.page = await pdf.getPage(n));
        if (dead) return;
        const s = scaleNow();
        const v1 = page.getViewport({ scale: 1 });
        p.w = v1.width; p.h = v1.height;
        const vp = page.getViewport({ scale: s });
        let ratio = Math.min(root.devicePixelRatio || 1, 2.5);
        const maxPx = 16e6;                                        // keep canvases within what phones can allocate
        if (vp.width * vp.height * ratio * ratio > maxPx) ratio = Math.sqrt(maxPx / (vp.width * vp.height));
        const c = el("canvas");
        c.width = Math.floor(vp.width * ratio); c.height = Math.floor(vp.height * ratio);
        c.style.width = Math.floor(vp.width) + "px"; c.style.height = Math.floor(vp.height) + "px";
        p.box.style.width = c.style.width; p.box.style.height = c.style.height;
        const t = page.render({ canvasContext: c.getContext("2d"), viewport: vp, transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : null });
        rendering.task = t;
        await t.promise;
        if (dead) return;
        const old = p.box.querySelector("canvas"); if (old) old.remove();
        p.box.append(c); p.box.classList.remove("loading"); p.box.classList.add("ready");
        done.add(n);
      } catch (e) {
        if (e && e.name === "RenderingCancelledException") return;
        p.box.classList.remove("loading"); p.box.classList.add("bad"); p.box.textContent = "Page " + n + " can't be shown";
        done.add(n);
      } finally { rendering.n = 0; rendering.task = null; }
    }

    async function pump() {                                      // one page at a time, most wanted first
      if (pumping || dead || !pdf) return;
      pumping = true;
      try {
        for (;;) {
          const n = pickNext([...visible], focus, done, count);
          if (!n || dead) break;
          await draw(n);
          for (const d of [...done]) if (shouldRelease(d, focus, 6)) release(d);
        }
      } finally { pumping = false; }
    }

    function updateFocus() {
      raf = 0;
      if (!pdf) return;
      const mid = scroller.getBoundingClientRect().top + scroller.clientHeight * 0.35;
      let best = focus, bestD = Infinity;
      for (const n of visible) {
        const r = pages[n].box.getBoundingClientRect();
        const d = mid < r.top ? r.top - mid : mid > r.bottom ? mid - r.bottom : 0;
        if (d < bestD) { bestD = d; best = n; }
      }
      if (best !== focus) { focus = best; pump(); }
      pageInfo.textContent = focus + " / " + count;
    }

    function setZoom(z) {
      if (!pdf) return;
      const nz = Math.max(0.4, Math.min(5, z));
      if (nz === zoom) return;
      const p = pages[focus], frac = p ? (scroller.scrollTop - p.box.offsetTop) / Math.max(1, p.box.offsetHeight) : 0;
      zoom = nz; zoomLbl.textContent = Math.round(zoom * 100) + "%";
      if (rendering.task) { try { rendering.task.cancel(); } catch (e) { /* done */ } }
      for (const n of [...done]) release(n);
      layout();
      if (p) scroller.scrollTop = p.box.offsetTop + frac * p.box.offsetHeight;
      pump();
    }

    async function open() {
      let lib;
      try { lib = await loadLib(opts.version || ""); } catch (e) { return fail(new Error("The PDF viewer could not be loaded")); }
      if (dead) return;
      task = lib.getDocument({
        url, withCredentials: true, isEvalSupported: false,
        rangeChunkSize: 1 << 20,        // 1 MB per request: fewer round trips than the 64 KB default, still far less than the file
        disableAutoFetch: true,         // fetch only what is on screen (or about to be), not the entire file in the background
        disableStream: false,
      });
      task.onProgress = (p) => progress(p.loaded, p.total);
      task.onPassword = (update, reason) => {
        const pw = root.prompt(reason === 2 ? "Wrong password. Enter the PDF password:" : "This PDF is password protected. Enter the password:");
        if (pw === null) fail(new Error("This PDF needs its password")); else update(pw);
      };
      try {
        pdf = await task.promise;
        if (dead) return;
        count = pdf.numPages;
        const first = await pdf.getPage(1);
        base = first.getViewport({ scale: 1 });
        for (let i = 1; i <= count; i++) {
          const box = el("div", "pvpage loading"); box.dataset.n = i;
          box.append(el("span", "vspin"));
          pages[i] = { box, page: i === 1 ? first : null, w: i === 1 ? base.width : 0, h: i === 1 ? base.height : 0 };
          pagesBox.append(box);
        }
        status.hidden = true;
        layout();
        io = new root.IntersectionObserver((ents) => {
          for (const e of ents) { const n = +e.target.dataset.n; if (e.isIntersecting) visible.add(n); else visible.delete(n); }
          if (!raf) raf = root.requestAnimationFrame(updateFocus);
          pump();
        }, { root: scroller, rootMargin: "30% 0px" });
        for (let i = 1; i <= count; i++) io.observe(pages[i].box);
        visible.add(1);                                        // page 1 is wanted before the observer has reported anything
        pageInfo.textContent = "1 / " + count;
        pump();
        scroller.addEventListener("scroll", () => { if (!raf) raf = root.requestAnimationFrame(updateFocus); }, { passive: true });
        root.addEventListener("resize", onResize);
      } catch (e) {
        if (dead) return;
        fail(e && e.name === "PasswordException" ? new Error("This PDF needs its password") : e && e.name === "InvalidPDFException" ? new Error("This file isn't a valid PDF") : e);
      }
    }
    let rt = 0;
    function onResize() { clearTimeout(rt); rt = setTimeout(() => { if (!pdf || dead) return; for (const n of [...done]) release(n); layout(); pump(); }, 150); }

    open();
    return {
      destroy() {
        dead = true;
        if (io) io.disconnect();
        if (raf) root.cancelAnimationFrame(raf);
        root.removeEventListener("resize", onResize);
        if (rendering.task) { try { rendering.task.cancel(); } catch (e) { /* done */ } }
        if (task) { try { task.destroy(); } catch (e) { /* done */ } }
        host.replaceChildren();
      },
    };
  }

  const api = { mount, fitScale, pickNext, shouldRelease };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.PdfView = api;
})(typeof window !== "undefined" ? window : globalThis);
