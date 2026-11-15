// Pure helpers of the PDF viewer: which page to draw next, scale, memory release.
const assert = require("assert");
const { fitScale, pickNext, shouldRelease } = require("../arkstorage/static/pdfview.js");

// page 1 is drawn first on open
assert.strictEqual(pickNext([1, 2], 1, new Set(), 100), 1);
// the page the reader is looking at wins over earlier visible ones; nearest to focus first
assert.strictEqual(pickNext([40, 41, 42], 41, new Set(), 100), 41);
assert.strictEqual(pickNext([40, 41, 42], 41, new Set([41]), 100), 40);
// everything visible is drawn: warm the next page, then the previous one
assert.strictEqual(pickNext([40, 41], 41, new Set([40, 41]), 100), 42);
assert.strictEqual(pickNext([40, 41], 41, new Set([40, 41, 42]), 100), 39);
// nothing left
assert.strictEqual(pickNext([1], 1, new Set([1, 2]), 2), 0);
assert.strictEqual(pickNext([], 1, new Set([1, 2]), 2), 0);
// memory: far pages are released, near ones kept
assert.strictEqual(shouldRelease(50, 10, 6), true);
assert.strictEqual(shouldRelease(14, 10, 6), false);
// fit to width, with zoom, never absurdly small
assert.strictEqual(Math.round(fitScale(624, 600, 1, 24) * 1000), 1000);
assert.strictEqual(Math.round(fitScale(624, 600, 2, 24) * 1000), 2000);
assert.ok(fitScale(10, 600, 1, 24) > 0);
console.log("pdfview tests passed");
