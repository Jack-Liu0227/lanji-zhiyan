// 静态体检：index.html 里「引用了、但页面里没有的 id」。
// 为什么需要它：单文件前端里一句顶层 $("x").onclick = … 撞上不存在的 id 就会抛错，
// 且它后面所有初始化一行都不跑 —— 表现是整页空壳，而语法检查（node --check）与 Python 自检全绿。
// 用法：node apps/desktop/web/ids.test.mjs
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("./index.html", import.meta.url), "utf8");

// 页面里定义过的 id：静态 markup 与 JS 模板串一视同仁（都在同一个文件里）；排除 data-id="…" 这类带前缀的
const defined = new Set(
  [...src.matchAll(/(?<![-\w])id\s*=\s*["']([A-Za-z0-9_-]+)["']/g)].map((m) => m[1])
);

const refs = new Map();
for (const re of [
  /\$\(\s*["']([A-Za-z0-9_-]+)["']\s*\)/g,               // $("x")
  /getElementById\(\s*["']([A-Za-z0-9_-]+)["']\s*\)/g,   // document.getElementById("x")
]) {
  for (const m of src.matchAll(re)) {
    const line = src.slice(0, m.index).split("\n").length;
    refs.set(m[1], (refs.get(m[1]) || []).concat(line));
  }
}

// 这几个是可选控件：代码里有 ?. 或 if(!x) return 兜底，缺了只是该功能静默退化，不致命
const OPTIONAL = new Set(["mailNotifStack", "prLangTo", "prTranslateSource"]);

const missing = [...refs.keys()].filter((id) => !defined.has(id) && !OPTIONAL.has(id));

console.log(`ids.test: 页面定义 ${defined.size} 个 id，代码引用 ${refs.size} 个`);
if (missing.length) {
  console.error("[X] 引用了但页面里没有的 id：");
  for (const id of missing) console.error(`    ${id}   引用行 ${refs.get(id).join(", ")}`);
  console.error("    顶层取到 null 会抛错，把后面所有初始化一起带没（整页空壳）—— 要么改 id，要么删掉那句引用。");
  process.exit(1);
}
console.log("[OK] 没有悬空 id 引用");
