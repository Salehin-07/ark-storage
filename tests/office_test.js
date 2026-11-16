// Unit tests for the pure helpers of the Office viewer (static/office.js).
const path = require("path"), assert = require("assert");
const O = require(path.join(__dirname, "..", "arkstorage", "static", "office.js"));
assert.strictEqual(O.familyOf("DOCX"), "word"); assert.strictEqual(O.familyOf("xls"), "sheet");
assert.strictEqual(O.familyOf("pptx"), "slides"); assert.strictEqual(O.familyOf("doc"), "convert");
assert.strictEqual(O.familyOf("txt"), null); assert.strictEqual(O.extOf("a.b.XLSX"), "xlsx"); assert.strictEqual(O.extOf("noext"), "");
assert.deepStrictEqual([0, 25, 26, 27, 701, 702].map(O.colName), ["A", "Z", "AA", "AB", "ZZ", "AAA"]);
let L = O.layout(400, 800, 816, 1);
assert.ok(Math.abs(L.k - 400 / 816) < 1e-9 && L.width === 816 && L.percent === 49);       // narrow screen: shrink to fit
L = O.layout(1600, 900, 816, 1); assert.strictEqual(L.k, 1); assert.strictEqual(L.width, 1600);   // never enlarged past 100% at fit
L = O.layout(1600, 900, 816, 2); assert.strictEqual(L.k, 2); assert.strictEqual(L.height, 450);
assert.strictEqual(O.layout(100, 100, 1e6, 0.01).k, 0.2);
const m = O.mergeMap([{ s: { r: 0, c: 0 }, e: { r: 1, c: 2 } }]);
assert.deepStrictEqual(m.get("0:0"), { span: [2, 3] }); assert.deepStrictEqual(m.get("1:2"), { skip: true }); assert.strictEqual(m.get("2:0"), undefined);
try {
  const { JSDOM } = require(process.env.JSDOM_PATH ? path.resolve(process.env.JSDOM_PATH) : "jsdom");
  const d = new JSDOM('<div><script>x()</script><a href="javascript:alert(1)" onclick="x()">l</a><a href="https://e.com">ok</a><img src=x onerror=y><iframe src=z></iframe></div>').window.document.body;
  O.sanitize(d);
  const h = d.innerHTML;
  assert.ok(!/script|onclick|onerror|iframe|javascript:/i.test(h), h); assert.ok(/href="https:\/\/e\.com"/.test(h) && /noopener/.test(h));
} catch (e) { if (e.code !== "MODULE_NOT_FOUND") throw e; console.log("jsdom missing: sanitize test skipped"); }
console.log("office_test ok");
