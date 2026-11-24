// jsdom smoke test: boots the real UI against a stubbed API, checks login, listing, selection, menus, viewer, editor, settings.
const fs = require("fs"), path = require("path");
const { JSDOM } = require(process.env.JSDOM_PATH ? path.resolve(process.env.JSDOM_PATH) : "jsdom");
const root = path.join(__dirname, "..", "arkstorage", "static");
let html = fs.readFileSync(path.join(root, "index.html"), "utf8").replace(/\{\{v\}\}/g, "t");
html = html.replace(/<script src="[^"]+"><\/script>/g, "");
const dom = new JSDOM(html, { runScripts: "outside-only", url: "http://localhost/", pretendToBeVisual: true });
const w = dom.window;
w.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
w.HTMLElement.prototype.scrollIntoView = () => {};
w.document.execCommand = () => false;
let authed = false, calls = [];
const files = [
  { name: "docs", path: "/docs", dir: true, size: 0, mtime: 0, kind: "dir", items: 2, enc: false, parts: 0, mime: "" },
  { name: "a.txt", path: "/a.txt", dir: false, size: 12, mtime: 1700000000, kind: "text", enc: false, parts: 1, mime: "text/plain" },
  { name: "pic.png", path: "/pic.png", dir: false, size: 2048, mtime: 1700000100, kind: "image", enc: true, parts: 1, mime: "image/png" },
  { name: "clip.mp4", path: "/clip.mp4", dir: false, size: 9e6, mtime: 1700000200, kind: "video", enc: false, parts: 1, mime: "video/mp4" },
  { name: "doc.pdf", path: "/doc.pdf", dir: false, size: 5e5, mtime: 1700000300, kind: "pdf", enc: false, parts: 1, mime: "application/pdf" },
  { name: "x.zip", path: "/x.zip", dir: false, size: 99, mtime: 1700000400, kind: "archive", enc: false, parts: 1, mime: "application/zip" },
];
let slow = 0;
const later = (o, ms) => new Promise((r) => setTimeout(() => r(o), ms));
const json = (o, s = 200) => Promise.resolve({ ok: s < 400, status: s, statusText: "", json: async () => o });
w.fetch = (url, init = {}) => {
  calls.push((init.method || "GET") + " " + url);
  const u = new URL(url, "http://localhost");
  switch (u.pathname) {
    case "/api/session": return json({ authed, user: authed ? "ark" : null, csrf: "tok", default_password: true, theme: "auto" });
    case "/api/login": authed = true; return json({ ok: true, csrf: "tok", user: "ark", default_password: true });
    case "/api/list": return json({ path: u.searchParams.get("path"), crumbs: [{ name: "Home", path: "/" }], entries: files, head: "abc1234" });
    case "/api/status": return json({ version: "1.0.0", problems: [], branch: "ark-data", private: false, repo: "me/r", repo_kb: 100, git: true, crypto: { enabled: false, passphrase_set: false, initialised: false }, stats: { files: 5, folders: 1, bytes: 123456, trash_items: 1 }, cache_bytes: 0 });
    case "/api/text": return json({ path: "/a.txt", content: "hello\nworld", size: 11, sha: "s1" });
    case "/api/settings": return json({ settings: { repo_url: { kind: "str", value: "me/r", source: "env", secret: false }, token: { kind: "str", value: "", set: true, source: "env", secret: true }, branch: { kind: "str", value: "ark-data", source: "default" }, encrypt: { kind: "bool", value: false, source: "default" }, passphrase: { kind: "str", value: "", set: false, secret: true, source: "default" }, chunk_mb: { kind: "int", value: 48, min: 1, max: 95, source: "default" }, stage_mb: { kind: "int", value: 48, min: 1, max: 95, source: "default" }, cache_mb: { kind: "int", value: 1024, min: 64, max: 99, source: "default" }, max_upload_mb: { kind: "int", value: 20480, min: 0, max: 99, source: "default" }, refresh_seconds: { kind: "int", value: 10, min: 0, max: 99, source: "default" }, trash: { kind: "bool", value: true, source: "default" }, show_hidden: { kind: "bool", value: false, source: "default" }, share_hours: { kind: "int", value: 24, min: 1, max: 99, source: "default" }, theme: { kind: "str", value: "auto", choices: ["auto", "light", "dark"], source: "default" }, session_hours: { kind: "int", value: 168, min: 1, max: 99, source: "default" }, commit_name: { kind: "str", value: "Ark", source: "default" }, commit_email: { kind: "str", value: "a@b.c", source: "default" } }, problems: [] });
    case "/api/mkdir": return later({ ok: true, status: 200, statusText: "", json: async () => ({ ok: true }) }, slow);
    case "/api/delete": return later({ ok: true, status: 200, statusText: "", json: async () => ({ ok: true, results: [{ path: "/a.txt", trash_id: "t1" }] }) }, slow);
    case "/api/trash": return json({ items: [{ id: "1", name: "old.txt", path: "/old.txt", dir: false, ts: 1700000000, size: 3 }] });
    default: return json({ ok: true });
  }
};
const load = (f) => w.eval(fs.readFileSync(path.join(root, f), "utf8"));
const errors = [];
w.addEventListener("error", (e) => errors.push(e.message));
["files-core.js", "hljs/highlight.bundle.js", "editor-core.js", "ui.js", "editor.js", "settings.js", "progress.js", "pdfview.js", "app.js"].forEach(load);
const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const q = (s) => w.document.querySelector(s), qa = (s) => [...w.document.querySelectorAll(s)];
let fails = 0;
const ok = (c, m) => { if (!c) { fails++; console.log("FAIL:", m); } else console.log("ok  :", m); };
(async () => {
  await tick(80);
  ok(!q("#login").hidden, "login screen shown when signed out");
  q("#lu").value = "ark"; q("#lp").value = "ark";
  q("#login-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  await tick(150);
  ok(!q("#app").hidden && q("#login").hidden, "app shown after login");
  ok(qa(".row").length === files.length, "rows rendered: " + qa(".row").length);
  ok(q("#banner") && !q("#banner").hidden && /default password/.test(q("#banner").textContent), "default-password banner");
  const rows = qa(".row");
  ok(!q("#top .brand-s") && !q("#top img"), "no logo or title in the top bar");
  ok(!q("#top .spacer"), "no spacer next to the search box (it takes all free width)");
  ok(q("#search").nextElementSibling && q("#search").nextElementSibling.tagName === "BUTTON", "search box is followed directly by the buttons");
  rows[1].click(); await tick();
  ok(rows[1].classList.contains("sel") && !q("#sel-bar").hidden, "click selects, action bar appears");
  rows[2].dispatchEvent(new w.MouseEvent("click", { bubbles: true, ctrlKey: true })); await tick();
  ok(qa(".row.sel").length === 2, "ctrl+click adds to selection");
  ok(!/null|undefined/.test(q("#sel-bar").textContent), "selection bar never prints null");
  rows[1].dispatchEvent(new w.MouseEvent("contextmenu", { bubbles: true })); await tick();
  ok(!!q(".menu") && /Delete/.test(q(".menu").textContent), "context menu");
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape" })); q(".menu") && q(".menu").remove();
  // 2.0.3: per-row "more" button, keyboard selection, type-ahead, menu keyboard navigation, sidebar toggle
  const key = (el, k, o = {}) => el.dispatchEvent(new w.KeyboardEvent("keydown", { key: k, bubbles: true, ...o }));
  ok(qa(".row .more").length === files.length && !!q(".head > span:last-child"), "every row has a more-actions button and the header has a matching empty cell");
  rows[0].focus(); key(rows[0], "ArrowDown"); await tick();
  ok(qa(".row.sel").length === 1 && rows[1].classList.contains("sel") && w.document.activeElement === rows[1], "ArrowDown moves focus and selection");
  key(rows[1], "ArrowDown", { shiftKey: true }); await tick();
  ok(qa(".row.sel").length === 2 && rows[2].classList.contains("sel"), "Shift+ArrowDown extends the selection");
  key(rows[2], "End"); await tick();
  ok(qa(".row.sel").length === 1 && rows[rows.length - 1].classList.contains("sel"), "End selects the last item");
  key(rows[rows.length - 1], "p"); await tick();
  ok(rows[4].classList.contains("sel") && w.document.activeElement === rows[4], "typing a letter jumps to the next item starting with it");
  rows[4].querySelector(".more").click(); await tick();
  ok(!!q(".menu") && /Rename/.test(q(".menu").textContent) && rows[4].classList.contains("sel"), "more button selects the row and opens its menu");
  ok(/F2/.test(q(".menu").textContent), "menu items show keyboard shortcuts");
  key(w.document.body, "ArrowDown"); await tick();
  ok(w.document.activeElement && w.document.activeElement.closest(".menu") && w.document.activeElement.tagName === "BUTTON", "ArrowDown moves focus into the menu");
  key(w.document.body, "Escape"); await tick();
  ok(!q(".menu"), "Escape closes the menu");
  q("#menu-btn").click(); await tick();
  ok(q("#app").classList.contains("side-off"), "sidebar button collapses the sidebar on desktop");
  q("#menu-btn").click(); await tick();
  ok(!q("#app").classList.contains("side-off"), "and expands it again");
  qa(".row").find((r) => r.dataset.path === "/pic.png").dispatchEvent(new w.MouseEvent("dblclick", { bubbles: true })); await tick();
  ok(!!q("#viewer") && !!q("#vstage .vload"), "double click on an image opens the viewer with a loading indicator (never a blank screen)");
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "ArrowRight" })); await tick();
  ok(!!q("#vstage video"), "arrow key goes to next media (video)");
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "ArrowRight" })); await tick();
  ok(!!q("#vstage .pvhost") && !q("#vstage iframe"), "next is the PDF, drawn by the built-in reader (no iframe)");
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape" })); await tick();
  ok(!q("#viewer"), "Esc closes the viewer");
  qa(".row").find((r) => r.dataset.path === "/a.txt").dispatchEvent(new w.MouseEvent("dblclick", { bubbles: true })); await tick(100);
  ok(!!q("#editor") && q("#ta").value === "hello\nworld", "text file opens in the editor");
  const ta = q("#ta"); ta.setSelectionRange(5, 5);
  ta.dispatchEvent(new w.KeyboardEvent("keydown", { key: "(", bubbles: true, cancelable: true }));
  ok(true, "editor keydown handled without throwing");
  q("#editor .ebar > .btn:last-child").click(); await tick(); q(".scrim") && q(".scrim").remove(); q("#editor") && q("#editor").remove();
  // ---- instant feedback: the new folder is on screen before GitHub has answered
  slow = 400;
  q("#fab").click(); await tick();
  const items = qa(".menu button"); items.find((b) => /New folder/.test(b.textContent)).click(); await tick(60);
  q(".dlg input").value = "Photos"; qa(".dlg footer .primary")[0].click(); await tick(40);
  const newRow = qa("#list .row").find((r) => r.dataset.path === "/Photos");
  ok(!!newRow && newRow.classList.contains("pending"), "new folder appears immediately, marked as saving (API still pending)");
  ok(q("#sync").classList.contains("on") && /Saving/.test(q("#status").textContent), "sync bar and status show it is saving");
  await tick(700);
  ok(!q("#sync").classList.contains("on"), "sync bar clears when GitHub has answered");
  // ---- trash is instant and undoable (no confirmation dialog)
  const before = qa("#list .row").length;
  qa("#list .row").find((r) => r.dataset.path === "/a.txt").click(); await tick();
  const delBtn = qa("#sel-bar button").find((b) => /Delete/.test(b.textContent)); delBtn.click(); await tick(40);
  ok(!q(".dlg"), "moving to trash asks nothing");
  ok(qa("#list .row").length === before - 1 && !qa("#list .row").some((r) => r.dataset.path === "/a.txt"), "item leaves the list immediately");
  await tick(700);
  ok(!!q(".toast-act") && /Undo/.test(q(".toast-act").textContent), "an Undo button is offered after the move");
  slow = 0;
  q("#side .nav:nth-child(3)").click(); await tick(150);
  ok(!!q(".dlg") && /Repository/.test(q(".dlg").textContent), "settings dialog opens");
  qa(".set-nav button").forEach((b) => b.click());
  ok(qa(".set-nav button").length === 7, "all settings tabs render without errors");
  q(".scrim").remove();
  q("#side .nav:nth-child(2)").click(); await tick(150);
  ok(/old.txt/.test(q("#list").textContent), "trash view lists items");
  ok(!/null|undefined/.test(q("#list").textContent), "trash view never prints null");
  w.Settings.setup({ defaultPw: true }); await tick(150);
  ok(!!q(".dlg") && /Welcome to Ark Storage/.test(q(".dlg").textContent) && !!q(".dlg input[type=password]"), "setup wizard opens with repository + token fields");
  q(".scrim").remove();
  ok(qa("#list .row .chk").length >= 1, "trash rows can be selected");
  ok(errors.length === 0, "no script errors" + (errors.length ? ": " + errors.join("; ") : ""));
  console.log(fails ? fails + " FAILED" : "UI smoke OK");
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
