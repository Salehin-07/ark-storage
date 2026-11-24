// jsdom test of the share page script: text is highlighted with balanced per-line spans, images/binary fall back sensibly.
// Run: npm i --no-save jsdom && JSDOM_PATH=node_modules/jsdom node tests/share_test.js
const fs = require("fs"), path = require("path");
const { JSDOM } = require(process.env.JSDOM_PATH ? path.resolve(process.env.JSDOM_PATH) : "jsdom");
const root = path.join(__dirname, "..", "arkstorage", "static");
let fails = 0;
const ok = (c, m) => { if (!c) { fails++; console.log("FAIL:", m); } else console.log("ok  :", m); };
const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));

async function page(kind, ext, name, body, size) {
  const dom = new JSDOM(`<!doctype html><meta name="ark-version" content="t"><body><a id="sh-dl"></a><main id="sh-view" data-kind="${kind}" data-ext="${ext}" data-name="${name}" data-size="${size || (body ? body.length : 0)}" data-raw="/s/T/${name}?raw=1" data-convert="0"></main></body>`,
    { runScripts: "outside-only", url: "http://localhost/s/T/" + name, pretendToBeVisual: true });
  const w = dom.window;
  w.fetch = async () => ({ ok: true, status: 200, arrayBuffer: async () => new w.TextEncoder().encode(body).buffer });
  w.eval(fs.readFileSync(path.join(root, "hljs/highlight.bundle.js"), "utf8"));
  w.eval(fs.readFileSync(path.join(root, "share.js"), "utf8"));
  await tick();
  return w;
}
(async () => {
  let w = await page("text", "sh", "a.sh", "#!/bin/sh\n# comment\necho \"hi\"\nif true; then\n  echo x\nfi\n");
  let lines = [...w.document.querySelectorAll(".code .ln")];
  ok(lines.length === 7, "one line element per line (" + lines.length + ")");
  ok(w.document.querySelector(".code .hljs-comment"), "shell syntax is coloured");
  ok(lines.every((l) => l.innerHTML.split("<span").length === l.innerHTML.split("</span>").length), "spans are balanced on every line");
  ok(lines.map((l) => l.textContent).join("\n") === "#!/bin/sh\n# comment\necho \"hi\"\nif true; then\n  echo x\nfi\n", "text round-trips exactly");

  w = await page("text", "py", "a.py", 'x = """multi\nline <b>"""\nprint(x)\n');
  lines = [...w.document.querySelectorAll(".code .ln")];
  ok(lines.every((l) => l.innerHTML.split("<span").length === l.innerHTML.split("</span>").length), "multi-line strings keep spans balanced");
  ok(!w.document.querySelector(".code b"), "file content is never parsed as HTML");

  w = await page("text", "txt", "bin.txt", "ab\u0000cd");
  ok(w.document.querySelector(".sh-card a.btn"), "binary data falls back to a Download card");

  w = await page("image", "png", "p.png", "");
  ok(w.document.querySelector(".sh-img img").getAttribute("src") === "/s/T/p.png?raw=1", "image points at the raw URL");

  w = await page("archive", "zip", "z.zip", "");
  ok(/zip/i.test(w.document.querySelector(".sh-card .big").textContent), "unknown kinds get a Download card");
  if (fails) { console.log(fails + " failed"); process.exit(1); }
})();
