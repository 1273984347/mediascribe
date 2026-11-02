// wiki.js 渲染器冒烟测试 — node 直跑, 无框架依赖 (Phase 4, 2026-11-03)。
// 覆盖: escapeHtml / 时间戳换算与深链拼接 / XSS 转义 / wikilink 缺失态 /
// frontmatter url 兜底 / js: href 拦截 / 基本结构。
// 运行: node scripts/tools/wiki_render_smoke.mjs  (或经 pytest 包装自动跑)
// 注意: stub 必须先于模块加载 — wiki.js 顶层会摸 document/localStorage。
// 渲染器冒烟测试 (node 直接跑, 无框架) — stub 必须先于模块加载
globalThis.document = {
  getElementById: () => ({ innerHTML: "", addEventListener: () => {}, appendChild: () => {},
    querySelector: () => null, querySelectorAll: () => [], insertAdjacentHTML: () => {},
    replaceChildren: () => {}, classList: { add: () => {}, remove: () => {}, toggle: () => {} },
    dataset: {}, textContent: "" }),
  querySelectorAll: () => [], querySelector: () => null,
  createElement: () => ({ addEventListener: () => {}, classList: { add: () => {}, remove: () => {}, toggle: () => {} },
    dataset: {}, innerHTML: "", appendChild: () => {}, querySelector: () => ({ textContent: "" }),
    insertAdjacentHTML: () => {} }),
};
globalThis.window = {}; globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.fetch = () => new Promise(() => {});
globalThis.CSS = { escape: (s) => s };

const { escapeHtml, tsToSeconds, withTimestamp, renderMarkdown } = await import("file:///D:/MediaScribe/mediascribe/web/static/wiki.js");

let failed = 0;
const check = (name, cond) => { if (!cond) { console.error("FAIL:", name); failed++; } else console.log("ok:", name); };

check("escapeHtml", escapeHtml(`<script>alert("x")</script>`) === "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;");
check("ts mm:ss", tsToSeconds("12:34") === 754);
check("ts h:mm:ss", tsToSeconds("1:02:03") === 3723);
check("ts invalid", tsToSeconds("abc") === null);
check("t param &", withTimestamp("https://b23.tv/x?t=1", 60) === "https://b23.tv/x?t=1&t=60s");
check("t param ?", withTimestamp("https://b23.tv/x", 60) === "https://b23.tv/x?t=60s");

const evil = '正文 <img src=x onerror=alert(1)> 和 <script>bad()</script>';
const out1 = renderMarkdown(evil, { type: "wiki" });
check("xss img escaped", !out1.includes("<img"));
check("xss script escaped", !out1.includes("<script>"));
check("xss text preserved", out1.includes("&lt;script&gt;"));

// 需要笔记表: wikilink 解析 + 概念笔记来源追踪 — 通过 load() 之外的注入点
// (notesByName 是模块内 Map; 用 fetch stub 次数控制不现实, 这里直接构造
//  raw 笔记场景 + 概念场景各测一轮, 依赖模块内表为空时的 missing 路径)
const out2 = renderMarkdown("> [[作者 - 影视飓风]] · [[概念 - 不存在的概念]]", { type: "wiki" });
check("wikilink missing class", out2.includes("wiki-link missing"));
check("wikilink rendered", out2.includes("data-wikilink="));

const out3 = renderMarkdown("---\ntitle: x\nurl: https://b23.tv/BV1\n---\n\n**[01:30]** 有人物出场。", { type: "raw" });
check("ts deep link", out3.includes('href="https://b23.tv/BV1?t=90s"'));
check("frontmatter stripped", !out3.includes("title: x"));

const out5 = renderMarkdown("- **[00:30]** 无来源", { type: "wiki" });
check("ts no url stamp", out5.includes('class="ts-stamp"'));

const out6 = renderMarkdown("[点我](javascript:alert(1))", { type: "wiki" });
check("js href blocked", !out6.includes('<a class="ext"'));

const out7 = renderMarkdown("# 标题\n\n- 项目一\n- 项目二\n\n> 引用句", { type: "wiki" });
check("h1", out7.includes("<h1>"));
check("ul", out7.includes("<ul>") && out7.includes("<li>"));
check("blockquote", out7.includes("<blockquote>"));

console.log(failed === 0 ? "ALL PASS" : `${failed} FAILED`);
process.exit(failed === 0 ? 0 : 1);
