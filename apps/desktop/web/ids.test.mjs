// 静态体检：页面里「引用了、但页面里没有的 id」。
// 前端已拆成多个 JS 文件，因此把 web/ 下的 .html/.js 当同一份源码扫描。
// 用法：node apps/desktop/web/ids.test.mjs
import { readdirSync, readFileSync } from "node:fs";

const dir = new URL(".", import.meta.url);
const sources = readdirSync(dir)
  .filter((name) => name.endsWith(".html") || name.endsWith(".js"))
  .map((name) => ({
    name,
    text: readFileSync(new URL(name, dir), "utf8"),
  }));

const defined = new Set();
const refs = new Map();

for (const { name, text } of sources) {
  for (const match of text.matchAll(/(?<![-\w])id\s*=\s*["']([A-Za-z0-9_-]+)["']/g)) {
    defined.add(match[1]);
  }
  for (const re of [
    /\$\(\s*["']([A-Za-z0-9_-]+)["']\s*\)/g,
    /getElementById\(\s*["']([A-Za-z0-9_-]+)["']\s*\)/g,
  ]) {
    for (const match of text.matchAll(re)) {
      const line = text.slice(0, match.index).split("\n").length;
      const list = refs.get(match[1]) || [];
      list.push(`${name}:${line}`);
      refs.set(match[1], list);
    }
  }
}

// 这些控件按需创建，或在代码里有 ?. / if(!x) return 兜底；缺了不会让整页初始化中断。
const OPTIONAL = new Set(["mailNotifStack", "prLangTo", "prTranslateSource", "tofClose"]);
const missing = [...refs.keys()].filter((id) => !defined.has(id) && !OPTIONAL.has(id));

console.log(`ids.test: 页面定义 ${defined.size} 个 id，代码引用 ${refs.size} 个`);
if (missing.length) {
  console.error("[X] 引用了但页面里没有的 id：");
  for (const id of missing) console.error(`    ${id}   引用自 ${refs.get(id).join(", ")}`);
  console.error("    顶层取到 null 会抛错，把后面所有初始化一起带没（整页空壳）——要么补 id，要么删掉那句引用。");
  process.exit(1);
}
console.log("[OK] 没有悬空 id 引用");
