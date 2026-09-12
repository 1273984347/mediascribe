# KIMI 分部位图标动画体系 — 分析与复刻制作指南

> 调研时间:2026-09。证据来源:kimi.com / kimi.ai 线上样式表实测(37+ SVG 采样、
> 全量 @keyframes 提取)+ 官方《Kimi 品牌手册》(kimi.ai/resources/kimi-brand)。
> 目的:沉淀一套可直接照做的"分部位图标动画"制作经验,供本项目后续新增图标时复用。

---

## 1. 这套体系是什么

KIMI 的图标不是静态贴图,而是一套 **"SVG 部位 + 分段关键帧 + 状态触发器"** 的动效体系:
一个图标被拆成 2~4 个有叙事关系的部位(part),悬停 / 点击 / 状态切换时各部位按
先后顺序播放各自的动画,像一场 0.3~0.5 秒的微型舞台剧。

线上样式表中的关键帧命名(实测摘录,后缀为构建 hash):

```
LeftBarAnimatedIcon-enter-rs-1sihg8c-mh2        ← 侧栏图标进场
LeftBarAnimatedIcon-leave-rs-...-collapse       ← 退场(还有折叠变体)
NewChatAnimatedIcon-svg-rs-ew8n6j-p1            ← 新建对话图标,部位 1
MoreAnimatedIcon-svg-rs-156gmjb-p0/p1/p2        ← "更多"图标,三部位
ScheduledTasksAnimatedIcon-svg-rs-s3vm2d-p1/p2/p3
MicroscopeTimeline1Icon-enter-rs-zd41x3-p0/p1   ← 时间线系列进场
km-button-loading-rotate-7e977b9b               ← 按钮内加载转圈
mcp-action-spin / shimmer-* / breath-*          ← 状态动画(运行/微光/呼吸)
running-* / skeleton-pulse / toolCallTextIn-*   ← 运行流 / 骨架屏 / 内容入场
```

命名直接暴露了体系的三条设计规则:

1. **`<图标名>AnimatedIcon`** —— 动画图标是独立组件类别,不是所有图标都动;
2. **`-p0/-p1/-p2/-p3`** —— 图标按语义拆成多个部位,每个部位一组关键帧;
3. **`-enter-` / `-leave-` 成对** —— 进出场必须对称定义,还有 `-collapse` 折叠变体。

分工边界:**普通图标的动效全用 CSS 关键帧**(零运行时);**IP 形象、复杂形变**
才用 Rive 引擎(样式表中的 `RiveImg-*`)。两者不混用,成本分层清晰。

---

## 2. 结构模型(逆向结论)

一个"分部位图标"由五层构成:

```
<svg viewBox="0 0 24 24">            ← 24 网格,线性描边 1.8,圆头圆角
  <path class="p0" .../>             ← 部位 0:主体(最先动)
  <path class="p1" .../>             ← 部位 1:次级(延迟 ~80ms)
  <path class="p2" .../>             ← 部位 2:点缀(延迟 ~160ms)
</svg>
```

| 层 | 内容 | KIMI 实测值 |
|---|---|---|
| 网格 | viewBox | `0 0 24 24`(插画类 1024) |
| 笔画 | 描边 | **1.8**(绝对主流),圆头圆角 `round/round` |
| 部位 | 语义拆分 | 2~4 个,`p0..p3`,叙事顺序 = 动画顺序 |
| 时序 | delay 错峰 | 部位间约 60~120ms;总时长 0.3~0.5s;ease-out |
| 触发 | 状态 | hover 进场 / leave 退场 / click / `.loading` `.done` 等状态类 |
| 颜色 | currentColor | 永远单色跟随文字,图标不带彩色 |
| 降级 | reduced-motion | `prefers-reduced-motion` 下全部静止 |

**动画只允许动三种属性**(合成器友好,不引发布局):
`transform`(位移/旋转/缩放)、`opacity`、`stroke-dashoffset`(描边画入)。

---

## 3. 复刻五步法(SOP)

### Step 1 — 语义拆解(纸面)
在 24 网格草图上把图标拆成 2~4 个**有叙事先后**的部位。
判断标准:用户读这个图标的顺序,就是部位编号顺序。
反例:一个简单的圆圈没有叙事,不要硬拆。

例:「新建对话」= 气泡轮廓(p0)+ 加号(p1);
「发送」= 纸飞机主体(p0)+ 尾迹线(p1)。

### Step 2 — 部位标注
```html
<svg class="icon" viewBox="0 0 24 24" data-icon="new-chat">
  <path class="p0" d="…气泡…"/>
  <path class="p1" d="…加号…"/>
</svg>
```
每个部位独立 class(`p0/p1/...`),父级用 `data-icon` 标注语义名。

### Step 3 — 每部位一组关键帧
常用四种动效原语(全部合成器友好):

| 原语 | 关键帧写法 | 适用部位 |
|---|---|---|
| 位移进场 | `from { opacity:0; transform: translate(6px,0) }` | 箭头/尾迹 |
| 缩放弹出 | `from { opacity:0; transform: scale(0.4) }` | 加号/点 |
| 描边画入 | `stroke-dasharray: L; stroke-dashoffset: L → 0` | 轮廓/对勾(L=路径长) |
| 旋转/摇晃 | `rotate ±10deg` 往返 | 加载/警示 |

### Step 4 — 触发器与错峰
```css
.icon .p0 { stroke-dasharray: 30; stroke-dashoffset: 30; }
.icon .p1 { opacity: 0; }

.icon:hover .p0 { animation: draw 0.3s ease-out forwards; }
.icon:hover .p1 { animation: pop 0.3s ease-out 0.09s forwards; }  /* 错峰 90ms */
.icon:not(:hover) .p0 { animation: undraw 0.25s ease-in forwards; } /* leave 成对 */
```

### Step 5 — 降级与打磨
```css
@media (prefers-reduced-motion: reduce) {
  .icon path { animation: none !important; opacity: 1 !important;
               stroke-dashoffset: 0 !important; }
}
```
终态必须与静态稿完全一致(动画只是"到达"的方式,不能改变最终形态)。

---

## 4. 完整可运行示例

### 例 A:「新建 +」两笔画依次画出(悬停触发)

```html
<button class="demo-new" title="新建">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
       stroke-width="1.8" stroke-linecap="round">
    <path class="p0" d="M12 5v14"/>
    <path class="p1" d="M5 12h14"/>
  </svg>
</button>
```

```css
.demo-new { width: 40px; height: 40px; display: grid; place-items: center;
            background: rgba(127,127,127,.08); border: none; border-radius: 10px;
            color: inherit; cursor: pointer; }
.demo-new svg { width: 20px; }
.demo-new .p0, .demo-new .p1 { stroke-dasharray: 14; stroke-dashoffset: 0; }

.demo-new:hover .p0 { animation: draw 0.28s ease-out; }
.demo-new:hover .p1 { animation: draw 0.28s ease-out 0.09s; }
@keyframes draw { from { stroke-dashoffset: 14; } to { stroke-dashoffset: 0; } }

@media (prefers-reduced-motion: reduce) {
  .demo-new .p0, .demo-new .p1 { animation: none !important; stroke-dashoffset: 0 !important; }
}
```

### 例 B:「发送」箭头推移 + 尾迹伸缩

```html
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
     stroke-linecap="round" class="send">
  <path class="p0" d="M4 12h13"/>
  <path class="p1" d="M12 6l6 6-6 6"/>
</svg>
```

```css
.send .p1 { transition: transform 0.2s ease; transform-origin: 18px 12px; }
button:hover .send .p0 { animation: nudge 0.25s ease; }   /* 主体前顶 */
button:hover .send .p1 { transform: translateX(2px); }     /* 箭头跟随 */
@keyframes nudge { 30% { transform: translateX(-2px); } 100% { transform: none; } }
```
(主体先回缩蓄力再弹出,箭头同时右移 —— 这是 KIMI「主体蓄力、点缀跟随」节奏的最小复刻。)

---

## 5. 命名与工程化约定

KIMI 线上类名形如 `NewChatAnimatedIcon-svg-rs-ew8n6j-p1`,去掉构建 hash 后的
语义结构是 `<Icon>AnimatedIcon-<trigger>-p<index>`。手工维护时建议简化为:

```
keyframes:   icon-<语义>-<部位>-<动作>      例: icon-send-p0-nudge
触发类:      .is-loading / .is-done / .is-armed / :hover
状态类由 JS 增删,动画全部声明在 CSS;JS 不写任何 transition/animation
```

- enter/leave **必须成对**声明,漏写 leave 会导致悬停只播一次;
- 时长/错峰用 CSS 变量暴露,便于全局统一:
  `--icon-step: 90ms; --icon-dur: 0.35s;`

---

## 6. 克制清单(什么时候不要用)

- **静态语义图标不加**(复制/保存/设置):动效必须绑定叙事或状态,纯装饰性抖动是噪声;
- **密集列表不加**:同屏动画元素 > 10 个时观感发躁且费电;
- **表单输入不加**;
- `prefers-reduced-motion` 用户一律静止;
- 每个动画问一句:它传达了什么状态变化?答不上来就删。

> 对照:本项目已落地的微动画(对勾画入、垃圾桶摇晃、箭头推移、按压回弹)
> 全部符合"状态叙事"标准;KIMI 的 shimmer 运行光效也已复刻为进度条/阶段名微光。

---

## 7. 本项目候选制作清单(按需启用)

| 图标 | 部位拆法 | 触发 |
|---|---|---|
| 新建任务「+」 | 竖笔 p0 / 横笔 p1 依次画出 | hover |
| 发送/提交 | 主体 p0 蓄力 + 箭头 p1 跟随 | 点击 |
| 主题切换 | 太阳 p0 / 月亮 p1 旋转互换 | 切换时 |
| 导出/分享 | 托盘 p0 + 箭头 p1 弹出 | hover |
| 任务完成 | 圆 p0 描边画入 + 对勾 p1 画出 | succeeded 事件 |

制作时严格走第 3 节五步;完成后用「第 6 节清单」自检一遍再合入。
