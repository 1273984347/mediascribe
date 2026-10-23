"""
L1 前端框架层基准 —— 候选工程脚手架生成器。

为什么需要脚手架
----------------
对比 5 个前端框架时，**测量口径必须完全一致**，否则数字不可比。
口径一旦交给多个执行体各自实现，必然出现「A 的冷启动含数据请求、B 的不含」
这类偏差。因此本脚本把 5 个候选的**页面结构、DOM 结构、CSS、埋点协议**
全部固定下来，只允许「框架语法 + 同步刷新方式」不同。

产物落在 ``output/bench/frontend/candidates/<name>/``（被 .gitignore 忽略），
入库的只有本脚本与 ``bench_frontend.py``。

候选
----
======  ==================  ==============================
name    技术栈                同步刷新方式
======  ==================  ==============================
vanilla 原生 HTML/CSS/JS      天然同步
react   React 18 + Vite        ReactDOM.flushSync
vue     Vue 3 + Vite           nextTick
svelte  Svelte 5 + Vite        flushSync (from 'svelte')
solid   SolidJS + Vite         同步信号更新（默认同步）
======  ==================  ==============================

统一埋点协议（每个候选都必须暴露）
---------------------------------
``window.__bench``:

==============  =========================================================
字段             含义
==============  =========================================================
``ready``        true 表示首屏 10000 条已渲染完成
``coldStartMs``  渲染完成时的 ``performance.now()``（time origin = 导航起点，
                 所以天然包含「导航 + 取数 + 渲染」全程）
``renderMs``     纯渲染耗时（挂载/更新 DOM），不含取数
``rows``         渲染出的行数
``filterMs``     筛选一次字符后 DOM 更新完成的耗时
``filterDone``   true 表示筛选测量已完成
==============  =========================================================

``window.__benchScroll(totalPx, frames)``：在 ``frames`` 帧内滚过 ``totalPx``，
用 rAF 采样，返回 ``{avgFrameMs, dropped, frames, droppedThresholdMs}``。

数据源统一为 ``../data/transcripts.json``（相对路径对所有候选一致）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_ROOT = REPO_ROOT / "output" / "bench" / "frontend"
CANDIDATE_ROOT = OUT_ROOT / "candidates"

# ---------------------------------------------------------------------------
# 共用资产
# ---------------------------------------------------------------------------

#: 统一 CSS —— 所有候选共用，保证布局/尺寸完全一致，渲染耗时才可比。
STYLES_CSS = """:root {
  --bg: #fbfaf9;
  --surface: #ffffff;
  --fg: rgba(0, 0, 0, 0.9);
  --fg-2: rgba(0, 0, 0, 0.56);
  --fg-3: rgba(0, 0, 0, 0.38);
  --line: rgba(0, 0, 0, 0.12);
  --fill: rgba(0, 0, 0, 0.04);
  --blue: #007cff;
  --green: #16c456;
  --orange: #ff9f0a;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; height: 100%; }
body {
  font-family: Inter, -apple-system, "Segoe UI", system-ui, "PingFang SC",
    "Microsoft YaHei", sans-serif;
  font-size: 14px;
  background: var(--bg);
  color: var(--fg);
}
.app {
  display: grid;
  grid-template-columns: 260px 1fr;
  height: 100vh;
  overflow: hidden;
}
.sidebar {
  border-right: 1px solid var(--line);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: var(--surface);
}
.sidebar h1 { font-size: 15px; margin: 0; font-weight: 600; }
#filter {
  width: 100%;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--fill);
  color: var(--fg);
  font-size: 13px;
}
.count { font-size: 12px; color: var(--fg-2); font-variant-numeric: tabular-nums; }
/* grid item 默认 min-height:auto，会让子元素撑破容器而无法滚动 —— 必须显式归零 */
.list-host { min-height: 0; overflow: hidden; }
.list { height: 100%; min-height: 0; overflow-y: auto; padding: 8px 16px; }
.row {
  height: 64px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 12px;
  border-bottom: 1px solid var(--line);
}
.row .idx { width: 56px; color: var(--fg-3); font-variant-numeric: tabular-nums; }
.row .title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row .dur { width: 72px; color: var(--fg-2); font-variant-numeric: tabular-nums; }
.row .engine, .row .status {
  width: 104px;
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--fill);
  color: var(--fg-2);
  text-align: center;
}
.row .status.done { color: var(--green); }
.row .status.failed { color: #ff4756; }
.row .status.running { color: var(--blue); }
.row .status.queued { color: var(--orange); }
"""

#: 统一的滚动采样函数（所有候选共用同一份实现）。
SCROLL_JS = """
window.__benchScroll = function (totalPx, frames) {
  return new Promise(function (resolve) {
    var list = document.getElementById('list');
    var samples = [];
    var start = null;
    var perFrame = totalPx / frames;
    function step(ts) {
      if (start === null) start = ts;
      samples.push(ts);
      list.scrollTop += perFrame;
      if (samples.length < frames + 1) {
        requestAnimationFrame(step);
      } else {
        var intervals = [];
        for (var i = 1; i < samples.length; i++) intervals.push(samples[i] - samples[i - 1]);
        var sum = 0;
        for (var j = 0; j < intervals.length; j++) sum += intervals[j];
        var dropped = 0;
        for (var k = 0; k < intervals.length; k++) if (intervals[k] > 32) dropped++;
        resolve({
          avgFrameMs: intervals.length ? sum / intervals.length : 0,
          maxFrameMs: intervals.length ? Math.max.apply(null, intervals) : 0,
          dropped: dropped,
          frames: intervals.length,
          droppedThresholdMs: 32
        });
      }
    }
    requestAnimationFrame(step);
  });
};
"""

#: 统一的公共 JS：数据加载、筛选逻辑、行渲染数据准备、bench 对象初始化。
COMMON_JS = """
var ALL = [];
var QUERY = '';

function matchQuery(item, q) {
  if (!q) return true;
  var hay = (item.title + ' ' + item.engine + ' ' + item.status).toLowerCase();
  return hay.indexOf(q) !== -1;
}

function filtered() {
  if (!QUERY) return ALL;
  var q = QUERY.toLowerCase();
  var out = [];
  for (var i = 0; i < ALL.length; i++) if (matchQuery(ALL[i], q)) out.push(ALL[i]);
  return out;
}

function fmtDuration(s) {
  var m = Math.floor(s / 60);
  var sec = s - m * 60;
  return m + ':' + (sec < 10 ? '0' : '') + sec.toFixed(1);
}

window.__bench = {
  ready: false,
  coldStartMs: 0,
  renderMs: 0,
  rows: 0,
  filterMs: 0,
  filterDone: false
};

window.__benchFlush = function () { return Promise.resolve(); };
"""


# ---------------------------------------------------------------------------
# 各候选的模板
# ---------------------------------------------------------------------------

VITE_CONFIG = """import { defineConfig } from 'vite'

export default defineConfig({
  // 相对基址：构建产物可放在任意子路径下被静态服务
  base: './',
  build: { outDir: 'dist', emptyOutDir: true, target: 'es2020' }
})
"""

INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Bench :: {name}</title>
    <link rel="stylesheet" href="./styles.css" />
  </head>
  <body>
    <div class="app">
      <aside class="sidebar">
        <h1>转写历史</h1>
        <input id="filter" type="text" placeholder="搜索标题 / 引擎 / 状态" autocomplete="off" />
        <div id="count" class="count"></div>
      </aside>
      <main class="list-host"><div id="root-slot"></div></main>
    </div>
    <!-- 共享层：全局工具 + 滚动采样。普通 script（同步执行）先于 type=module
         （defer）加载，因此框架候选的模块脚本执行时这些全局量已就绪。 -->
    <script src="./bench-shared.js"></script>
    <script type="module" src="./src/main.{ext}"></script>
  </body>
</html>
"""

#: 共享层：所有候选（含框架候选）都引入这一个文件，保证全局工具与
#: 滚动采样实现**逐字相同**，否则渲染/滚动指标不可比。
SHARED_JS = COMMON_JS + SCROLL_JS

# --- vanilla ---------------------------------------------------------------

#: vanilla 的 DOM 由脚本直接填充列表容器，结构与框架候选不同源，
#: 因此单独一份 HTML；但**类名、尺寸、样式**与其他候选完全一致。
VANILLA_HTML = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Bench :: vanilla</title>
    <link rel="stylesheet" href="./styles.css" />
  </head>
  <body>
    <div class="app">
      <aside class="sidebar">
        <h1>转写历史</h1>
        <input id="filter" type="text" placeholder="搜索标题 / 引擎 / 状态" autocomplete="off" />
        <div id="count" class="count"></div>
      </aside>
      <main id="list" class="list"></main>
    </div>
    <script src="./bench-shared.js"></script>
    <script type="module" src="./src/main.js"></script>
  </body>
</html>
"""

VANILLA_JS = (
    """
var listEl = document.getElementById('list');
var countEl = document.getElementById('count');

function buildRow(item, index) {
  var row = document.createElement('div');
  row.className = 'row';
  var idx = document.createElement('span');
  idx.className = 'idx';
  idx.textContent = '#' + index;
  var title = document.createElement('span');
  title.className = 'title';
  title.textContent = item.title;
  var dur = document.createElement('span');
  dur.className = 'dur';
  dur.textContent = fmtDuration(item.duration_s);
  var engine = document.createElement('span');
  engine.className = 'engine';
  engine.textContent = item.engine;
  var status = document.createElement('span');
  status.className = 'status ' + item.status;
  status.textContent = item.status;
  row.appendChild(idx);
  row.appendChild(title);
  row.appendChild(dur);
  row.appendChild(engine);
  row.appendChild(status);
  return row;
}

function paint(items) {
  var t0 = performance.now();
  var frag = document.createDocumentFragment();
  for (var i = 0; i < items.length; i++) frag.appendChild(buildRow(items[i], i));
  listEl.replaceChildren(frag);
  countEl.textContent = items.length + ' / ' + ALL.length + ' 条';
  return performance.now() - t0;
}

document.getElementById('filter').addEventListener('input', function (ev) {
  QUERY = ev.target.value;
  var t0 = performance.now();
  paint(filtered());
  window.__bench.filterMs = performance.now() - t0;
  window.__bench.filterDone = true;
});

fetch('../data/transcripts.json')
  .then(function (r) { return r.json(); })
  .then(function (data) {
    ALL = data;
    var t0 = performance.now();
    window.__bench.renderMs = paint(ALL);
    window.__bench.rows = listEl.childElementCount;
    window.__bench.coldStartMs = performance.now();
    window.__bench.ready = true;
  });
"""
)

# --- react -----------------------------------------------------------------

REACT_HTML = INDEX_HTML.format(name="react", ext="jsx")
REACT_JSX = """import React, { useState, useEffect, useMemo } from 'react'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'

function Row({ item, index }) {
  return (
    <div className="row">
      <span className="idx">#{index}</span>
      <span className="title">{item.title}</span>
      <span className="dur">{fmtDuration(item.duration_s)}</span>
      <span className="engine">{item.engine}</span>
      <span className={'status ' + item.status}>{item.status}</span>
    </div>
  )
}

function App() {
  const [all, setAll] = useState([])
  const [query, setQuery] = useState('')
  const [tick, setTick] = useState(0)

  useEffect(() => {
    fetch('../data/transcripts.json')
      .then((r) => r.json())
      .then((data) => {
        ALL = data
        const t0 = performance.now()
        flushSync(() => setAll(data))
        window.__bench.renderMs = performance.now() - t0
        window.__bench.rows = document.getElementById('list').childElementCount
        window.__bench.coldStartMs = performance.now()
        window.__bench.ready = true
      })
  }, [])

  const items = useMemo(() => {
    if (!query) return all
    const q = query.toLowerCase()
    return all.filter((it) => matchQuery(it, q))
  }, [all, query, tick])

  useEffect(() => {
    const el = document.getElementById('filter')
    const handler = (ev) => {
      const q = ev.target.value
      const t0 = performance.now()
      flushSync(() => {
        setQuery(q)
      })
      window.__bench.filterMs = performance.now() - t0
      window.__bench.filterDone = true
    }
    el.addEventListener('input', handler)
    return () => el.removeEventListener('input', handler)
  }, [])

  return (
    <>
      <div className="count" id="count">
        {items.length} / {all.length} 条
      </div>
      <div id="list" className="list">
        {items.map((it, i) => (
          <Row key={it.id} item={it} index={i} />
        ))}
      </div>
    </>
  )
}

createRoot(document.getElementById('root-slot')).render(<App />)
"""

# React 需要一个挂载点：把 index.html 的 main 换成带 root-slot 的容器。
REACT_HTML = REACT_HTML.replace(
    '<main id="list" class="list"></main>',
    '<main class="list-host"><div id="root-slot"></div></main>',
)

# --- vue -------------------------------------------------------------------

VUE_HTML = INDEX_HTML.format(name="vue", ext="js")

#: Vue 用 SFC（.vue）而不是运行时 template 字符串 —— 两个原因：
#: ① Vite 默认解析到 ``vue.runtime.esm-bundler.js``，**不含模板编译器**，
#:    `template` 字符串会被**静默忽略**，页面渲染成一个空注释节点 ``<!---->``，
#:    不报任何 error（极具隐蔽性）；② SFC 是 Vue 项目的真实主流用法。
#: 另：模板表达式编译成 ``_ctx.xxx``，全局函数必须显式引入，否则取不到。
VUE_SFC = """<script setup>
import { ref, computed, nextTick, onMounted } from 'vue'

// 从共享层取全局工具（模板里的标识符走 _ctx 代理，取不到裸全局）
const { fmtDuration, matchQuery } = window

const all = ref([])
const query = ref('')

const items = computed(() => {
  if (!query.value) return all.value
  const q = query.value.toLowerCase()
  return all.value.filter((it) => matchQuery(it, q))
})

fetch('../data/transcripts.json')
  .then((r) => r.json())
  .then(async (data) => {
    ALL = data
    const t0 = performance.now()
    all.value = data
    await nextTick()
    window.__bench.renderMs = performance.now() - t0
    window.__bench.rows = document.getElementById('list').childElementCount
    window.__bench.coldStartMs = performance.now()
    window.__bench.ready = true
  })

const onFilter = async (ev) => {
  const t0 = performance.now()
  query.value = ev.target.value
  await nextTick()
  window.__bench.filterMs = performance.now() - t0
  window.__bench.filterDone = true
}

// 筛选框在组件之外（sidebar），需显式绑定
onMounted(() => {
  document.getElementById('filter').addEventListener('input', onFilter)
})
</script>

<template>
  <div class="count" id="count">{{ items.length }} / {{ all.length }} 条</div>
  <div id="list" class="list">
    <div class="row" v-for="(it, i) in items" :key="it.id">
      <span class="idx">#{{ i }}</span>
      <span class="title">{{ it.title }}</span>
      <span class="dur">{{ fmtDuration(it.duration_s) }}</span>
      <span class="engine">{{ it.engine }}</span>
      <span class="status" :class="it.status">{{ it.status }}</span>
    </div>
  </div>
</template>
"""

VUE_MAIN = """import { createApp } from 'vue'
import App from './App.vue'

createApp(App).mount('#root-slot')
"""

VUE_HTML = VUE_HTML.replace(
    '<main id="list" class="list"></main>',
    '<main class="list-host"><div id="root-slot"></div></main>',
)

# --- svelte ----------------------------------------------------------------

SVELTE_HTML = INDEX_HTML.format(name="svelte", ext="js")

#: Svelte 5 的 runes（``$state`` / ``$derived``）**只在 .svelte / .svelte.js
#: 模块里可用** —— 写在普通 .js 里会报 ``$state is not defined``。
#: 因此 Svelte 候选用标准 SFC，这是它的真实用法，也是有意义的对比基准。
SVELTE_SFC = """<script>
  import { flushSync } from 'svelte'

  let all = $state([])
  let query = $state('')
  let items = $derived(
    query ? all.filter((it) => matchQuery(it, query.toLowerCase())) : all
  )

  fetch('../data/transcripts.json')
    .then((r) => r.json())
    .then((data) => {
      ALL = data
      const t0 = performance.now()
      flushSync(() => {
        all = data
      })
      window.__bench.renderMs = performance.now() - t0
      window.__bench.rows = document.getElementById('list').childElementCount
      window.__bench.coldStartMs = performance.now()
      window.__bench.ready = true
    })

  function onFilter(ev) {
    const t0 = performance.now()
    flushSync(() => {
      query = ev.target.value
    })
    window.__bench.filterMs = performance.now() - t0
    window.__bench.filterDone = true
  }

  // 筛选框在组件之外（sidebar），需显式绑定
  document.getElementById('filter').addEventListener('input', onFilter)
</script>

<div class="count" id="count">{items.length} / {all.length} 条</div>
<div id="list" class="list">
  {#each items as it, i}
    <div class="row">
      <span class="idx">#{i}</span>
      <span class="title">{it.title}</span>
      <span class="dur">{fmtDuration(it.duration_s)}</span>
      <span class="engine">{it.engine}</span>
      <span class="status {it.status}">{it.status}</span>
    </div>
  {/each}
</div>
"""

SVELTE_MAIN = """import { mount } from 'svelte'
import App from './App.svelte'

mount(App, { target: document.getElementById('root-slot') })
"""

SVELTE_HTML = SVELTE_HTML.replace(
    '<main id="list" class="list"></main>',
    '<main class="list-host"><div id="root-slot"></div></main>',
)

# --- solid -----------------------------------------------------------------

SOLID_HTML = INDEX_HTML.format(name="solid", ext="jsx")
SOLID_JSX = """import { createSignal, For } from 'solid-js'
import { render } from 'solid-js/web'

function App() {
  const [all, setAll] = createSignal([])
  const [query, setQuery] = createSignal('')
  const items = () => {
    const q = query().toLowerCase()
    return q ? all().filter((it) => matchQuery(it, q)) : all()
  }

  fetch('../data/transcripts.json')
    .then((r) => r.json())
    .then((data) => {
      ALL = data
      const t0 = performance.now()
      setAll(data)
      window.__bench.renderMs = performance.now() - t0
      window.__bench.rows = document.getElementById('list').childElementCount
      window.__bench.coldStartMs = performance.now()
      window.__bench.ready = true
    })

  const onFilter = (ev) => {
    const t0 = performance.now()
    setQuery(ev.target.value)
    window.__bench.filterMs = performance.now() - t0
    window.__bench.filterDone = true
  }

  // 筛选框在组件之外（sidebar），需显式绑定
  document.getElementById('filter').addEventListener('input', onFilter)

  return (
    <>
      <div class="count" id="count">
        {items().length} / {all().length} 条
      </div>
      <div id="list" class="list">
        <For each={items()}>
          {(it, i) => (
            <div class="row">
              <span class="idx">#{i()}</span>
              <span class="title">{it.title}</span>
              <span class="dur">{fmtDuration(it.duration_s)}</span>
              <span class="engine">{it.engine}</span>
              <span class="status {it.status}">{it.status}</span>
            </div>
          )}
        </For>
      </div>
    </>
  )
}

render(() => <App />, document.getElementById('root-slot'))
"""

SOLID_HTML = SOLID_HTML.replace(
    '<main id="list" class="list"></main>',
    '<main class="list-host"><div id="root-slot"></div></main>',
)


# ---------------------------------------------------------------------------
# 候选定义
# ---------------------------------------------------------------------------


def _npm_bin() -> str:
    """定位可执行的 npm。

    Windows 上 npm 实际是 ``npm.cmd`` —— ``subprocess`` 不经过 shell 时
    不会解析 PATHEXT，直接用 ``npm`` 会 ``FileNotFoundError``。
    """
    if os.name == "nt":
        p = shutil.which("npm.cmd")
        if p:
            return p
    return shutil.which("npm") or "npm"


def _npm_latest(pkg: str, retries: int = 2) -> Optional[str]:
    """查 npm 上某包的当前版本；查不到返回 None（调用方降级处理）。

    每个包都要起一次 npm 进程（较慢），且在网络波动时会超时 —— 因此：
    重试 + 放宽超时 + 结果落盘缓存（重跑脚手架不必重新查）。
    """
    cache_path = OUT_ROOT / "npm-versions.json"
    cache: Dict[str, str] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    if pkg in cache:
        return cache[pkg]

    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(
                [_npm_bin(), "view", pkg, "version"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode == 0:
                val = proc.stdout.strip().splitlines()
                if val and val[-1].strip():
                    cache[pkg] = val[-1].strip()
                    try:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        cache_path.write_text(
                            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                    except Exception:
                        pass
                    return cache[pkg]
        except Exception:
            pass
        time.sleep(2 + attempt * 3)
    return None


class Candidate:
    def __init__(
        self,
        name: str,
        kind: str,
        html: str,
        source: str,
        source_name: str,
        deps: List[str],
        dev_deps: List[str],
        vite_plugins: Optional[List[str]] = None,
        extra_sources: Optional[List[Tuple[str, str]]] = None,
    ):
        self.name = name
        self.kind = kind  # 'static' | 'vite'
        self.html = html
        self.source = source
        self.source_name = source_name
        self.deps = deps
        self.dev_deps = dev_deps
        self.vite_plugins = vite_plugins or []
        #: 额外的源文件 [(相对 src/ 的路径, 内容)] —— 例如 Svelte 的 .svelte SFC
        self.extra_sources = extra_sources or []


CANDIDATES: List[Candidate] = [
    Candidate(
        name="vanilla",
        kind="static",
        html=VANILLA_HTML,
        source=VANILLA_JS,
        # 必须与 VANILLA_HTML 里引用的 ./src/main.js 一致 —— 文件名对不上
        # 会直接 404，页面静默不渲染，基准会空等 120 s 超时。
        source_name="main.js",
        deps=[],
        dev_deps=[],
    ),
    Candidate(
        name="react",
        kind="vite",
        html=REACT_HTML,
        source=REACT_JSX,
        source_name="main.jsx",
        deps=["react", "react-dom"],
        dev_deps=["vite", "@vitejs/plugin-react"],
    ),
    Candidate(
        name="vue",
        kind="vite",
        html=VUE_HTML,
        source=VUE_MAIN,
        source_name="main.js",
        deps=["vue"],
        dev_deps=["vite", "@vitejs/plugin-vue"],
        extra_sources=[("App.vue", VUE_SFC)],
    ),
    Candidate(
        name="svelte",
        kind="vite",
        html=SVELTE_HTML,
        source=SVELTE_MAIN,
        source_name="main.js",
        deps=["svelte"],
        dev_deps=["vite", "@sveltejs/vite-plugin-svelte"],
        extra_sources=[("App.svelte", SVELTE_SFC)],
    ),
    Candidate(
        name="solid",
        kind="vite",
        html=SOLID_HTML,
        source=SOLID_JSX,
        source_name="main.jsx",
        deps=["solid-js"],
        dev_deps=["vite", "vite-plugin-solid"],
    ),
]


PLUGIN_SETUP = {
    "react": """import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: './',
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true, target: 'es2020' }
})
""",
    "vue": """import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: './',
  plugins: [vue()],
  build: { outDir: 'dist', emptyOutDir: true, target: 'es2020' }
})
""",
    "svelte": """import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

export default defineConfig({
  base: './',
  plugins: [svelte()],
  build: { outDir: 'dist', emptyOutDir: true, target: 'es2020' }
})
""",
    "solid": """import { defineConfig } from 'vite'
import solid from 'vite-plugin-solid'

export default defineConfig({
  base: './',
  plugins: [solid()],
  build: { outDir: 'dist', emptyOutDir: true, target: 'es2020' }
})
""",
}


# ---------------------------------------------------------------------------
# 数据生成
# ---------------------------------------------------------------------------

TITLES = [
    "第{n}集 科技观察：人工智能如何改变软件开发",
    "第{n}期 读书笔记：统计物理中的相变与普适类",
    "第{n}讲 公开课：机器学习模型的工程化落地",
    "第{n}章 纪录片解说：城市轨道交通的百年变迁",
    "第{n}集 访谈：独立开发者的产品方法论",
    "第{n}期 评测：年度旗舰手机影像横评",
    "第{n}讲 科普：量子纠缠到底说了什么",
    "第{n}集 播客：远程办公五年的真实数据",
]

ENGINES = ["faster-whisper", "whisperx", "openai-whisper", "whisper.cpp"]
STATUSES = ["done", "failed", "running", "queued", "done", "done", "queued"]


def generate_data(n: int = 10000) -> List[Dict[str, Any]]:
    """固定种子生成 10000 条转写记录（5 个候选共用同一份）。

    返回**裸数组**而不是带包装的对象 —— 页面代码统一按数组消费，
    包装结构一旦不一致就会出现「渲染 0 行」这种静默失败。
    """
    import random

    rnd = random.Random(20261023)
    items = []
    for i in range(n):
        tpl = TITLES[i % len(TITLES)]
        items.append(
            {
                "id": i,
                "title": tpl.format(n=i + 1),
                "duration_s": round(rnd.uniform(30, 7200), 1),
                "engine": ENGINES[rnd.randrange(len(ENGINES))],
                "status": STATUSES[rnd.randrange(len(STATUSES))],
            }
        )
    return items


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


def write_candidate(cand: Candidate, root: Path) -> Dict:
    d = root / cand.name
    # 只清 src/ —— **保留 node_modules**，否则每次重跑脚手架都要重装依赖
    # （npm install 在弱网/受限环境里是分钟级开销）。
    src_dir = d / "src"
    if src_dir.exists():
        shutil.rmtree(src_dir)
    (d / "src").mkdir(parents=True, exist_ok=True)

    (d / "styles.css").write_text(STYLES_CSS, encoding="utf-8")
    (d / "index.html").write_text(cand.html, encoding="utf-8")
    (d / "src" / cand.source_name).write_text(cand.source, encoding="utf-8")
    for rel_name, content in cand.extra_sources:
        target = d / "src" / rel_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    # 共享层落盘：所有候选引用同一份，保证全局工具与滚动采样逐字相同
    (d / "bench-shared.js").write_text(SHARED_JS, encoding="utf-8")

    meta: Dict = {"name": cand.name, "kind": cand.kind}
    if cand.kind == "vite":
        versions: Dict[str, str] = {}
        for pkg in cand.deps + cand.dev_deps:
            v = _npm_latest(pkg)
            versions[pkg] = v or "*"
        pkg_json = {
            "name": f"bench-{cand.name}",
            "private": True,
            "version": "0.0.0",
            "type": "module",
            "scripts": {"build": "vite build"},
            "dependencies": {p: versions[p] for p in cand.deps},
            "devDependencies": {p: versions[p] for p in cand.dev_deps},
        }
        (d / "package.json").write_text(
            json.dumps(pkg_json, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (d / "vite.config.js").write_text(PLUGIN_SETUP[cand.name], encoding="utf-8")
        meta["versions"] = versions
    return meta


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Scaffold L1 benchmark candidates")
    p.add_argument("--out", type=Path, default=CANDIDATE_ROOT)
    p.add_argument("--rows", type=int, default=10000)
    args = p.parse_args(argv)

    root = args.out
    root.mkdir(parents=True, exist_ok=True)

    data_dir = OUT_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = generate_data(args.rows)
    (data_dir / "transcripts.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[data ] {data_dir / 'transcripts.json'} ({args.rows} rows)")

    metas = []
    for cand in CANDIDATES:
        meta = write_candidate(cand, root)
        metas.append(meta)
        ver = meta.get("versions", {})
        vstr = " ".join(f"{k}@{v}" for k, v in ver.items())
        print(f"[cand ] {cand.name:8} {cand.kind:6} {vstr}")

    (root / "candidates.json").write_text(
        json.dumps(metas, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[ok] {len(CANDIDATES)} candidates scaffolded -> {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
