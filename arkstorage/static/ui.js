/* Small UI toolkit: element builder, icons, dialogs, menus, toasts, API client. No dependencies. */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";

  // Browsers print null/undefined/false passed to these as the text "null"; drop them instead.
  for (const m of ["replaceChildren", "append", "prepend"]) {
    const orig = Element.prototype[m];
    Element.prototype[m] = function (...k) { return orig.apply(this, k.flat().filter((x) => x !== null && x !== undefined && x !== false)); };
  }

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k in el && k !== "list" && k !== "type" && k !== "form") { try { el[k] = v; } catch (e) { el.setAttribute(k, v); } }
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  const P = {
    folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
    file: "M6 3h8l5 5v13H6zM14 3v5h5", image: "M4 5h16v14H4zM8 10a1.5 1.5 0 1 0 0-.01M4 17l5-5 4 4 3-3 4 4",
    video: "M3 6h12v12H3zM15 10l6-3v10l-6-3z", audio: "M9 18V6l10-2v12M9 18a3 3 0 1 1-3-3 3 3 0 0 1 3 3zM19 16a3 3 0 1 1-3-3 3 3 0 0 1 3 3z",
    pdf: "M6 3h8l5 5v13H6zM14 3v5h5M9 15h6M9 18h4", archive: "M5 4h14v16H5zM12 4v3M12 9v2M12 13v2M10 17h4",
    code: "M8 8l-4 4 4 4M16 8l4 4-4 4M13.5 6l-3 12", upload: "M12 16V4M7 9l5-5 5 5M4 20h16", download: "M12 4v12M7 11l5 5 5-5M4 20h16",
    trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6", edit: "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
    copy: "M9 9h11v11H9zM5 15V5h10", cut: "M6 6a3 3 0 1 0 0 .01M6 18a3 3 0 1 0 0 .01M8.5 8l11 10M8.5 16l11-10",
    paste: "M9 4h6v3H9zM7 5H5v16h14V5h-2M9 12h6M9 16h6", plus: "M12 5v14M5 12h14", search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4",
    grid: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z", list: "M4 6h16M4 12h16M4 18h16",
    settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.7a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.5a7 7 0 0 0 0 2.4l-2 1.5 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.7a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z",
    logout: "M9 4H5v16h4M15 8l4 4-4 4M19 12H9", link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
    more: "M5 12h.01M12 12h.01M19 12h.01", x: "M6 6l12 12M18 6L6 18", check: "M5 12l5 5 9-10", lock: "M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3",
    refresh: "M20 5v5h-5M4 19v-5h5M19 10a7 7 0 0 0-12-3M5 14a7 7 0 0 0 12 3", info: "M12 11v6M12 7.5v.01M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
    chev: "M9 6l6 6-6 6", menu: "M4 6h16M4 12h16M4 18h16", home: "M4 11l8-7 8 7M6 10v10h12V10", folderplus: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM12 10v5M9.5 12.5h5",
    fileplus: "M6 3h8l5 5v13H6zM14 3v5h5M12 12v6M9 15h6", restore: "M4 12a8 8 0 1 0 3-6.2M4 4v5h5", sun: "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5",
    moon: "M20 14A8 8 0 0 1 10 4a8 8 0 1 0 10 10z", up: "M12 19V5M6 11l6-6 6 6", select: "M4 4h16v16H4zM8 12l3 3 5-6", zip: "M5 4h14v16H5zM12 4v3M12 9v2M12 13v2M10 17h4",
    play: "M7 4l13 8-13 8z", save: "M5 4h11l3 3v13H5zM8 4v5h7V4M8 20v-6h8v6", find: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4", wrap: "M4 6h16M4 12h12a3 3 0 0 1 0 6h-4M14 15l-2 3 2 3M4 18h4",
  };
  function icon(name, cls) {
    const s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("class", "i" + (cls ? " " + cls : ""));
    s.setAttribute("aria-hidden", "true");
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", P[name] || P.file);
    s.append(p);
    return s;
  }

  // ---- formatting ---------------------------------------------------------
  const fmtBytes = (n) => window.FilesCore.fmtBytes(n);
  function fmtDate(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000), now = new Date();
    const opts = d.getFullYear() === now.getFullYear() ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" } : { year: "numeric", month: "short", day: "numeric" };
    return d.toLocaleString(undefined, opts);
  }

  // ---- toasts -------------------------------------------------------------
  function toast(msg, kind, ms, action) {
    const el = h("div", { class: "toast" + (kind === "bad" ? " bad" : ""), role: "status" }, h("span", { class: "msg" }, msg));
    if (action) el.append(h("button", { class: "toast-act", type: "button", onclick: () => { el.remove(); action.run(); } }, action.label));
    document.getElementById("toasts").append(el);
    setTimeout(() => el.remove(), ms || (kind === "bad" ? 6000 : 3200));
  }

  // ---- modal dialogs (never the browser's own popups) ---------------------
  function modal(o) {
    return new Promise((resolve) => {
      const prevFocus = document.activeElement;
      const body = h("div", { class: "bd" }, o.body || "");
      const foot = h("footer");
      const dlg = h("div", { class: "dlg" + (o.wide ? " wide" : ""), role: "dialog", "aria-modal": "true", "aria-label": o.title || "" },
        o.title ? h("header", null, o.title) : null, o.nav || null, body, foot);
      const scrim = h("div", { class: "scrim" }, dlg);
      let closed = false, release = () => {};
      const close = (v) => {
        if (closed) return true;
        closed = true;
        release();
        scrim.remove();
        document.removeEventListener("keydown", onKey, true);
        if (prevFocus && prevFocus.focus) prevFocus.focus();
        resolve(v);
        if (o.onClose) o.onClose(v);
        return true;
      };
      (o.buttons || [{ label: "Close", value: null }]).forEach((b) => {
        foot.append(h("button", { class: "btn " + (b.kind || ""), type: "button", onclick: () => {
          if (b.validate && !b.validate()) return;
          close(b.value);
        } }, b.label));
      });
      if (o.footExtra) foot.prepend(o.footExtra);
      const onKey = (e) => {
        if (e.key === "Escape" && o.dismiss !== false) { e.stopPropagation(); close(undefined); }
        else if (e.key === "Enter" && o.enter && e.target.tagName !== "TEXTAREA" && e.target.tagName !== "BUTTON") {
          e.preventDefault(); const b = (o.buttons || []).find((x) => x.kind && x.kind.includes("primary")); if (b && (!b.validate || b.validate())) close(b.value);
        }
      };
      document.addEventListener("keydown", onKey, true);
      scrim.addEventListener("mousedown", (e) => { if (e.target === scrim && o.dismiss !== false) close(undefined); });
      document.body.append(scrim);
      release = pushOverlay(() => (o.dismiss === false ? false : close(undefined)));
      const f = dlg.querySelector("[autofocus], input, select, textarea") || foot.querySelector(".primary") || foot.querySelector("button");
      if (f) setTimeout(() => { f.focus(); if (f.select && f.tagName === "INPUT" && o.selectStem) f.select(); }, 30);
      if (o.ready) o.ready(dlg, close);
    });
  }

  async function ask(title, message, opts) {
    opts = opts || {};
    const v = await modal({ title, body: h("p", { style: { margin: "6px 0" } }, message), enter: true,
      buttons: [{ label: opts.cancel || "Cancel", value: false }, { label: opts.ok || "OK", kind: opts.danger ? "danger solid" : "primary", value: true }] });
    return v === true;
  }

  async function prompt(title, value, opts) {
    opts = opts || {};
    const inp = h("input", { class: "inp", value: value || "", autofocus: true, spellcheck: false, autocapitalize: "none" });
    const err = h("div", { class: "err" });
    const body = h("div", null, opts.label ? h("div", { class: "lbl", style: { marginBottom: "6px" } }, opts.label) : null, inp, opts.hint ? h("div", { class: "hint", style: { marginTop: "6px" } }, opts.hint) : null, err);
    const v = await modal({ title, body, enter: true, buttons: [{ label: "Cancel", value: null },
      { label: opts.ok || "OK", kind: "primary", value: "ok", validate: () => { if (!inp.value.trim()) { err.textContent = "Please enter a name"; return false; } return true; } }],
      ready: (d) => { const stem = (value || "").lastIndexOf("."); inp.focus(); inp.setSelectionRange(0, opts.stem && stem > 0 ? stem : inp.value.length); } });
    return v === "ok" ? inp.value.trim() : null;
  }

  async function choose(title, message, options, cancel) {
    return modal({ title, body: h("p", { style: { margin: "6px 0" } }, message),
      buttons: [{ label: cancel || "Cancel", value: null }, ...options] });
  }

  // ---- back-button aware overlays -----------------------------------------
  // Viewer, editor and dialogs each take one history entry, so the Back button closes the top overlay
  // instead of leaving the app (and wiping everything on screen).
  const overlays = [], skips = [], navFns = [];
  const idle = () => new Promise((r) => { let n = 0; const t = setInterval(() => { if (!skips.length || ++n > 40) { clearInterval(t); r(); } }, 20); });
  // history.back() calls are issued one at a time: two quick ones (a dialog and the editor closing together) can be merged by the browser.
  let backQ = 0, backBusy = false;
  function drainBack() {
    if (backBusy || !backQ) return;
    backQ--; backBusy = true;
    const tok = {}; let done = false;
    tok.fin = () => { if (done) return; done = true; const k = skips.indexOf(tok); if (k >= 0) skips.splice(k, 1); backBusy = false; drainBack(); };
    skips.push(tok); setTimeout(tok.fin, 600);
    history.back();
  }
  function pushOverlay(close) {
    const e = { close, gone: false };
    overlays.push(e);
    try { history.pushState({ ark: 1 }, "", location.href); } catch (x) { /* history unavailable */ }
    return function release() {
      if (e.gone) return;
      e.gone = true;
      const i = overlays.indexOf(e), top = i === overlays.length - 1;
      if (i >= 0) overlays.splice(i, 1);
      if (top) { backQ++; drainBack(); }
    };
  }
  window.addEventListener("popstate", (ev) => {
    if (skips.length) { const t = skips.shift(); t.fin(); return; }
    const top = overlays[overlays.length - 1];
    if (top) {
      top.gone = true; overlays.pop();
      Promise.resolve(top.close(true)).then(async (closed) => {
        if (closed !== false) return;
        await idle();                                  // the dialog's own Back must land before we put our entry back
        top.gone = false; overlays.push(top);
        try { history.pushState({ ark: 1 }, "", location.href); } catch (x) { /* ignore */ }
      });
      return;
    }
    navFns.forEach((f) => f(ev));
  });
  const onNav = (fn) => navFns.push(fn);

  // ---- context menu -------------------------------------------------------
  let openMenu = null, menuOff = null;
  function closeMenu() {
    if (menuOff) { menuOff(); menuOff = null; }
    if (openMenu) { openMenu.remove(); openMenu = null; }
  }
  function popMenu(x, y, items) {
    closeMenu();
    const m = h("div", { class: "menu", role: "menu" });
    items.forEach((it) => {
      if (it === "-") { m.append(h("hr")); return; }
      if (!it) return;
      m.append(h("button", { type: "button", role: "menuitem", class: it.danger ? "danger" : "", onclick: () => { closeMenu(); it.run(); } }, it.icon ? icon(it.icon) : null, it.label));
    });
    document.body.append(m);
    const r = m.getBoundingClientRect();
    m.style.left = Math.max(8, Math.min(x, innerWidth - r.width - 8)) + "px";
    m.style.top = Math.max(8, Math.min(y, innerHeight - r.height - 8)) + "px";
    openMenu = m;
    // Every listener is removed together with the menu. (They used to linger, and an old one would close the *next*
    // menu on pointerdown, before its button could be clicked: buttons looked frozen.)
    const off = (e) => { if (!m.contains(e.target)) closeMenu(); };
    const esc = (e) => { if (e.key === "Escape") closeMenu(); };
    const t = setTimeout(() => document.addEventListener("pointerdown", off, true), 0);
    document.addEventListener("keydown", esc, true);
    window.addEventListener("blur", closeMenu);
    menuOff = () => { clearTimeout(t); document.removeEventListener("pointerdown", off, true); document.removeEventListener("keydown", esc, true); window.removeEventListener("blur", closeMenu); };
  }

  // ---- API ----------------------------------------------------------------
  const S = { csrf: "" };
  async function api(path, o) {
    o = o || {};
    const method = o.method || "GET";
    const init = { method, credentials: "same-origin", headers: {} };
    if (method !== "GET") init.headers["X-CSRF-Token"] = S.csrf;
    if (o.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(o.body); }
    let r;
    try { r = await fetch(path, init); } catch (e) { throw Object.assign(new Error("Can't reach the server. Check your connection."), { network: true }); }
    let data = null;
    try { data = await r.json(); } catch (e) { /* not json */ }
    if (r.status === 401 && window.Ark && window.Ark.onLogout) window.Ark.onLogout();
    if (!r.ok) throw Object.assign(new Error((data && data.error) || r.statusText || "Request failed"), { status: r.status, data });
    return data;
  }
  const enc = (p) => encodeURIComponent(p);

  window.UI = { h, icon, fmtBytes, fmtDate, toast, modal, ask, prompt, choose, popMenu, closeMenu, api, S, enc, pushOverlay, onNav };
})();
