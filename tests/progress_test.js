// Unit tests for static/progress.js: the bar must glide, never go backwards, never stall, and never lie.
const assert = require("assert"), path = require("path");
const P = require(path.join(__dirname, "..", "arkstorage", "static", "progress.js"));
let n = 0;
const t = (name, fn) => { fn(); n++; console.log("ok  :", name); };
const frame = 16;
function run(m, ms, t0) { const out = []; for (let t = 0; t < ms; t += frame) out.push(P.step(m, t0 + t + frame)); return out; }

t("a jump from 20% to 66% is spread over many frames", () => {
  const m = P.meter(0); P.report(m, 0.2, 0); run(m, 3000, 0);
  P.report(m, 0.66, 3000, 0.03);
  const f = run(m, 1200, 3000);
  const steps = f.map((v, i) => v - (i ? f[i - 1] : 0.2)).slice(1);
  assert(Math.max(...steps) < 0.035, "largest single-frame move " + Math.max(...steps));
  assert(f.filter((v) => v > 0.25 && v < 0.6).length > 8, "should pass through the middle slowly");
  assert(f[f.length - 1] > 0.64, "should arrive near the real value");
});
t("never goes backwards, even if a lower value is reported", () => {
  const m = P.meter(0); P.report(m, 0.5, 0); run(m, 2000, 0); const before = m.shown;
  P.report(m, 0.3, 2000); const f = run(m, 500, 2000);
  assert(f.every((v) => v >= before - 1e-9));
});
t("keeps moving while nothing is reported, but never passes its ceiling", () => {
  const m = P.meter(0); P.report(m, 0.9, 0, 0.05); const f = run(m, 60000, 0);
  assert(f[f.length - 1] > 0.9 + 0.02, "should creep: " + f[f.length - 1]);
  assert(Math.max(...f) <= 0.95 + 1e-9, "must stay under target+headroom");
});
t("never shows 100% until finished, then reaches exactly 100%", () => {
  const m = P.meter(0); P.report(m, 0.99, 0, 0.5); run(m, 120000, 0);
  assert(m.shown < 1);
  P.finish(m); run(m, 1500, 120000); assert.strictEqual(m.shown, 1);
});
t("ignores NaN and out-of-range reports", () => {
  const m = P.meter(0); P.report(m, NaN, 0); P.report(m, 7, 1); assert(m.target <= 1 && m.target >= 0);
});
t("speed uses a sliding window", () => {
  const r = P.rateTracker(4000);
  for (let i = 0; i <= 10; i++) P.sample(r, i * 1000, i < 5 ? i * 10e6 : 40e6 + (i - 4) * 1e6);   // fast burst, then slow
  const v = P.rate(r);
  assert(v > 0.9e6 && v < 1.5e6, "recent speed expected near 1 MB/s, got " + v);
});
console.log("progress OK (" + n + " tests)");
