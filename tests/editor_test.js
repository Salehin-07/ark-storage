// jsdom test of the code editor (typing, auto-indent, auto-pairs, colours, line numbers), the Back button and menus.
// Run: npm i --no-save jsdom && JSDOM_PATH=node_modules/jsdom node tests/editor_test.js
const fs = require("fs"), path = require("path");
const { JSDOM } = require(process.env.JSDOM_PATH ? path.resolve(process.env.JSDOM_PATH) : "jsdom");
const root = path.join(__dirname, "..", "arkstorage", "static");
const dom = new JSDOM('<!doctype html><body><div id="toasts"></div></body>', { runScripts: "outside-only", url: "http://localhost/browse/x", pretendToBeVisual: true });
const w = dom.window;
let FILE = { content: "", sha: "s1" };
w.fetch = async (url) => ({ ok: true, status: 200, statusText: "OK", json: async () => (String(url).startsWith("/api/text") ? FILE : {}) });
for (const f of ["files-core.js", "hljs/highlight.bundle.js", "editor-core.js", "ui.js", "editor.js"]) w.eval(fs.readFileSync(path.join(root, f), "utf8"));
const errors = []; w.addEventListener("error", (e) => errors.push(e.message));
const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));
const q = (s) => w.document.querySelector(s), qa = (s) => [...w.document.querySelectorAll(s)];
let fails = 0;
const ok = (c, m) => { if (!c) { fails++; console.log("FAIL:", m); } else console.log("ok  :", m); };

// Types like a phone/desktop keyboard: a cancelable beforeinput first, the browser's own insertion only if nobody cancelled it.
function beforeinput(ta, inputType, data) {
  const ev = new w.InputEvent("beforeinput", { inputType, data: data === undefined ? null : data, bubbles: true, cancelable: true });
  const notCancelled = ta.dispatchEvent(ev);
  if (notCancelled) {
    const s = ta.selectionStart, e = ta.selectionEnd, v = ta.value;
    if (inputType === "insertText") ta.setRangeText(data, s, e, "end");
    else if (inputType === "insertLineBreak") ta.setRangeText("\n", s, e, "end");
    else if (inputType === "deleteContentBackward") { if (s === e) ta.setRangeText("", Math.max(0, s - 1), e, "end"); else ta.setRangeText("", s, e, "end"); }
    ta.dispatchEvent(new w.InputEvent("input", { inputType, data, bubbles: true }));
  }
}
const typeText = (ta, str) => { for (const ch of str) beforeinput(ta, ch === "\n" ? "insertLineBreak" : "insertText", ch === "\n" ? undefined : ch); };
async function openFile(name, content) {
  FILE = { content, sha: "s" + Math.random() };
  const p = w.Editor.open("/" + name, name, () => {}); await p; await tick();
  return q("#ta");
}
const closeEditor = async () => { const b = q("#editor .ebar > .btn:last-child"); b.click(); await tick(); const m = q(".scrim"); if (m) { [...m.querySelectorAll("button")].find((x) => /Discard/.test(x.textContent)).click(); await tick(); } };

(async () => {
  // ---- indentation (Python) -------------------------------------------------
  let ta = await openFile("a.py", "");
  typeText(ta, "def f(x):\nreturn x\n");
  ok(ta.value === "def f(x):\n    return x\n", "Python: Enter after ':' indents; value was " + JSON.stringify(ta.value));
  ok(/^ +$/.test(ta.value.split("\n")[2] || " "), "Python: Enter after return keeps/dedents correctly");
  ok(/Spaces: 4/.test(q(".estat-btn").textContent), "status bar shows the indentation");
  ta.setSelectionRange(ta.value.length, ta.value.length); ta.value = "a\nb"; ta.setSelectionRange(0, 3);
  ta.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true }));
  ok(ta.value === "    a\n    b", "Tab indents the whole selection");
  ta.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true }));
  ok(ta.value === "a\nb", "Shift+Tab outdents it");
  await closeEditor();

  // ---- indentation detected from the file (tabs / 2 spaces) -------------------
  ta = await openFile("b.js", "function a() {\n  if (x) {\n    y();\n  }\n}\n");
  ta.setSelectionRange(ta.value.length, ta.value.length); typeText(ta, "if (z) {\nq();");
  ok(ta.value.endsWith("if (z) {\n  q();\n}"), "JS: braces open an indented block and Enter between {} splits it; value " + JSON.stringify(ta.value.slice(-24)));
  await closeEditor();

  // ---- auto-closing pairs ------------------------------------------------------
  ta = await openFile("c.js", "");
  typeText(ta, "foo(");
  ok(ta.value === "foo()" && ta.selectionStart === 4, "( inserts the closing )");
  typeText(ta, ")");
  ok(ta.value === "foo()" && ta.selectionStart === 5, "typing ) steps over the existing )");
  typeText(ta, ' = "');
  ok(ta.value === 'foo() = ""' && ta.selectionStart === 9, 'a quote inserts the closing quote');
  typeText(ta, "hi");
  typeText(ta, '"');
  ok(ta.value === 'foo() = "hi"' && ta.selectionStart === 12, "closing quote is typed over");
  ta.value = ""; ta.setSelectionRange(0, 0); typeText(ta, "[{(");
  ok(ta.value === "[{()}]", "nested pairs: " + ta.value);
  beforeinput(ta, "deleteContentBackward");
  ok(ta.value === "[{}]", "Backspace removes an empty pair");
  ta.value = "let s = ab"; ta.setSelectionRange(8, 10); typeText(ta, "'");
  ok(ta.value === "let s = 'ab'", "typing a quote around a selection wraps it");
  ta.value = ""; ta.setSelectionRange(0, 0); typeText(ta, "{\n");
  ok(ta.value === "{\n  \n}" && ta.selectionStart === 4, "Enter inside {} opens an indented line with } below: " + JSON.stringify(ta.value));
  await closeEditor();
  ta = await openFile("d.txt", ""); typeText(ta, "don't"); ok(ta.value === "don't", "an apostrophe in plain text is not paired");
  await closeEditor();

  // ---- phone keyboards: composition input that can't be cancelled ---------------------
  ta = await openFile("e.js", "");
  ta.setRangeText("(", 0, 0, "end");
  ta.dispatchEvent(new w.InputEvent("beforeinput", { inputType: "insertCompositionText", data: "(", bubbles: true }));
  ta.dispatchEvent(new w.InputEvent("input", { inputType: "insertCompositionText", data: "(", bubbles: true }));
  ok(ta.value === "()" && ta.selectionStart === 1, "( typed as composition text is still auto-closed: " + JSON.stringify(ta.value));
  await closeEditor();

  // ---- colours, line numbers ---------------------------------------------------------
  ta = await openFile("f.py", "# note\ndef go(a):\n    return \"x\" + 1\n");
  ok(qa("#hl .hljs-keyword").length >= 2 && qa("#hl .hljs-comment").length === 1 && qa("#hl .hljs-string").length === 1, "Python is coloured (keywords, comment, string)");
  ok(qa("#hl .l").length === 4 && qa("#gutter .n").length === 4, "one coloured row and one line number per line");
  ok(/color:\s*transparent/.test(fs.readFileSync(path.join(root, "style.css"), "utf8").match(/\n#ta \{[^}]*\}/)[0]), "textarea text is transparent over the colour layer");
  typeText(ta, "\nclass A:\n");
  ok(qa("#gutter .n").length === ta.value.split("\n").length, "line numbers follow typing");
  w.document.querySelector(".estat-sel").value = "text"; w.document.querySelector(".estat-sel").dispatchEvent(new w.Event("change"));
  ok(qa("#hl .hljs-keyword").length === 0, "switching the language to plain text removes colours");
  await closeEditor();
  ta = await openFile("g.html", "<!-- a\nb --><div class=\"x\">hi</div>\n<script>let a = 1;</script>");
  ok(qa("#hl .l").length === 3 && qa("#hl .hljs-comment").length === 2, "multi-line comment keeps its colour on every row");
  await closeEditor();
  const lines = w.Editor.splitLines('<span class="a">x\ny</span>z\n\nq');
  ok(JSON.stringify(lines) === JSON.stringify(['<span class="a">x</span>', '<span class="a">y</span>z', "", "q"]), "splitLines re-opens spans across rows");

  // ---- Back button closes the editor instead of leaving the app ----------------------
  ta = await openFile("h.js", "x");
  ok(!!(w.history.state && w.history.state.ark), "opening the editor adds a history entry of its own");
  w.history.back(); await tick(80);
  ok(!q("#editor"), "Back closes the editor");
  ok(w.location.pathname === "/browse/x", "…and stays on the same page");
  ta = await openFile("i.js", "x"); typeText(ta, "y"); w.history.back(); await tick(120);
  ok(!!q("#editor") && !!q(".scrim"), "Back with unsaved changes asks first, the editor stays");
  [...q(".scrim").querySelectorAll("button")].find((x) => /Cancel/.test(x.textContent)).click(); await tick(150);
  ok(!!q("#editor") && !q(".scrim"), "Cancel keeps the editor open");
  w.history.back(); await tick(120);
  [...q(".scrim").querySelectorAll("button")].find((x) => /Discard/.test(x.textContent)).click(); await tick(150);
  ok(!q("#editor") && !q(".scrim"), "Back again then Discard closes it");
  const m = w.UI.modal({ title: "t", body: "b" }); await tick();
  ok(!!q(".scrim"), "dialog open"); w.history.back(); await tick(80);
  ok(!q(".scrim"), "Back closes a dialog"); await m;

  // ---- menus: opening a second menu must not be closed by the first one's leftovers ------------
  let ran = 0;
  w.UI.popMenu(10, 10, [{ label: "One", run: () => { ran++; } }]); await tick(10);
  q(".menu button").click(); await tick();
  ok(ran === 1 && !q(".menu"), "menu item runs and closes the menu");
  w.UI.popMenu(10, 10, [{ label: "Two", run: () => { ran++; } }]); await tick(10);
  const btn2 = q(".menu button");
  btn2.dispatchEvent(new w.Event("pointerdown", { bubbles: true }));
  ok(!!q(".menu"), "pointerdown inside a new menu does not close it");
  btn2.click(); await tick(); ok(ran === 2, "second menu's button works");
  w.UI.popMenu(10, 10, [{ label: "Three", run: () => {} }]); await tick(10);
  w.document.body.dispatchEvent(new w.Event("pointerdown", { bubbles: true }));
  ok(!q(".menu"), "pointerdown outside closes the menu");

  ok(errors.length === 0, "no script errors" + (errors.length ? ": " + errors.join("; ") : ""));
  console.log(fails ? `${fails} FAILED` : "editor tests OK");
  process.exit(fails ? 1 : 0);
})();
