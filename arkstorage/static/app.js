/* Ark Storage: file browser. Selection logic lives in files-core.js; dialogs/menus in ui.js. */
(function () {
  "use strict";
  const { h, icon, fmtBytes, fmtDate, toast, modal, ask, prompt, choose, popMenu, closeMenu, api, S, enc } = window.UI;
  const FC = window.FilesCore, PR = window.ArkProgress;

  // Start the two requests every page load needs right away and in parallel, instead of session first and
  // the folder listing only after it. (A signed-out visitor just gets a 401 for the listing, which is dropped.)
  const hashPath = () => { const x = decodeURIComponent(location.hash.slice(1)); return x === "!trash" ? null : x.startsWith("/") ? x : "/"; };
  const quick = (url) => fetch(url, { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  const pre = { path: hashPath(), session: quick("/api/session"), list: null };
  if (pre.path) pre.list = quick("/api/list?path=" + enc(pre.path));

  const st = {
    path: "/", entries: [], sel: [], anchor: null, view: localStorage.getItem("ark.view") || "list",
    sort: JSON.parse(localStorage.getItem("ark.sort") || '{"key":"name","dir":1}'), clip: null, mode: "files", // files | trash | search
    query: "", status: null, loadErr: "", head: "", trash: [], trashLoaded: false, jobs: [], xferOpen: true, dismissed: {}, user: "",
    pending: 0, flash: null,
  };
  const $ = (s, r) => (r || document).querySelector(s);
  const app = $("#app");
  let el = {};

  // ------------------------------------------------------------ theme
  function applyTheme(pref) {
    pref = localStorage.getItem("ark.theme") || pref || "auto";
    const dark = pref === "dark" || (pref === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.mode = dark ? "dark" : "light";
    document.querySelector('meta[name="theme-color"]').content = dark ? "#0f1115" : "#ffffff";
  }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme());

  // ------------------------------------------------------------ boot / login
  async function boot() {
    let s = null;
    try { s = pre.session ? await pre.session : null; } catch (e) { s = null; }
    pre.session = null;
    if (!s || !("csrf" in s)) { try { s = await api("/api/session"); } catch (e) { document.body.textContent = e.message; return; } }
    S.csrf = s.csrf;
    applyTheme(s.theme);
    if (s.authed) { st.user = s.user; start(s); } else showLogin();
  }
  function showLogin() {
    app.hidden = true; $("#login").hidden = false;
    const f = $("#login-form"), err = $("#login-err");
    if (!f.dataset.bound) {
      f.dataset.bound = "1";
      f.addEventListener("submit", async (e) => {
        e.preventDefault(); err.textContent = "";
        try {
          const r = await api("/api/login", { method: "POST", body: { username: $("#lu").value, password: $("#lp").value } });
          S.csrf = r.csrf; st.user = r.user; $("#lp").value = "";
          start({ user: r.user, default_password: r.default_password });
        } catch (ex) { err.textContent = ex.message; }
      });
    }
    setTimeout(() => $("#lu").focus(), 50);
  }
  window.Ark = { onLogout: () => { if (!$("#login").hidden) return; app.hidden = true; showLogin(); } };

  function start(sess) {
    $("#login").hidden = true; app.hidden = false;
    build();
    st.defaultPw = !!sess.default_password;
    loadStatus().then(() => { if (st.status && st.status.problems && st.status.problems.length) openSetup(); });
    route();
  }

  // ------------------------------------------------------------ layout
  function btn(label, ic, fn, cls, title) {
    return h("button", { class: "btn " + (cls || ""), type: "button", title: title || label, "aria-label": title || label, onclick: fn }, ic ? icon(ic) : null, label ? h("span", { class: "lb" }, label) : null);
  }
  function build() {
    el = {};
    el.crumbs = h("div", { id: "crumbs", role: "navigation", "aria-label": "Path" });
    el.search = h("input", { class: "inp", type: "search", placeholder: "Search files…  ( / )", "aria-label": "Search", oninput: debounce(onSearch, 180) });
    el.viewSeg = h("div", { class: "seg", role: "group" });
    el.paste = btn("Paste", "paste", doPaste, "", "Paste here"); el.paste.hidden = true;
    el.bar = h("div", { id: "bar" }, el.crumbs, el.paste, el.viewSeg, btn("New", "plus", (e) => newMenu(e.currentTarget), "primary", "Create or upload"));
    el.banner = h("div", { id: "banner", hidden: true });
    el.selBar = h("div", { id: "sel-bar", hidden: true });
    el.list = h("div", { id: "list" });
    el.wrap = h("div", { id: "listwrap" }, el.list);
    el.statusBar = h("div", { id: "status" });
    el.fileIn = h("input", { type: "file", multiple: true, hidden: true, onchange: () => { uploadFiles([...el.fileIn.files].map((f) => ({ file: f, rel: f.name }))); el.fileIn.value = ""; } });
    el.dirIn = h("input", { type: "file", webkitdirectory: true, directory: true, multiple: true, hidden: true, onchange: () => { uploadFiles([...el.dirIn.files].map((f) => ({ file: f, rel: f.webkitRelativePath || f.name }))); el.dirIn.value = ""; } });
    el.xfer = h("div", { id: "xfer", hidden: true });
    el.nav = {
      files: h("button", { class: "nav on", type: "button", onclick: () => { closeSide(); go("/"); } }, icon("home"), "My files"),
      trash: h("button", { class: "nav", type: "button", onclick: () => { closeSide(); location.hash = "#!trash"; } }, icon("trash"), "Trash"),
      settings: h("button", { class: "nav", type: "button", onclick: () => { closeSide(); openSettings(); } }, icon("settings"), "Settings"),
    };
    el.usage = h("div", { class: "usage" });
    el.side = h("aside", { id: "side" }, el.nav.files, el.nav.trash, el.nav.settings, el.usage,
      h("button", { class: "nav", type: "button", onclick: logout }, icon("logout"), "Sign out"));
    const top = h("header", { id: "top" },
      h("button", { class: "btn icon ghost", id: "menu-btn", style: { display: "none" }, type: "button", "aria-label": "Menu", onclick: () => el.side.classList.toggle("open") }, icon("menu")),
      h("div", { id: "search" }, icon("search"), el.search),
      btn("", "up", () => { el.xferOpen = true; st.xferOpen = true; renderXfer(); }, "icon ghost hide-m", "Transfers"),
      btn("", "sun", toggleTheme, "icon ghost", "Toggle theme"));
    el.fab = h("button", { id: "fab", type: "button", "aria-label": "New", onclick: (e) => newMenu(e.currentTarget) }, icon("plus"));
    el.sync = h("div", { id: "sync", "aria-hidden": "true" });
    el.main = h("main", { id: "main" }, el.sync, el.bar, el.banner, el.selBar, el.wrap, el.statusBar);
    app.replaceChildren(top, el.side, el.main, el.xfer, el.fab, el.fileIn, el.dirIn);
    renderViewSeg();
    bindList();
    bindDrop();
    document.addEventListener("keydown", onKey);
    window.addEventListener("hashchange", route);
  }
  const closeSide = () => el.side && el.side.classList.remove("open");
  function toggleTheme() {
    const dark = document.documentElement.dataset.mode === "dark";
    localStorage.setItem("ark.theme", dark ? "light" : "dark"); applyTheme();
  }
  function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

  function renderViewSeg() {
    el.viewSeg.replaceChildren(
      h("button", { type: "button", class: st.view === "list" ? "on" : "", "aria-label": "List view", title: "List view", onclick: () => setView("list") }, icon("list")),
      h("button", { type: "button", class: st.view === "grid" ? "on" : "", "aria-label": "Grid view", title: "Grid view", onclick: () => setView("grid") }, icon("grid")));
  }
  function setView(v) { st.view = v; localStorage.setItem("ark.view", v); renderViewSeg(); render(); }

  // ------------------------------------------------------------ routing & loading
  function route() {
    const hash = decodeURIComponent(location.hash.slice(1));
    el.search.value = ""; st.query = "";
    if (hash === "!trash") return showTrash();
    st.mode = "files";
    load(hash.startsWith("/") ? hash : "/");
  }
  function go(path) { const want = "#" + encodeURI(path).replace(/#/g, "%23"); if (location.hash === want || (path === "/" && !location.hash)) route(); else location.hash = want; }

  // Folder listings are cached for the session and always revalidated: opening a folder you've seen is instant,
  // and the screen only redraws if GitHub says something changed. Hovering a folder warms its cache.
  const dirCache = new Map(), inflight = new Set();
  let gen = 0, loadSeq = 0, idleWaiters = [];
  const cacheDir = (path, d) => { dirCache.delete(path); dirCache.set(path, d); if (dirCache.size > 40) dirCache.delete(dirCache.keys().next().value); };
  const invalidate = () => { gen++; dirCache.clear(); };
  function takePre(path) { const q = pre.list && pre.path === path ? pre.list : null; pre.list = null; return q; }
  function prefetchDir(path) {
    if (dirCache.has(path) || inflight.has(path)) return;
    inflight.add(path); const g = gen;
    api("/api/list?path=" + enc(path)).then((d) => { if (g === gen) cacheDir(path, d); }).catch(() => {}).finally(() => inflight.delete(path));
  }
  function applyDir(d) {
    st.entries = d.entries; st.crumbs = d.crumbs; st.head = d.head; st.loadErr = "";
    st.sel = FC.pruneSelection(st.entries.map((e) => e.path), st.sel);
  }
  async function load(path, quiet) {
    const seq = ++loadSeq, cached = dirCache.get(path), first = takePre(path);
    st.path = path; st.mode = "files";
    let showed = false, timer = 0;
    if (!quiet) {
      if (cached) { applyDir(cached); render(); showed = true; }
      else timer = setTimeout(() => { if (seq === loadSeq) el.list.replaceChildren(h("div", { class: "empty" }, "Loading…")); }, 140);   // no flash for fast loads
    }
    try {
      const d = (first && (await first)) || (await api("/api/list?path=" + enc(path)));
      clearTimeout(timer);
      if (seq !== loadSeq) return;
      if (quiet && st.pending) return;                 // an optimistic change is on screen; the final reload reconciles it
      cacheDir(path, d);
      if (showed && cached.head === d.head) return;    // nothing changed: no redraw
      applyDir(d);
    } catch (e) {
      clearTimeout(timer);
      if (seq !== loadSeq) return;
      if (showed) { toast("Couldn't refresh: " + e.message, "bad"); return; }
      st.loadErr = e.message; st.entries = []; st.crumbs = [{ name: "Home", path: "/" }];
      if (e.status === 404 && path !== "/") { toast("That folder no longer exists", "bad"); return go("/"); }
    }
    render();
  }
  const reload = () => (st.mode === "trash" ? showTrash() : load(st.path, true));

  async function loadStatus() {
    try { st.status = await api("/api/status"); } catch (e) { st.status = null; }
    renderUsage(); renderBanner();
  }

  function renderUsage() {
    const s = st.status;
    if (!s) { el.usage.replaceChildren(); return; }
    const kb = s.repo_kb || 0, pct = Math.min(100, (kb / (5 * 1024 * 1024)) * 100);
    el.usage.replaceChildren(
      h("div", { class: "muted" }, "Storage"), h("b", null, s.stats ? fmtBytes(s.stats.bytes) : "—"),
      h("div", { class: "meter" }, h("i", { style: { width: pct.toFixed(1) + "%" } })),
      h("div", { class: "muted" }, s.stats ? `${s.stats.files} files · repo ${fmtBytes(kb * 1024)}` : "Not connected"),
      s.private !== undefined ? h("div", { style: { marginTop: "6px" } }, h("span", { class: "badge " + (s.private ? "ok" : "warn") }, s.private ? "private repo" : "public repo"), " ", s.crypto && s.crypto.enabled ? h("span", { class: "badge ok" }, "encrypted") : null) : null);
    el.nav.trash.replaceChildren(icon("trash"), "Trash", s.stats && s.stats.trash_items ? h("span", { class: "badge", style: { marginLeft: "auto" } }, String(s.stats.trash_items)) : "");
  }
  function renderBanner() {
    const s = st.status, msgs = [];
    if (st.defaultPw && !st.dismissed.pw) msgs.push({ k: "pw", t: "You are still using the default password (ark / ark).", a: "Change it", f: () => openSettings("acct") });
    if (s && s.problems && s.problems.length) msgs.push({ k: "setup", bad: true, t: "Setup needed: " + s.problems[0], a: "Set up now", f: () => openSetup() });
    else if (s && s.error) msgs.push({ k: "err", bad: true, t: s.error, a: "Settings", f: () => openSettings("repo") });
    else if (s && s.private === false && !(s.crypto && s.crypto.enabled) && !st.dismissed.pub) msgs.push({ k: "pub", t: "This repository is public, so plain files stored here are readable by anyone.", a: "Encrypt", f: () => openSettings("enc") });
    if (s && s.git === false) msgs.push({ k: "git", bad: true, t: "git is not installed on the server; uploads won't work." });
    const m = msgs[0];
    el.banner.hidden = !m;
    if (!m) return;
    el.banner.className = m.bad ? "bad" : "";
    el.banner.id = "banner";
    el.banner.replaceChildren(icon("info"), h("span", { style: { flex: 1 } }, m.t), m.a ? h("button", { class: "btn", type: "button", onclick: m.f }, m.a) : null,
      m.bad ? null : h("button", { class: "btn ghost icon", type: "button", "aria-label": "Dismiss", onclick: () => { st.dismissed[m.k] = true; renderBanner(); } }, icon("x")));
  }

  // ------------------------------------------------------------ rendering
  const KIND_ICON = { dir: "folder", image: "image", video: "video", audio: "audio", pdf: "pdf", archive: "archive", text: "code", binary: "file", file: "file" };
  function sorted(list) {
    const { key, dir } = st.sort;
    const k = (e) => key === "size" ? e.size : key === "mtime" ? e.mtime : key === "kind" ? (e.name.split(".").pop() || "") : e.name.toLowerCase();
    return list.slice().sort((a, b) => {
      if (a.dir !== b.dir) return a.dir ? -1 : 1;
      const x = k(a), y = k(b);
      const c = typeof x === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true });
      return (c || a.name.localeCompare(b.name)) * dir;
    });
  }
  function order() { return st.view === "x" ? [] : (st.shown || []).map((e) => e.path); }

  function renderCrumbs() {
    if (st.mode === "trash") { el.crumbs.replaceChildren(h("button", { type: "button", onclick: () => go("/") }, "Home"), h("span", { class: "sep" }, "›"), h("button", { type: "button" }, "Trash")); return; }
    if (st.mode === "search") { el.crumbs.replaceChildren(h("button", { type: "button" }, `Search: “${st.query}”`)); return; }
    const out = [];
    (st.crumbs || []).forEach((c, i) => {
      if (i) out.push(h("span", { class: "sep" }, "›"));
      const b = h("button", { type: "button", onclick: () => go(c.path) }, c.name);
      b.dataset.drop = c.path; out.push(b);
    });
    el.crumbs.replaceChildren(...out);
    el.crumbs.scrollLeft = el.crumbs.scrollWidth;
  }

  function render() {
    renderCrumbs();
    el.nav.files.classList.toggle("on", st.mode !== "trash"); el.nav.trash.classList.toggle("on", st.mode === "trash");
    el.paste.hidden = !st.clip || st.mode !== "files";
    el.viewSeg.hidden = st.mode === "trash";
    const shown = sorted(st.entries);
    st.shown = shown;
    el.list.className = st.view === "grid" ? "grid" : "";
    el.list.classList.toggle("sel-mode", st.sel.length > 0);
    const kids = [];
    if (st.loadErr) {
      kids.push(h("div", { class: "empty" }, icon("info"), h("div", null, st.loadErr), btn("Retry", "refresh", () => load(st.path))));
    } else if (!shown.length) {
      kids.push(h("div", { class: "empty" }, icon(st.mode === "search" ? "search" : "folder"), h("div", null, st.mode === "search" ? "Nothing matches your search." : "This folder is empty."),
        st.mode === "files" ? h("div", { class: "hint" }, "Drop files here or use the New button.") : null));
    } else {
      if (st.view === "list") {
        const all = h("input", { type: "checkbox", class: "chk" + (st.sel.length ? " always" : ""), "aria-label": "Select all", checked: st.sel.length === shown.length, onclick: (e) => { e.stopPropagation(); st.sel = e.target.checked ? shown.map((x) => x.path) : []; paint(); } });
        const hb = (key, label) => h("button", { type: "button", class: key === "mtime" ? "c-md" : "", onclick: () => { st.sort = { key, dir: st.sort.key === key ? -st.sort.dir : 1 }; localStorage.setItem("ark.sort", JSON.stringify(st.sort)); render(); } }, label, st.sort.key === key ? h("span", null, st.sort.dir > 0 ? "▲" : "▼") : null);
        kids.push(h("div", { class: "head" }, all, hb("name", "Name"), hb("size", "Size"), hb("mtime", "Modified")));
      }
      shown.forEach((e) => kids.push(rowEl(e)));
    }
    el.list.replaceChildren(...kids);
    paint();
    if (st.flash) {                                    // draw the eye to the item that was just created
      const r = [...el.list.querySelectorAll(".row")].find((x) => x.dataset.path === st.flash);
      if (r) { st.flash = null; r.classList.add("flash"); r.scrollIntoView({ block: "nearest" }); }
    }
  }

  function rowEl(e) {
    const tip = e.enc ? icon("lock", "lock") : null;
    let ic;
    if (st.view === "grid" && e.kind === "image" && e.size < 3e6) {
      ic = h("div", { class: "ic image" }, h("img", { loading: "lazy", alt: "", src: "/api/raw?path=" + enc(e.path), onerror: (ev) => ev.target.replaceWith(icon("image")) }));
    } else ic = h("div", { class: "ic " + e.kind }, icon(KIND_ICON[e.kind] || "file"));
    const r = h("div", { class: "row" + (e.pending ? " pending" : ""), role: "row", tabindex: 0, draggable: st.mode !== "search" && !e.pending, "aria-label": e.name },
      h("input", { type: "checkbox", class: "chk", tabindex: -1, "aria-label": "Select " + e.name }),
      h("div", { class: "nm" }, ic, h("span", { class: "t", title: e.path }, e.name), tip, e.pending ? icon("refresh", "spin") : null, st.mode === "search" ? h("span", { class: "muted", style: { fontSize: "12px" } }, e.path) : null),
      h("div", { class: "sz" }, e.dir ? (e.items != null ? e.items + (e.items === 1 ? " item" : " items") : "") : fmtBytes(e.size)),
      h("div", { class: "md" }, fmtDate(e.mtime)));
    r.dataset.path = e.path;
    if (e.dir) r.dataset.dir = "1";
    if (e.pending) r.dataset.pending = "1";
    return r;
  }

  function paint() {
    const set = new Set(st.sel);
    el.list.classList.toggle("sel-mode", st.sel.length > 0);
    const cut = st.clip && st.clip.cut ? new Set(st.clip.paths) : new Set();
    el.list.querySelectorAll(".row").forEach((r) => {
      const on = set.has(r.dataset.path);
      r.classList.toggle("sel", on); r.classList.toggle("cut", cut.has(r.dataset.path));
      const c = r.querySelector(".chk"); if (c) c.checked = on; r.setAttribute("aria-selected", on);
    });
    const all = el.list.querySelector(".head .chk"); if (all) { all.checked = st.sel.length && st.sel.length === (st.shown || []).length; all.classList.toggle("always", st.sel.length > 0); }
    renderSelBar();
    renderStatus();
  }
  function renderStatus() {
    const files = st.entries.filter((e) => !e.dir).length, dirs = st.entries.length - files;
    el.statusBar.replaceChildren(h("span", null, st.mode === "trash" ? `${st.trash.length} in trash` : `${dirs} folder${dirs === 1 ? "" : "s"}, ${files} file${files === 1 ? "" : "s"}`),
      st.sel.length ? h("span", null, `${st.sel.length} selected`) : null, st.pending ? h("span", { class: "saving" }, "Saving to GitHub…") : null,
      h("span", { class: "spacer" }), h("span", null, st.head ? "commit " + st.head : ""));
  }
  const syncUI = () => { el.sync.classList.toggle("on", st.pending > 0); renderStatus(); };

  function selEntries() { const m = new Map(st.entries.map((e) => [e.path, e])); return st.sel.map((p) => m.get(p)).filter(Boolean); }

  function renderSelBar() {
    const es = selEntries();
    el.selBar.hidden = !es.length || st.mode === "trash";
    if (el.selBar.hidden) return;
    const one = es.length === 1 ? es[0] : null;
    el.selBar.replaceChildren(
      h("span", { class: "cnt" }, `${es.length} selected`),
      btn("Download", "download", () => downloadSel(es)), one && !one.dir ? btn("Share", "link", () => share(one)) : null,
      one ? btn("Rename", "edit", () => rename(one)) : null,
      btn("Copy", "copy", () => setClip(false)), btn("Cut", "cut", () => setClip(true)),
      btn("Compress", "zip", () => compress(es)), one && /\.zip$/i.test(one.name) ? btn("Extract", "archive", () => extract(one)) : null,
      one ? btn("Properties", "info", () => props(one)) : null,
      btn("Delete", "trash", () => del(es), "danger", trashOn() ? "Move to trash" : "Delete"),
      h("span", { class: "spacer" }), btn("Select all", "select", () => { st.sel = st.shown.map((e) => e.path); paint(); }, "ghost"),
      btn("", "x", () => { st.sel = []; paint(); }, "icon ghost", "Clear selection"));
  }

  // ------------------------------------------------------------ interaction
  let lastPointer = "mouse", pressTimer = null, pressed = false;
  function bindList() {
    const L = el.list;
    L.addEventListener("pointerdown", (e) => {
      lastPointer = e.pointerType || "mouse";
      const row = e.target.closest(".row");
      if (row && row.dataset.dir && !row.dataset.pending && st.mode === "files") prefetchDir(row.dataset.path);   // the click follows ~100 ms later
      if (lastPointer === "touch" && row && !e.target.closest(".chk")) {
        pressed = false;
        const sx = e.clientX, sy = e.clientY;
        pressTimer = setTimeout(() => { pressed = true; if (navigator.vibrate) navigator.vibrate(15); toggleSel(row.dataset.path); }, 450);
        const cancel = (ev) => { if (ev.type === "pointermove" && Math.hypot(ev.clientX - sx, ev.clientY - sy) < 8) return; clearTimeout(pressTimer); L.removeEventListener("pointermove", cancel); L.removeEventListener("pointerup", cancel); L.removeEventListener("pointercancel", cancel); };
        L.addEventListener("pointermove", cancel); L.addEventListener("pointerup", cancel); L.addEventListener("pointercancel", cancel);
      }
    });
    let hoverT = 0;
    L.addEventListener("pointerover", (e) => {
      const row = e.pointerType === "touch" ? null : e.target.closest(".row");
      clearTimeout(hoverT);
      if (row && row.dataset.dir && !row.dataset.pending && st.mode === "files") hoverT = setTimeout(() => prefetchDir(row.dataset.path), 90);
    });
    L.addEventListener("click", (e) => {
      if (pressed) { pressed = false; return; }
      const row = e.target.closest(".row");
      if (!row) { if (!e.target.closest(".head") && !band.moved) { st.sel = []; paint(); } return; }
      const path = row.dataset.path;
      if (e.target.classList.contains("chk")) { toggleSel(path); return; }
      if (lastPointer === "touch") {
        if (st.sel.length) toggleSel(path); else openPath(path);
        return;
      }
      const r = FC.applyClick(st.shown.map((x) => x.path), st.sel, st.anchor, path, { mod: e.ctrlKey || e.metaKey, shift: e.shiftKey });
      st.sel = r.selected; st.anchor = r.anchor; paint();
    });
    L.addEventListener("dblclick", (e) => { const row = e.target.closest(".row"); if (row && lastPointer !== "touch") openPath(row.dataset.path); });
    L.addEventListener("keydown", (e) => { const row = e.target.closest(".row"); if (row && e.key === "Enter") { e.preventDefault(); openPath(row.dataset.path); } else if (row && e.key === " ") { e.preventDefault(); toggleSel(row.dataset.path); } else if (row && (e.key === "ArrowDown" || e.key === "ArrowUp")) { e.preventDefault(); const n = e.key === "ArrowDown" ? row.nextElementSibling : row.previousElementSibling; if (n && n.classList.contains("row")) n.focus(); } });
    L.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      const row = e.target.closest(".row");
      if (row) { if (!st.sel.includes(row.dataset.path)) { st.sel = [row.dataset.path]; st.anchor = row.dataset.path; paint(); } rowMenu(e.clientX, e.clientY); }
      else if (st.mode === "files") popMenu(e.clientX, e.clientY, [{ label: "New folder", icon: "folderplus", run: newFolder }, { label: "New file", icon: "fileplus", run: newFile }, { label: "Upload files", icon: "upload", run: () => el.fileIn.click() }, st.clip ? { label: "Paste", icon: "paste", run: doPaste } : null]);
    });
    // rubber band (mouse)
    el.wrap.addEventListener("pointerdown", bandStart);
  }
  const band = { moved: false };
  function bandStart(e) {
    if (e.pointerType !== "mouse" || e.button !== 0 || e.target.closest(".row") || e.target.closest(".head") || st.view === "x") return;
    const wrap = el.wrap, rect = () => wrap.getBoundingClientRect();
    const sx = e.clientX - rect().left + wrap.scrollLeft, sy = e.clientY - rect().top + wrap.scrollTop;
    const base = e.ctrlKey || e.metaKey ? st.sel.slice() : [];
    const box = h("div", { id: "band" }); let active = false; band.moved = false;
    const move = (ev) => {
      const cx = ev.clientX - rect().left + wrap.scrollLeft, cy = ev.clientY - rect().top + wrap.scrollTop;
      if (!active && Math.hypot(cx - sx, cy - sy) < 4) return;
      if (!active) { active = true; band.moved = true; wrap.append(box); }
      const x1 = Math.min(sx, cx), y1 = Math.min(sy, cy), x2 = Math.max(sx, cx), y2 = Math.max(sy, cy);
      Object.assign(box.style, { left: x1 + "px", top: y1 + "px", width: x2 - x1 + "px", height: y2 - y1 + "px" });
      const rows = [...el.list.querySelectorAll(".row")], wr = rect();
      const hits = [];
      rows.forEach((r, i) => { const b = r.getBoundingClientRect(); const top = b.top - wr.top + wrap.scrollTop, left = b.left - wr.left + wrap.scrollLeft; if (top < y2 && top + b.height > y1 && left < x2 && left + b.width > x1) hits.push(i); });
      st.sel = FC.bandSelection(st.shown.map((x) => x.path), base, hits, true); paint();
      const sp = FC.autoScrollSpeed(ev.clientY, wr.top, wr.bottom); if (sp) wrap.scrollTop += sp;
    };
    const up = () => { document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", up); box.remove(); setTimeout(() => { band.moved = false; }, 0); };
    document.addEventListener("pointermove", move); document.addEventListener("pointerup", up);
  }
  function toggleSel(path) { const r = FC.applyClick(st.shown.map((x) => x.path), st.sel, st.anchor, path, { touch: true }); st.sel = r.selected; st.anchor = r.anchor; paint(); }

  function onKey(e) {
    if (document.querySelector(".scrim, #viewer, #editor") || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || st.mode === "trash" && !e.ctrlKey) return;
    const mod = e.ctrlKey || e.metaKey, es = selEntries();
    if (mod && e.key.toLowerCase() === "a") { e.preventDefault(); st.sel = (st.shown || []).map((x) => x.path); paint(); }
    else if (mod && e.key.toLowerCase() === "c" && es.length) { e.preventDefault(); setClip(false); }
    else if (mod && e.key.toLowerCase() === "x" && es.length) { e.preventDefault(); setClip(true); }
    else if (mod && e.key.toLowerCase() === "v" && st.clip) { e.preventDefault(); doPaste(); }
    else if (e.key === "Delete" && es.length) { e.preventDefault(); del(es, e.shiftKey); }
    else if (e.key === "/" || (mod && e.key.toLowerCase() === "k")) { e.preventDefault(); el.search.focus(); el.search.select(); }
    else if (e.key === "F2" && es.length === 1) { e.preventDefault(); rename(es[0]); }
    else if (e.key === "Escape") { st.sel = []; paint(); closeSide(); }
    else if (e.key === "Backspace" && st.mode === "files" && st.path !== "/") { e.preventDefault(); go(parentOf(st.path)); }
  }
  const trashOn = () => !st.status || st.status.trash !== false;
  const whenIdle = () => (st.pending ? new Promise((r) => idleWaiters.push(r)) : Promise.resolve());
  const parentOf = (p) => p.replace(/\/[^/]*$/, "") || "/";
  const joinp = (d, n) => (d === "/" ? "" : d) + "/" + n;

  function rowMenu(x, y) {
    const es = selEntries(), one = es.length === 1 ? es[0] : null;
    popMenu(x, y, [
      one ? { label: "Open", icon: "play", run: () => openPath(one.path) } : null,
      { label: "Download" + (es.length > 1 || (one && one.dir) ? " (zip)" : ""), icon: "download", run: () => downloadSel(es) },
      one && !one.dir ? { label: "Share link…", icon: "link", run: () => share(one) } : null, "-",
      one ? { label: "Rename", icon: "edit", run: () => rename(one) } : null,
      { label: "Copy", icon: "copy", run: () => setClip(false) }, { label: "Cut", icon: "cut", run: () => setClip(true) },
      one && one.dir && st.clip ? { label: "Paste into folder", icon: "paste", run: () => doPaste(one.path) } : null, "-",
      { label: "Compress to zip", icon: "zip", run: () => compress(es) },
      one && /\.zip$/i.test(one.name) ? { label: "Extract here", icon: "archive", run: () => extract(one) } : null,
      one && !one.dir && one.kind === "text" ? { label: "Edit", icon: "edit", run: () => editPath(one) } : null,
      one ? { label: "Properties", icon: "info", run: () => props(one) } : null, "-",
      { label: trashOn() ? "Move to trash" : "Delete forever", icon: "trash", danger: true, run: () => del(es) },
      trashOn() ? { label: "Delete forever…", icon: "x", danger: true, run: () => del(es, true) } : null]);
  }

  // ------------------------------------------------------------ opening things
  function openPath(path) {
    const e = st.entries.find((x) => x.path === path); if (!e) return;
    if (e.pending) { toast("Saving “" + e.name + "”…"); whenIdle().then(() => { const n = st.entries.find((x) => x.path === path); if (n) openPath(path); }); return; }
    if (e.dir) { go(e.path); return; }
    if (["image", "video", "audio", "pdf"].includes(e.kind)) return openViewer(e);
    if (e.kind === "archive" || e.kind === "binary") { toast("Can't preview this type; downloading instead"); return downloadSel([e]); }
    editPath(e);
  }
  function editPath(e) { window.Editor.open(e.path, e.name, () => reload()).then((ok) => { if (ok === false && /\.(zip|rar|7z|tar|gz)$/i.test(e.name)) downloadSel([e]); }); }

  function openViewer(entry) {
    const items = (st.shown || []).filter((x) => ["image", "video", "audio", "pdf"].includes(x.kind));
    let i = Math.max(0, items.findIndex((x) => x.path === entry.path));
    const stage = h("div", { id: "vstage" }), title = h("div", { class: "t" }), count = h("span", { class: "muted" });
    const v = h("div", { id: "viewer", role: "dialog", "aria-label": "Viewer", tabindex: -1 },
      h("div", { class: "vbar" }, title, count, h("button", { class: "btn", type: "button", onclick: () => { const x = items[i]; downloadSel([x]); } }, icon("download"), h("span", { class: "lb" }, "Download")), h("button", { class: "btn icon", type: "button", "aria-label": "Close", onclick: close }, icon("x"))), stage);
    let live = null;                                   // whatever must be torn down when the viewer moves on (PDF worker, blob URL, request)
    const cleanup = () => { if (live) { try { live(); } catch (e) { /* already gone */ } live = null; } };
    const failCard = (x) => h("div", { class: "vcard" }, h("div", null, "This file can't be shown in the browser."), h("button", { class: "btn primary", type: "button", onclick: () => downloadSel([x]) }, "Download"));
    // Big files must never leave a blank screen: a spinner (and, where the size is known, a bar) shows until the first bytes are usable.
    function loader(text) {
      const fill = h("i"), bar = h("div", { class: "vprog" }, fill), label = h("span", null, text || "Loading…");
      const box = h("div", { class: "vload" }, h("span", { class: "vspin" }), label, bar);
      return { box, set(f, t) { if (t) label.textContent = t; if (f !== null && f !== undefined) { bar.classList.add("on"); fill.style.width = (Math.min(1, f) * 100).toFixed(1) + "%"; } }, done() { box.remove(); } };
    }
    function showImage(x, src, wrap) {
      const ld = loader("Loading image…"); wrap.append(ld.box);
      const ctl = new AbortController(); let url = null;
      live = () => { ctl.abort(); if (url) URL.revokeObjectURL(url); };
      fetch(src, { credentials: "same-origin", signal: ctl.signal }).then(async (r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        const total = +r.headers.get("Content-Length") || 0, type = r.headers.get("Content-Type") || "";
        if (!r.body || !total) { return r.blob(); }
        const rd = r.body.getReader(), got = []; let n = 0;
        for (;;) { const { done, value } = await rd.read(); if (done) break; got.push(value); n += value.length; ld.set(n / total, "Loading image… " + fmtBytes(n) + " of " + fmtBytes(total)); }
        return new Blob(got, { type });
      }).then((blob) => {
        url = URL.createObjectURL(blob);
        const img = h("img", { src: url, alt: x.name });
        img.onerror = () => wrap.replaceChildren(failCard(x));
        img.onload = () => ld.done();
        wrap.append(img);
      }).catch((e) => {
        if (e.name === "AbortError") return;
        // progress isn't available (or fetch failed): let the browser load the image itself, still with the spinner up
        const img = h("img", { alt: x.name });
        img.onload = () => ld.done();
        img.onerror = () => wrap.replaceChildren(failCard(x));
        img.src = src; wrap.append(img);
      });
    }
    function showMedia(x, src, wrap, kind) {
      const ld = loader(kind === "video" ? "Loading video…" : "Loading audio…"); wrap.append(ld.box);
      const m = kind === "video" ? h("video", { src, controls: true, autoplay: false, playsInline: true, preload: "metadata" }) : h("audio", { src, controls: true, preload: "metadata" });
      const hide = () => ld.done(), buffering = () => { if (!m.paused || m.readyState < 2) wrap.append(ld.box); };
      m.addEventListener("loadedmetadata", hide); m.addEventListener("canplay", hide); m.addEventListener("playing", hide);
      m.addEventListener("waiting", buffering); m.addEventListener("seeking", buffering); m.addEventListener("seeked", hide);
      m.onerror = () => wrap.replaceChildren(failCard(x));
      live = () => { m.pause(); m.removeAttribute("src"); m.load(); };
      if (kind === "audio") wrap.replaceChildren(h("div", { class: "vcard" }, icon("audio"), h("div", null, x.name), m, ld.box)); else wrap.append(m);
    }
    function show() {
      cleanup();
      const x = items[i], src = "/api/raw?path=" + enc(x.path);
      title.textContent = x.name; count.textContent = items.length > 1 ? `${i + 1} / ${items.length}` : "";
      const wrap = h("div", { class: "vwrap" });
      if (x.kind === "image") showImage(x, src, wrap);
      else if (x.kind === "video" || x.kind === "audio") showMedia(x, src, wrap, x.kind);
      else {
        const host = h("div", { class: "pvhost" }); wrap.append(host);
        const pv = window.PdfView.mount(host, src, { version: (document.querySelector("meta[name=ark-version]") || {}).content || "", onError: () => host.append(h("div", { class: "pvfail" }, h("button", { class: "btn primary", type: "button", onclick: () => downloadSel([x]) }, "Download"), h("a", { class: "btn", href: src, target: "_blank", rel: "noopener" }, "Open in new tab"))) });
        live = () => pv.destroy();
      }
      stage.replaceChildren(...(items.length > 1 ? [h("button", { class: "btn icon vnav l", type: "button", "aria-label": "Previous", onclick: () => step(-1) }, "‹"), h("button", { class: "btn icon vnav r", type: "button", "aria-label": "Next", onclick: () => step(1) }, "›")] : []), wrap);
    }
    const step = (d) => { i = (i + d + items.length) % items.length; show(); };
    const onk = (e) => { if (e.key === "Escape") close(); else if (e.key === "ArrowRight" && !/AUDIO|VIDEO/.test(document.activeElement.tagName)) step(1); else if (e.key === "ArrowLeft" && !/AUDIO|VIDEO/.test(document.activeElement.tagName)) step(-1); };
    function close() { cleanup(); document.removeEventListener("keydown", onk); v.remove(); }
    let tx = 0; v.addEventListener("touchstart", (e) => { tx = e.touches[0].clientX; }, { passive: true });
    v.addEventListener("touchend", (e) => { const dx = e.changedTouches[0].clientX - tx; if (Math.abs(dx) > 70 && items[i].kind === "image") step(dx < 0 ? 1 : -1); }, { passive: true });
    document.addEventListener("keydown", onk); document.body.append(v); show(); v.focus();
  }

  // ------------------------------------------------------------ actions
  async function guard(fn) { try { return await fn(); } catch (e) { toast(e.message, "bad"); return null; } }

  function newMenu(anchor) {
    const r = anchor.getBoundingClientRect();
    popMenu(Math.min(r.left, innerWidth - 220), r.bottom + 6 > innerHeight - 260 ? r.top - 230 : r.bottom + 6, [
      { label: "New folder", icon: "folderplus", run: newFolder }, { label: "New file", icon: "fileplus", run: newFile }, "-",
      { label: "Upload files", icon: "upload", run: () => el.fileIn.click() }, { label: "Upload folder", icon: "folder", run: () => el.dirIn.click() }]);
  }

  // Writes the way fast apps do them: the screen changes immediately, GitHub is told in the background,
  // and if that fails the real state is reloaded and the error shown. Several can be in flight at once;
  // the list is reconciled with the server when the last one finishes.
  async function optimistic(local, remote) {
    if (local) local();
    st.pending++; syncUI();
    let out = null;
    try { out = await remote(); } catch (e) { toast(e.message, "bad"); out = null; }
    st.pending--; invalidate(); syncUI();
    if (!st.pending) { await reload(); loadStatus(); const w = idleWaiters; idleWaiters = []; w.forEach((f) => f()); }
    return out;
  }
  function addLocal(dir, entry) {
    if (st.mode !== "files" || st.path !== dir || entry.name.includes("/")) return;
    st.entries = st.entries.concat(entry); render();
  }
  const exists = (p) => st.entries.some((e) => e.path === p);

  async function newFolder() {
    const name = await prompt("New folder", "", { label: "Folder name", ok: "Create" }); if (!name) return;
    const dir = st.path, p = joinp(dir, name);
    if (exists(p)) return toast("“" + name + "” already exists here", "bad");
    st.flash = p;
    await optimistic(() => addLocal(dir, { name, path: p, dir: true, size: 0, mtime: Date.now() / 1000, kind: "dir", items: 0, enc: false, pending: true }),
      () => api("/api/mkdir", { method: "POST", body: { path: p } }));
  }
  async function newFile() {
    const name = await prompt("New file", "untitled.txt", { label: "File name", ok: "Create", stem: true }); if (!name) return;
    const dir = st.path, p = joinp(dir, name);
    if (exists(p)) return toast("“" + name + "” already exists here", "bad");
    st.flash = p;
    await optimistic(() => addLocal(dir, { name, path: p, dir: false, size: 0, mtime: Date.now() / 1000, kind: "text", enc: false, parts: 1, mime: "text/plain", pending: true }),
      () => api("/api/newfile", { method: "POST", body: { path: p } }).then((r) => { window.Editor.open(p, name.split("/").pop(), () => reload()); return r; }));   // editor opens the moment it exists
  }
  async function rename(e) {
    const name = await prompt("Rename", e.name, { label: "New name", ok: "Rename", stem: !e.dir }); if (!name || name === e.name) return;
    if (name.includes("/")) return toast("A name can't contain “/”", "bad");
    const np = joinp(parentOf(e.path), name);
    if (exists(np)) return toast("“" + name + "” already exists here", "bad");
    st.flash = np;
    await optimistic(() => { st.entries = st.entries.map((x) => (x.path === e.path ? Object.assign({}, x, { name, path: np, pending: true }) : x)); st.sel = []; render(); },
      () => api("/api/rename", { method: "POST", body: { path: e.path, name } }));
  }
  async function del(es, forever) {
    const permanent = !!forever || !trashOn();
    const what = es.length === 1 ? `“${es[0].name}”` : `${es.length} items`;
    // Moving to the trash needs no question: it is instant and undoable. Only "forever" asks first.
    if (permanent && !(await ask("Delete " + what + " forever?", "This can't be undone.", { ok: "Delete forever", danger: true }))) return;
    const gone = new Set(es.map((x) => x.path)), dir = st.path;
    await optimistic(() => { st.entries = st.entries.filter((e) => !gone.has(e.path)); st.sel = []; render(); },
      () => api("/api/delete", { method: "POST", body: { paths: [...gone], permanent } }).then((r) => {
        const ids = ((r && r.results) || []).map((x) => x.trash_id).filter(Boolean);
        if (permanent || !ids.length) toast("Deleted " + what);
        else toast("Moved " + what + " to trash", "ok", 8000, { label: "Undo", run: () => undo(ids, es, dir) });
        return r;
      }));
  }
  function undo(ids, es, dir) {
    return optimistic(() => { if (st.mode === "files" && st.path === dir) { st.entries = st.entries.concat(es.map((e) => Object.assign({}, e, { pending: true }))); render(); } },
      () => api("/api/trash/restore", { method: "POST", body: { ids } }).then((r) => { toast("Restored"); return r; }));
  }
  function setClip(cut) { st.clip = { paths: st.sel.slice(), cut }; paint(); el.paste.hidden = false; toast(`${st.clip.paths.length} item(s) ${cut ? "cut" : "copied"}`); }
  async function doPaste(into) {
    if (!st.clip) return;
    const dest = typeof into === "string" ? into : st.path, clip = st.clip;
    const ex = await guard(() => api("/api/exists", { method: "POST", body: { paths: clip.paths.map((p) => joinp(dest, p.split("/").pop())) } })); if (!ex) return;
    let pol = "rename";
    if (ex.existing.length) {
      pol = await choose("Name conflict", `${ex.existing.length} item(s) already exist in the destination.`, [{ label: "Skip", value: "skip" }, { label: "Keep both", value: "rename" }, { label: "Replace", kind: "danger", value: "replace" }]);
      if (!pol) return;
    }
    await optimistic(null, () => api("/api/move", { method: "POST", body: { paths: clip.paths, dest, copy: !clip.cut, on_conflict: pol } })
      .then((r) => { if (clip.cut) st.clip = null; toast(clip.cut ? "Moved" : "Copied"); return r; }));
  }
  function downloadSel(es) {
    if (es.length === 1 && !es[0].dir) { const a = h("a", { href: "/api/raw?download=1&path=" + enc(es[0].path), download: es[0].name }); document.body.append(a); a.click(); a.remove(); return; }
    const q = es.map((x) => "p=" + enc(x.path)).join("&") + (es.length === 1 ? "&name=" + enc(es[0].name) : "&name=" + enc(st.path === "/" ? "ark-files" : st.path.split("/").pop()));
    const a = h("a", { href: "/api/zip?" + q }); document.body.append(a); a.click(); a.remove();
    toast("Preparing zip — the download starts as it streams");
  }
  async function share(e) {
    const hours = (st.status && 24) || 24;
    const sel = h("select", { class: "sel" }, ...[[1, "1 hour"], [24, "1 day"], [168, "7 days"], [720, "30 days"], [8760, "1 year"]].map(([v, l]) => h("option", { value: v, selected: v === hours }, l)));
    const out = h("input", { class: "inp mono", readOnly: true, placeholder: "Click “Create link”" });
    const go2 = async () => { const r = await guard(() => api("/api/share", { method: "POST", body: { path: e.path, hours: Number(sel.value) } })); if (r) { out.value = r.url; out.select(); try { await navigator.clipboard.writeText(r.url); toast("Link copied"); } catch (x) { /* manual copy */ } } };
    await modal({ title: "Share “" + e.name + "”", body: h("div", null, h("div", { class: "hint", style: { marginBottom: "10px" } }, "Anyone with the link can download this file until it expires. Links stop working if the file is deleted."), h("div", { class: "field" }, h("label", { class: "lbl" }, "Link lasts"), sel), out),
      buttons: [{ label: "Close", value: null }], footExtra: h("button", { class: "btn primary", type: "button", onclick: go2 }, "Create link") });
  }
  async function props(e) {
    const d = await guard(() => api("/api/stat?path=" + enc(e.path))); if (!d) return;
    const kv = [["Name", d.name], ["Location", d.path.replace(/\/[^/]*$/, "") || "/"], ["Type", d.dir ? "Folder" : d.mime || "File"], ["Size", fmtBytes(d.size) + (d.size ? ` (${d.size.toLocaleString()} bytes)` : "")], ["Modified", fmtDate(d.mtime)]];
    if (d.dir) kv.push(["Contains", `${d.files} file(s), ${d.folders} folder(s)`]); else kv.push(["Stored as", `${d.pieces} piece${d.pieces > 1 ? "s" : ""}${d.enc ? ", encrypted" : ", plain"}`], ["In repo", d.repo_path]);
    await modal({ title: "Properties", body: h("dl", { class: "kv" }, ...kv.flatMap(([k, v]) => [h("dt", null, k), h("dd", null, String(v))])) });
  }
  async function compress(es) {
    const base = es.length === 1 ? es[0].name.replace(/\.[^.]*$/, "") || es[0].name : "archive";
    const name = await prompt("Compress to zip", base + ".zip", { label: "Archive name", ok: "Compress" }); if (!name) return;
    const r = await guard(() => api("/api/compress", { method: "POST", body: { paths: es.map((x) => x.path), dest: st.path, name: name.endsWith(".zip") ? name : name + ".zip" } }));
    if (r) trackJob(r.job);
  }
  async function extract(e) { const r = await guard(() => api("/api/extract", { method: "POST", body: { path: e.path } })); if (r) trackJob(r.job); }

  // ------------------------------------------------------------ search
  let searchSeq = 0;
  async function onSearch() {
    const q = el.search.value.trim(); st.query = q; const seq = ++searchSeq;
    if (!q) { st.mode = "files"; return reload(); }
    const d = await guard(() => api("/api/search?q=" + enc(q) + "&path=" + enc(st.mode === "search" ? "/" : st.path)));
    if (!d || seq !== searchSeq) return;
    st.mode = "search"; st.entries = d.entries; st.sel = []; st.loadErr = ""; render();
  }

  // ------------------------------------------------------------ trash
  async function showTrash() {
    st.mode = "trash"; st.sel = []; st.tsel = new Set(); loadSeq++;
    renderCrumbs(); el.nav.files.classList.remove("on"); el.nav.trash.classList.add("on"); el.paste.hidden = true; el.viewSeg.hidden = true; el.selBar.hidden = true;
    drawTrash();                                       // instant: what we knew last time, or "Loading…" on the very first visit
    const d = await guard(() => api("/api/trash"));
    if (st.mode !== "trash") return;
    if (d) { st.trash = d.items; st.trashLoaded = true; }
    drawTrash();
  }
  // Rows vanish immediately, GitHub is updated in the background, the list is re-synced when it's done.
  async function trashAct(ids, url, label, okMsg) {
    const gone = new Set(ids);
    await optimistic(() => { st.trash = st.trash.filter((t) => !gone.has(t.id)); st.tsel = new Set(); drawTrash(); },
      () => api(url, { method: "POST", body: { ids } }).then((r) => { toast(okMsg); return r; }));
  }
  function drawTrash() {
    el.selBar.hidden = true; el.list.className = "";
    const sel = st.tsel, all = st.trash.map((t) => t.id), picked = all.filter((i) => sel.has(i));
    const tool = h("div", { style: { padding: "10px 14px", display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" } },
      h("span", { class: "muted" }, picked.length ? `${picked.length} selected` : `${all.length} item(s). Items stay in git history until you compact it.`), h("span", { class: "spacer" }),
      picked.length ? btn("Restore selected", "restore", () => trashAct(picked, "/api/trash/restore", `Restoring ${picked.length} item(s)`, "Restored")) : btn("Restore all", "restore", () => trashAct(all, "/api/trash/restore", "Restoring everything", "Restored")),
      picked.length
        ? btn("Delete selected", "trash", async () => { if (await ask("Delete forever?", `${picked.length} item(s) will be removed permanently.`, { ok: "Delete", danger: true })) trashAct(picked, "/api/trash/empty", `Deleting ${picked.length} item(s) forever`, "Deleted"); }, "danger")
        : btn("Empty trash", "trash", async () => { if (await ask("Empty the trash?", "Everything in it is removed permanently.", { ok: "Empty trash", danger: true })) trashAct(all, "/api/trash/empty", "Emptying the trash", "Trash emptied"); }, "danger"));
    const rows = st.trash.map((t) => {
      const chk = h("input", { type: "checkbox", class: "chk always", checked: sel.has(t.id), "aria-label": "Select " + t.name, onclick: (e) => { e.stopPropagation(); if (chk.checked) sel.add(t.id); else sel.delete(t.id); drawTrash(); } });
      return h("div", { class: "row" + (sel.has(t.id) ? " sel" : ""), style: { gridTemplateColumns: "28px minmax(0,1fr) 110px 150px 200px" } }, chk,
        h("div", { class: "nm" }, h("div", { class: "ic " + (t.dir ? "dir" : "file") }, icon(t.dir ? "folder" : "file")), h("div", { style: { minWidth: 0 } }, h("div", { class: "t", style: { overflow: "hidden", textOverflow: "ellipsis" } }, t.name), h("div", { class: "muted", style: { fontSize: "12px", overflow: "hidden", textOverflow: "ellipsis" } }, t.path))),
        h("div", { class: "sz" }, t.dir ? "folder" : fmtBytes(t.size)), h("div", { class: "md" }, fmtDate(t.ts)),
        h("div", { style: { display: "flex", gap: "6px" } },
          btn("Restore", "restore", () => trashAct([t.id], "/api/trash/restore", "Restoring " + t.name, "Restored to " + t.path)),
          btn("", "trash", async () => { if (await ask("Delete forever?", `“${t.name}” will be removed permanently.`, { ok: "Delete", danger: true })) trashAct([t.id], "/api/trash/empty", "Deleting " + t.name, "Deleted"); }, "danger icon", "Delete forever")));
    });
    el.list.replaceChildren(...(!st.trashLoaded ? [h("div", { class: "empty" }, "Loading…")] : rows.length ? [tool, ...rows] : [h("div", { class: "empty" }, icon("trash"), h("div", null, "The trash is empty."))]));
    renderCrumbs(); renderStatus();
  }

  // ------------------------------------------------------------ uploads
  const xfers = [];
  function renderXfer() {
    el.xfer.hidden = !xfers.length;
    if (!xfers.length) return;
    const running = xfers.filter((x) => x.state === "run").length;
    el.xfer.replaceChildren(
      h("header", { onclick: () => { st.xferOpen = !st.xferOpen; renderXfer(); } }, h("span", null, running ? `Transfers (${running} running)` : "Transfers"),
        h("span", { style: { display: "flex", gap: "6px" } }, !running ? h("button", { class: "btn ghost icon", type: "button", "aria-label": "Clear", onclick: (e) => { e.stopPropagation(); xfers.length = 0; renderXfer(); } }, icon("trash")) : null, h("span", null, st.xferOpen ? "▾" : "▴"))),
      st.xferOpen ? h("div", { class: "items" }, ...xfers.slice().reverse().map(xferRow)) : null);
    xfers.forEach(paintX); kick();
  }
  function xferRow(x) {
    const pct = h("span", { class: "muted" }), bar = h("i"), msg = h("div", { class: "muted", style: { fontSize: "12px" } });
    x.dom = { pct, bar, msg };
    return h("div", { class: "xi " + x.state }, h("div", { class: "t" }, h("span", null, x.title), pct), h("div", { class: "meter" }, bar), msg);
  }
  // Only the bar, the percentage and the message are touched per frame; the panel is rebuilt only when something is added or finishes.
  function paintX(x) {
    if (!x.dom) return;
    const v = x.m.shown;
    x.dom.bar.style.transform = "scaleX(" + v.toFixed(4) + ")";
    const t = x.state === "err" ? "failed" : x.state === "done" && v >= 1 ? "done" : Math.round(v * 100) + "%";
    if (x.dom.pct.textContent !== t) x.dom.pct.textContent = t;
    if (x.dom.msg.textContent !== (x.msg || "")) x.dom.msg.textContent = x.msg || "";
  }
  let raf = 0;
  function frame(now) {
    raf = 0; let live = false;
    for (const x of xfers) { if (x.state === "run" || (x.state === "done" && x.m.shown < 1)) { PR.step(x.m, now); live = true; } paintX(x); }
    if (live) raf = requestAnimationFrame(frame);
  }
  function kick() { if (!raf && xfers.some((x) => x.state === "run" || (x.state === "done" && x.m.shown < 1))) raf = requestAnimationFrame(frame); }
  function newXfer(title) { const x = { title, state: "run", msg: "", m: PR.meter(performance.now()), dom: null }; xfers.push(x); st.xferOpen = true; renderXfer(); return x; }
  function xprog(x, v, msg, headroom) { PR.report(x.m, v, performance.now(), headroom); if (msg !== undefined) x.msg = msg; kick(); }
  function xdone(x) { x.state = "done"; x.msg = ""; PR.finish(x.m); renderXfer(); }
  function xfail(x, msg) { x.state = "err"; x.msg = msg; renderXfer(); }

  // One chunk via XHR: unlike fetch it reports bytes as they leave the browser, so the bar moves smoothly instead of per 16 MB chunk.
  function putChunk(url, blob, onBytes) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", url); xhr.withCredentials = true; xhr.setRequestHeader("X-CSRF-Token", S.csrf);
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) onBytes(e.loaded); };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) return resolve();
        let m = xhr.statusText; try { m = JSON.parse(xhr.responseText).error || m; } catch (e) { /* not json */ }
        reject(Object.assign(new Error(m || "Upload failed"), { fatal: xhr.status < 500 && xhr.status !== 429 }));
      };
      xhr.onerror = () => reject(new Error("Can't reach the server. Check your connection."));
      xhr.onabort = () => reject(Object.assign(new Error("Upload cancelled"), { fatal: true }));
      xhr.send(blob);
    });
  }

  const CHUNK = 8 * 1024 * 1024;
  async function uploadFiles(items, dirs) {
    if (!items.length && !(dirs && dirs.length)) return;
    if (st.mode !== "files") { toast("Open a folder first", "bad"); return; }
    const dest = st.path;
    const files = items.map((it, i) => ({ id: "f" + i, file: it.file, path: joinp(dest, it.rel.replace(/^\/+/, "")), mtime: it.file.lastModified }));
    let policy = "rename";
    const ex = await guard(() => api("/api/exists", { method: "POST", body: { paths: files.map((f) => f.path) } }));
    if (!ex) return;
    if (ex.existing.length) {
      policy = await choose("Files already exist", `${ex.existing.length} of ${files.length} file(s) already exist in this folder.`, [{ label: "Skip them", value: "skip" }, { label: "Keep both", value: "rename" }, { label: "Replace", kind: "danger", value: "replace" }]);
      if (!policy) return;
    }
    const batches = []; let cur = [], bytes = 0;
    files.forEach((f) => { if (cur.length && (bytes + f.file.size > 1024 * 1048576 || cur.length >= 300)) { batches.push(cur); cur = []; bytes = 0; } cur.push(f); bytes += f.file.size; });
    if (cur.length || (dirs && dirs.length)) batches.push(cur);
    for (let b = 0; b < batches.length; b++) {
      const batch = batches[b], total = batch.reduce((s, f) => s + f.file.size, 0) || 1;
      const x = newXfer(batch.length === 1 ? batch[0].file.name : `${batch.length} files` + (batches.length > 1 ? ` (batch ${b + 1}/${batches.length})` : ""));
      let begin = null;
      try {
        begin = await api("/api/upload/begin", { method: "POST", body: { files: batch.map((f) => ({ id: f.id, size: f.file.size })) } });
        const CH = begin.chunk || CHUNK;
        // Every chunk of every file goes into one queue; 4 requests are always in flight, so even a single
        // huge file saturates the connection. The server puts the pieces back in order.
        const tasks = [];
        batch.forEach((f) => { for (let off = 0; off < f.file.size || (off === 0 && f.file.size === 0); off += CH) { tasks.push({ f, off }); if (f.file.size === 0) break; } });
        let next = 0, doneBytes = 0;
        const live = new Map(), speed = PR.rateTracker(4000);
        const report = () => {
          let sent = doneBytes; live.forEach((n) => { sent += n; }); sent = Math.min(sent, total);
          PR.sample(speed, performance.now(), sent);
          xprog(x, (sent / total) * 0.9, `Uploading ${fmtBytes(sent)} of ${fmtBytes(total)} · ${fmtBytes(PR.rate(speed))}/s`, 0.025);
        };
        const worker = async () => {
          while (next < tasks.length) {
            const { f, off } = tasks[next++], key = f.id + ":" + off, blob = f.file.slice(off, off + CH);
            for (let tries = 0; ; tries++) {
              try { await putChunk(`/api/upload/${begin.batch}/${f.id}?offset=${off}`, blob, (n) => { live.set(key, n); report(); }); break; }
              catch (e) { live.delete(key); if (e.fatal || tries >= 4) throw e; await new Promise((r) => setTimeout(r, 600 * (tries + 1))); }
            }
            live.delete(key); doneBytes += blob.size; report();
          }
        };
        await Promise.all([worker(), worker(), worker(), worker()]);
        xprog(x, 0.92, "Finishing on GitHub…", 0.06);
        const r = await api(`/api/upload/${begin.batch}/commit`, { method: "POST", body: { files: batch.map((f) => ({ id: f.id, path: f.path, mtime: f.mtime })), dirs: b === 0 ? dirs || [] : [], on_conflict: policy } });
        await followJob(r.job, x, 0.92);
      } catch (e) {
        xfail(x, e.message); toast(e.message, "bad");
        if (begin && begin.batch) api(`/api/upload/${begin.batch}/cancel`, { method: "POST", body: {} }).catch(() => {});
      }
    }
    invalidate(); reload(); loadStatus();
  }
  async function followJob(job, x, base) {
    for (;;) {
      const j = await api("/api/jobs/" + job.id);
      xprog(x, base + j.progress * (1 - base), j.message || x.msg, 0.04);
      if (j.state === "done") { xdone(x); return j; }
      if (j.state === "error") { xfail(x, j.error); throw new Error(j.error); }
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  async function trackJob(job) { const x = newXfer(job.title); try { await followJob(job, x, 0); toast(job.title + " finished"); invalidate(); reload(); loadStatus(); } catch (e) { toast(e.message, "bad"); } }

  // ------------------------------------------------------------ drag & drop
  function bindDrop() {
    let depth = 0, zone = null;
    const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
    el.main.addEventListener("dragenter", (e) => { if (!hasFiles(e)) return; depth++; if (!zone) { zone = h("div", { id: "dropzone" }, "Drop to upload to this folder"); el.main.append(zone); } });
    el.main.addEventListener("dragleave", (e) => { if (!hasFiles(e)) return; if (--depth <= 0) { depth = 0; if (zone) zone.remove(); zone = null; } });
    el.main.addEventListener("dragover", (e) => { e.preventDefault(); const t = e.target.closest("[data-path],[data-drop]"); document.querySelectorAll(".drop").forEach((x) => x.classList.remove("drop")); const rowT = t && (t.dataset.drop || (st.entries.find((x) => x.path === t.dataset.path) || {}).dir) ? t : null; if (rowT && !hasFiles(e) && !(rowT.dataset.path && st.sel.includes(rowT.dataset.path))) rowT.classList.add("drop"); });
    el.main.addEventListener("drop", async (e) => {
      e.preventDefault(); depth = 0; if (zone) zone.remove(); zone = null; document.querySelectorAll(".drop").forEach((x) => x.classList.remove("drop"));
      if (hasFiles(e)) { const { items, dirs } = await collect(e.dataTransfer); uploadFiles(items, dirs); return; }
      const mv = e.dataTransfer.getData("application/x-ark"); if (!mv) return;
      const t = e.target.closest("[data-path],[data-drop]"); let dest = null;
      if (t) dest = t.dataset.drop || ((st.entries.find((x) => x.path === t.dataset.path) || {}).dir ? t.dataset.path : null);
      if (!dest) return;
      const paths = JSON.parse(mv).filter((p) => p !== dest && !dest.startsWith(p + "/"));
      if (!paths.length) return;
      const moved = new Set(paths);
      await optimistic(() => { st.entries = st.entries.filter((x) => !moved.has(x.path)); st.sel = []; render(); },
        () => api("/api/move", { method: "POST", body: { paths, dest, on_conflict: "rename" } }).then((r) => { toast("Moved"); return r; }));
    });
    el.list.addEventListener("dragstart", (e) => {
      const row = e.target.closest(".row"); if (!row) return;
      if (!st.sel.includes(row.dataset.path)) { st.sel = [row.dataset.path]; paint(); }
      e.dataTransfer.setData("application/x-ark", JSON.stringify(st.sel)); e.dataTransfer.effectAllowed = "move";
    });
  }
  async function collect(dt) {
    const items = [], dirs = [];
    const entries = [...dt.items].map((i) => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null)).filter(Boolean);
    if (!entries.length) return { items: [...dt.files].map((f) => ({ file: f, rel: f.name })), dirs };
    const walk = async (en, prefix) => {
      if (en.isFile) { const f = await new Promise((r, j) => en.file(r, j)); items.push({ file: f, rel: prefix + f.name }); return; }
      const rd = en.createReader(); let all = [];
      for (;;) { const batch = await new Promise((r, j) => rd.readEntries(r, j)); if (!batch.length) break; all = all.concat(batch); }
      if (!all.length) dirs.push(joinp(st.path, prefix + en.name));
      for (const c of all) await walk(c, prefix + en.name + "/");
    };
    for (const en of entries) await walk(en, "");
    return { items, dirs };
  }

  // ------------------------------------------------------------ settings / session
  function openSetup() {
    window.Settings.setup({
      defaultPw: st.defaultPw,
      account: (u) => { st.user = u; st.defaultPw = false; renderBanner(); },
      done: () => { loadStatus(); reload(); },
    });
  }
  function openSettings(tab) {
    window.Settings.open(tab, {
      reload: () => { loadStatus(); reload(); }, jobs: () => { st.xferOpen = true; pollJobs(); },
      saved: (S2, ch) => { applyTheme(S2.theme.value); loadStatus(); reload(); }, account: (u) => { st.user = u; st.defaultPw = false; renderBanner(); },
    });
  }
  async function pollJobs() {
    const d = await guard(() => api("/api/jobs")); if (!d) return;
    for (const j of d.jobs.filter((x) => x.state === "running" && !st.jobs.includes(x.id))) { st.jobs.push(j.id); trackJob(j); }
  }
  async function logout() { await guard(() => api("/api/logout", { method: "POST", body: {} })); location.hash = ""; location.reload(); }

  boot();
})();
