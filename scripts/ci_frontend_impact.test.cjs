const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { analyze, selectImpact } = require("./ci_frontend_impact.cjs");
const root = path.resolve(__dirname, "..");
const component = "frontend/src/components/Trend.tsx";
const ledger = "frontend/src/pages/Ledger/index.tsx";
const spec = "frontend/tests/visual/runtime-compat.spec.ts";
const mapping = { browser_smoke: ["frontend/tests/a11y/a11y.spec.ts"], browser: { [ledger]: [spec] } };
function tree(extra = {}) {
  return new Map(Object.entries({
    [component]: "export default function Trend() { return null; }",
    [ledger]: 'import Trend from "@/components/Trend"; export default Trend;',
    "frontend/src/App.tsx": 'import Page from "./pages/Ledger"; export default Page;',
    "frontend/src/main.tsx": 'import App from "./App"; export default App;',
    ...extra,
  }));
}
test("component impact reaches its page and retains smoke", () => {
  const result = analyze(root, [component], [tree()], mapping);
  assert.equal(result.mode, "selected");
  assert.deepEqual(result.tests, [mapping.browser_smoke[0], spec]);
  assert.deepEqual(result.affected_pages, [ledger]);
});
test("base/head dependency union retains removed import edges", () => {
  const head = tree({ [ledger]: "export default function Page() { return null; }" });
  assert.equal(analyze(root, [component], [head, tree()], mapping).mode, "selected");
});
test("an unknown affected page forces the whole suite", () => {
  const files = tree({ "frontend/src/pages/Unknown.tsx": 'import Trend from "../components/Trend"; export default Trend;' });
  files.set("frontend/src/pages/Unknown.tsx", 'import Trend from "@/components/Trend"; export default Trend;');
  assert.equal(analyze(root, [component], [files], mapping).mode, "full");
});
test("a shared shell path cannot be mistaken for a page-only dependency", () => {
  const files = tree({ "frontend/src/App.tsx": 'import Trend from "./components/Trend"; import Page from "./pages/Ledger"; export default Page;' });
  assert.equal(analyze(root, [component], [files], mapping).mode, "full");
});
test("new/deleted files require full coverage", () => {
  const before = tree({ [ledger]: "export default function Page() { return null; }" }); before.delete(component);
  assert.equal(analyze(root, [component], [tree(), before], mapping).mode, "full");
});
test("a source without a known owner cannot skip tests", () => {
  const files = tree({ "frontend/src/lib/unowned.ts": "export const x = 1;" });
  assert.equal(analyze(root, ["frontend/src/lib/unowned.ts"], [files], mapping).mode, "full");
});
test("nonliteral runtime imports are not silently ignored", () => {
  assert.throws(() => analyze(root, [component], [tree({ [component]: "const name = './dynamic'; import(name);" })], mapping), /动态引用/);
});
test("missing local import is unsafe even when another dependency resolves", () => {
  assert.throws(() => analyze(root, [component], [tree({ [component]: 'import x from "./missing"; export default x;' })], mapping), /未解析/);
});
test("entry/config/asset changes are full", () => {
  for (const p of ["frontend/src/App.tsx", "frontend/package.json", "frontend/src/index.css"]) {
    assert.equal(analyze(root, [p], [tree()], mapping).mode, "full");
  }
});
test("a changed spec retains smoke", () => {
  const result = analyze(root, [spec], [tree()], mapping);
  assert.equal(result.mode, "selected"); assert.ok(result.tests.includes(spec)); assert.ok(result.tests.includes(mapping.browser_smoke[0]));
});
test("manual/release and malformed context cannot narrow tests", () => {
  assert.equal(selectImpact({ paths: [component], force_full: true }).mode, "full");
  assert.equal(selectImpact(null).mode, "full");
  assert.equal(selectImpact({ paths: [42], force_full: false }).mode, "full");
});
