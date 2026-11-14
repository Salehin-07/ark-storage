/* Progress smoothing for the transfers panel. Pure functions (no DOM) so they can be unit-tested.
 *
 * Real progress arrives in jumps (a 16 MB chunk finishing, a server job moving from 20% to 66%). Showing it raw makes
 * the bar leap. Instead the bar eases toward the real value, never moves backwards, and creeps a little while
 * nothing new is reported, so it always looks alive without ever promising more than it knows. */
(function (root) {
  "use strict";
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  /** A meter for one transfer. `now` is a millisecond clock (performance.now()). */
  function meter(now) { return { target: 0, shown: 0, ceil: 0.03, moved: now, last: now, done: false }; }

  /** Report real progress (0..1). `headroom` is how far the bar may creep past it while waiting for the next report. */
  function report(m, v, now, headroom) {
    v = clamp(Number.isFinite(v) ? v : 0, 0, 1);
    if (v > m.target) { m.target = v; m.moved = now; }
    m.ceil = Math.min(0.99, m.target + (headroom === undefined ? 0.03 : headroom));
    return m;
  }

  function finish(m) { m.done = true; m.target = 1; m.ceil = 1; return m; }

  /** Advance one animation frame; returns the value to draw (0..1, monotonic). */
  function step(m, now) {
    const dt = clamp((now - m.last) / 1000, 0, 0.25);
    m.last = now;
    let goal = m.target;
    if (!m.done && m.ceil > m.target) {
      const idle = Math.max(0, now - m.moved) / 1000;
      goal = m.target + (m.ceil - m.target) * (1 - Math.exp(-idle / 6));   // slows to a crawl, stops at the ceiling
    }
    const k = 1 - Math.exp(-dt / (m.done ? 0.08 : 0.25));                  // ~0.25 s to cover a jump
    const next = m.shown + (goal - m.shown) * k;
    m.shown = Math.max(m.shown, Math.min(next, m.done ? 1 : 0.995));
    if (m.done && 1 - m.shown < 0.002) m.shown = 1;
    return m.shown;
  }

  /** Transfer speed over a sliding window, so one fast burst doesn't skew it for the rest of the upload. */
  function rateTracker(windowMs) { return { w: windowMs || 4000, s: [] }; }
  function sample(r, now, bytes) {
    r.s.push([now, bytes]);
    while (r.s.length > 2 && now - r.s[0][0] > r.w) r.s.shift();
  }
  function rate(r) {
    if (r.s.length < 2) return 0;
    const a = r.s[0], b = r.s[r.s.length - 1], dt = (b[0] - a[0]) / 1000;
    return dt > 0.2 ? Math.max(0, (b[1] - a[1]) / dt) : 0;
  }

  const api = { meter, report, finish, step, rateTracker, sample, rate };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.ArkProgress = api;
})(typeof window !== "undefined" ? window : globalThis);
