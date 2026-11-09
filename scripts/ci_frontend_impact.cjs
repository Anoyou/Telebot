#!/usr/bin/env node
// Resolve actual TypeScript imports after pnpm install; uncertainty means all tests.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const { createRequire } = require("node:module");
const ROOT = path.resolve(__dirname, "..");
const ts = createRequire(path.join(ROOT, "frontend/package.json"))("typescript");
const full = reason => ({ mode: "full", tests: null, reason });
const relative = (root, file) => path.relative(root, file).split(path.sep).join("/");

function walk(folder) {
  return fs.readdirSync(folder, { withFileTypes: true }).flatMap(e => e.isDirectory() ? walk(path.join(folder, e.name)) : [path.join(folder, e.name)]);
}
function snapshot(root, revision = "") {
  if (!revision) return new Map([...walk(path.join(root, "frontend/src")), path.join(root, "frontend/package.json")].map(p => [relative(root, p), fs.readFileSync(p, "utf8")]));
  const paths = execFileSync("git", ["ls-tree", "-r", "--name-only", "-z", revision, "--", "frontend/src", "frontend/package.json"], { cwd: root, maxBuffer: 16 * 1024 * 1024 }).toString().split("\0").filter(Boolean);
  return new Map(paths.map(p => [p, /\.[cm]?[jt]sx?$/.test(p) ? execFileSync("git", ["show", `${revision}:${p}`], { cwd: root, maxBuffer: 16 * 1024 * 1024 }).toString() : ""]));
}
function suiteHash(root) {
  const hash = crypto.createHash("sha256");
  for (const p of walk(path.join(root, "frontend/tests")).filter(p => /\.(ts|tsx|js|json|swift)$/.test(p)).sort()) {
    hash.update(relative(root, p)); hash.update("\0"); hash.update(fs.readFileSync(p)); hash.update("\0");
  }
  return hash.digest("hex");
}
function reverseGraph(root, files) {
  const configPath = path.join(root, "frontend/tsconfig.app.json");
  const config = ts.readConfigFile(configPath, ts.sys.readFile);
  if (config.error) throw new Error("无法读取 TypeScript 配置");
  const options = ts.parseJsonConfigFileContent(config.config, ts.sys, path.dirname(configPath)).options;
  const sourceRoot = path.join(root, "frontend/src") + path.sep;
  const host = {
    fileExists: p => p.startsWith(sourceRoot) ? files.has(relative(root, p)) : ts.sys.fileExists(p),
    readFile: p => p.startsWith(sourceRoot) ? files.get(relative(root, p)) : ts.sys.readFile(p),
    directoryExists: p => p.startsWith(sourceRoot) ? [...files.keys()].some(k => k.startsWith(relative(root, p) + "/")) : ts.sys.directoryExists(p),
    realpath: p => p,
    getCurrentDirectory: () => root,
  };
  const cache = ts.createModuleResolutionCache(root, s => s, options);
  const reverse = new Map();
  for (const [file, text] of files) {
    if (!/\.[cm]?[jt]sx?$/.test(file) || /\.(test|spec)\.[cm]?[jt]sx?$/.test(file)) continue;
    const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true);
    if (source.parseDiagnostics.length) throw new Error(`TypeScript 解析失败: ${file}`);
    const imports = [];
    const visit = n => {
      if ((ts.isImportDeclaration(n) || ts.isExportDeclaration(n)) && n.moduleSpecifier) imports.push(n.moduleSpecifier.text);
      if (ts.isImportEqualsDeclaration(n) && ts.isExternalModuleReference(n.moduleReference)) imports.push(n.moduleReference.expression.text);
      if (ts.isCallExpression(n) && (n.expression.kind === ts.SyntaxKind.ImportKeyword || (ts.isIdentifier(n.expression) && n.expression.text === "require"))) {
        if (n.arguments.length !== 1 || !ts.isStringLiteralLike(n.arguments[0])) throw new Error(`动态引用无法可靠解析: ${file}`);
        imports.push(n.arguments[0].text);
      }
      if (ts.isCallExpression(n) && n.expression.getText(source).startsWith("import.meta.glob")) throw new Error(`动态资源引用: ${file}`);
      ts.forEachChild(n, visit);
    };
    visit(source);
    for (const specifier of imports) {
      if (!specifier.startsWith(".") && !specifier.startsWith("@/") && !specifier.startsWith("/")) continue;
      const resolved = ts.resolveModuleName(specifier, path.join(root, file), options, host, cache).resolvedModule;
      let dependency = resolved && relative(root, resolved.resolvedFileName);
      if (!dependency || !files.has(dependency)) {
        const clean = specifier.split("?")[0];
        dependency = relative(root, clean.startsWith("@/") ? path.join(root, "frontend/src", clean.slice(2)) : path.resolve(root, path.dirname(file), clean));
      }
      if (!files.has(dependency)) throw new Error(`本地引用未解析: ${file} -> ${specifier}`);
      if (!reverse.has(dependency)) reverse.set(dependency, new Set());
      reverse.get(dependency).add(file);
    }
  }
  return reverse;
}
function analyze(root, changed, snapshots, mapping) {
  const specs = new Set(mapping.browser_smoke);
  const pages = new Set();
  const reverse = new Map();
  for (const files of snapshots) for (const [dependency, consumers] of reverseGraph(root, files)) {
    if (!reverse.has(dependency)) reverse.set(dependency, new Set());
    for (const consumer of consumers) reverse.get(dependency).add(consumer);
  }
  for (const file of changed) {
    if (/^frontend\/tests\/(a11y|visual)\/.*\.spec\.ts$/.test(file)) { specs.add(file); continue; }
    if (!/^frontend\/src\/.*\.[cm]?[jt]sx?$/.test(file) || ["frontend/src/App.tsx", "frontend/src/main.tsx"].includes(file)) return full(`公共入口、依赖、资源或配置变化: ${file}`);
    if (snapshots.some(files => !files.has(file))) return full(`新增、删除或重命名: ${file}`);
    const pending = [[file, false]];
    const seen = new Set();
    let owned = false;
    while (pending.length) {
      let [node, throughPage] = pending.pop();
      if (node.startsWith("frontend/src/pages/")) {
        if (!mapping.browser[node]) return full(`受影响页面未登记: ${node}`);
        pages.add(node); owned = true; throughPage = true;
        for (const spec of mapping.browser[node]) specs.add(spec);
      }
      if (["frontend/src/App.tsx", "frontend/src/main.tsx"].includes(node)) {
        if (!throughPage) return full(`改动直接影响公共入口: ${file}`);
        continue;
      }
      const state = `${node}:${throughPage}`;
      if (seen.has(state)) continue;
      seen.add(state);
      for (const consumer of reverse.get(node) || []) pending.push([consumer, throughPage]);
    }
    if (!owned) return full(`找不到已验证的页面归属: ${file}`);
  }
  return { mode: "selected", tests: [...specs].sort(), reason: "TypeScript base/head 反向依赖 + 已核对页面映射 + 基础冒烟", affected_pages: [...pages].sort() };
}
function selectImpact(context, root = ROOT) {
  try {
    if (!context || context.force_full || !Array.isArray(context.paths)) return full("全量运行或没有可靠的变更计划");
    const mapping = JSON.parse(fs.readFileSync(path.join(root, "scripts/ci-test-map.json"), "utf8"));
    if (mapping.browser_suite_hash !== suiteHash(root)) return full("浏览器测试集变化，覆盖映射需要复核");
    const changed = context.paths.filter(p => p.startsWith("frontend/") && !/\.(md|rst)$/.test(p));
    if (!changed.length) return full("浏览器任务未获得可缩小的改动集合");
    const result = analyze(root, changed, [snapshot(root), ...(context.base ? [snapshot(root, context.base)] : [])], mapping);
    if (result.tests && result.tests.some(p => !/^frontend\/tests\/(a11y|visual)\/.*\.spec\.ts$/.test(p) || !fs.existsSync(path.join(root, p)))) return full("测试映射存在无效路径");
    return result;
  } catch (error) { return full(`无法可靠确定前端影响范围: ${error.message}`); }
}
module.exports = { analyze, selectImpact, reverseGraph, suiteHash };
if (require.main === module) {
  try { console.log(JSON.stringify(selectImpact(JSON.parse(fs.readFileSync(0, "utf8"))))); }
  catch (error) { console.log(JSON.stringify(full(`计划解析失败: ${error.message}`))); }
}
