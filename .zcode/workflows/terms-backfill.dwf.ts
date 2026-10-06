/* zcode-workflow
description: 术语库回填守门工作流：新误识对逐条三重查重（现有库/库内互查/语义近似）+
  回源稿核上下文，只放证据充分的条目；落盘后自动跑数据卫生测试与全量测试，全过才提交。防的就是上次 SAFE_TERMS 重复对连挂 5 次 CI 那类事故。
whenToUse: 每次转录交付后回填新误识对时。传入 pairs 数组(必要时附 source 源稿路径), 工作流完成查重→核实→落盘→测试门禁→提交全链路。
args:
  pairs:
    type: json
    description: "新误识对数组, 每项 {wrong: 误识写法, right: 正确写法, note?: 来源备注}"
    required: true
  push:
    type: boolean
    description: 门禁全过后是否自动 git push
    default: false
  source:
    type: string
    description: 源稿/转录稿路径, 供逐条回源核实上下文(强烈建议提供)
*/
interface NewPair {
  /** ASR 误识写法 */
  wrong: string;
  /** 正确写法 */
  right: string;
  /** 备注: 来源集数/语境 */
  note?: string;
}
interface PairVerdict {
  wrong: string;
  right: string;
  /** keep=确认新增; drop=与现有库重复或依据不足; merge=与本次其他新对重复 */
  decision: "keep" | "drop" | "merge";
  /** 一句话理由, 必须带证据位置(文件:行 或 源稿位置) */
  reason: string;
  /** decision=merge 时并入的目标对 "wrong=>right" */
  mergesInto?: string;
}
interface CuratedSet {
  /** 语义查重后确认入库的条目 */
  final: { wrong: string; right: string }[];
  /** 语义查重阶段被合并掉的对与原因 */
  merged: { pair: string; into: string; reason: string }[];
}
interface ApplyResult {
  /** 实际写入的条数 */
  written: number;
  /** 写到了哪些文件(逗号分隔) */
  files: string;
}
interface Finding {
  where: string;
  what: string;
  evidence: string;
  status: "verified" | "unconfirmed";
  severity: "low" | "medium" | "high";
}
interface WorkflowReport {
  conclusion: string;
  findings: Finding[];
  verified: string[];
  notCovered: string[];
}

const rawPairs = args.pairs;
if (!Array.isArray(rawPairs) || rawPairs.length === 0) {
  throw new Error("pairs 参数必须是非空数组, 每项 {wrong, right, note?}");
}
const pairs: NewPair[] = rawPairs
  .map((p) => {
    const o = p as Record<string, unknown>;
    return {
      wrong: String(o.wrong ?? "").trim(),
      right: String(o.right ?? "").trim(),
      note: o.note === undefined ? undefined : String(o.note),
    };
  })
  .filter((p) => p.wrong !== "" && p.right !== "");
if (pairs.length === 0) {
  throw new Error("pairs 里没有有效的 wrong/right 条目");
}
const source = String(args.source ?? "").trim();
const doPush = args.push === true;

phase("逐条核实新术语对");
log(`待核实 ${pairs.length} 对新术语`);
const verdicts = await Promise.all(
  pairs.map((p, i) =>
    agent(`术语核查员-${i + 1}`, {
      system: "你是 ASR 术语库的守门人: 宁缺毋滥, 只放证据充分的条目。若指令互相矛盾或无法核实, 如实说明而不是硬给结论。",
    }).ask<PairVerdict>(
      `核实这条新误识对是否该入库: ${JSON.stringify(p)}
步骤:
1. 查重 — 读 scripts/seed_learned_terms.py 里的 SAFE_TERMS(以及仓库里能找到的 learned_terms.json), 精确重复与语义近似(同义/变体/仅标点差异)都算重复;
2. 上下文核实 — ${
        source
          ? `读源稿 ${source}, 确认 wrong 确实出现且 right 是正确写法, 并主动找反例(right 反而错的位置)`
          : "未提供源稿, 只按常识判断该纠正是否成立, 把握不足一律 drop"
      };
3. 裁定 keep / drop / merge, reason 必须带证据位置(文件:行)。
不要编辑任何文件, 只做核查。`,
    ),
  ),
);
const keeps = verdicts.filter((v) => v.decision === "keep");
const drops = verdicts.filter((v) => v.decision !== "keep");
for (const d of drops) {
  report({ wrong: d.wrong, right: d.right, decision: d.decision, reason: d.reason });
}
log(`核查完成: ${keeps.length} 条拟入库, ${drops.length} 条被挡下`);

let curated: CuratedSet = { final: keeps.map((k) => ({ wrong: k.wrong, right: k.right })), merged: [] };
if (keeps.length > 1) {
  phase("对新条目做语义查重");
  curated = await agent("查重仲裁员", {
    system: "你只做跨条目语义查重: 变体、包含关系、同义、仅标点差异都算同一条; 输出最终集合。",
  }).ask<CuratedSet>(
    `下面是逐条核查后拟入库的新术语对, 做一次跨条目语义查重并给出最终集合:\n${keeps
      .map((k, i) => `${i + 1}. ${k.wrong} => ${k.right}`)
      .join("\n")}`,
  );
}
for (const m of curated.merged) {
  report({ decision: "merge", pair: m.pair, into: m.into, reason: m.reason });
}

let applied: ApplyResult | null = null;
if (curated.final.length > 0) {
  phase("落盘并过数据卫生门禁");
  let gateFeedback = "无(首轮)";
  const applier = agent("术语落盘员", {
    system: "你按仓库既有惯例落盘数据并让门禁通过; 做不到就如实说明卡点, 不要伪造结果。",
  });
  for (let round = 1; round <= 3; round++) {
    const res = await applier.ask<ApplyResult>(
      `把以下 ${curated.final.length} 条术语对按仓库既有惯例入库, 注意与现有条目查重、绝不产生重复:\n${curated.final
        .map((p) => `${p.wrong} => ${p.right}`)
        .join("\n")}
参考: 历史落盘走 scripts/seed_learned_terms.py 的 SAFE_TERMS(有去重硬拦截), 并按该脚本说明同步 learned_terms.json。
上一轮门禁反馈: ${gateFeedback}
只做落盘编辑, 不要自己跑测试(外层门禁会跑), 返回写入条数与文件列表。`,
    );
    const hygiene = await world.run("python", ["-m", "pytest", "tests/test_detect_repeated_pairs.py", "-q"]);
    if (hygiene.exitCode !== 0) {
      gateFeedback = hygiene.stdout + "\n" + hygiene.stderr;
      log("数据卫生测试未过, 进入下一轮修复");
      continue;
    }
    const full = await world.run(
      "python",
      ["-m", "pytest", "tests/", "-q", "-m", "not integration and not network", "--ignore=tests/test_e2e_real_urls.py"],
      { timeoutMs: 600000 },
    );
    if (full.exitCode !== 0) {
      gateFeedback = full.stdout + "\n" + full.stderr;
      log("全量测试未过, 进入下一轮修复");
      continue;
    }
    applied = res;
    break;
  }

  if (applied !== null) {
    phase("提交并按需推送");
    // add -A: 落盘员可能按惯例动了种子文件之外的相关文件, 全部纳入本提交
    await world.run("git", ["add", "-A"]);
    const committed = await world.run("git", [
      "commit",
      "-m",
      `chore(terms): workflow 回填 ${applied.written} 对新误识术语(三重查重+门禁通过)`,
    ]);
    if (committed.exitCode !== 0) {
      log("commit 未产生变更(可能没有实际改动)");
    }
    const leftover = await world.run("git", ["status", "--short"]);
    if (leftover.stdout.trim() !== "") {
      log(`提交后工作区仍有残留:\n${leftover.stdout.trim()}`);
    }
    if (doPush) {
      await world.run("git", ["push"]);
    }
  }
}

const keptCount = applied !== null ? applied.written : 0;
const md = [
  `# 术语回填报告`,
  "",
  `- 提交核查: ${pairs.length} 对`,
  `- 逐条核查后拟入库: ${keeps.length} 对; 语义查重后最终入库: ${curated.final.length} 对`,
  `- 实际写入: ${keptCount} 条 (${applied !== null ? applied.files : "门禁未过, 未写入"})`,
  `- 门禁: 数据卫生测试 + 全量测试 (3 轮上限)${doPush ? " + git push" : ""}`,
  "",
  `## 被挡下的条目 (${drops.length + curated.merged.length})`,
  ...[...drops, ...curated.merged.map((m) => ({ wrong: m.pair, right: m.into, decision: "merge", reason: m.reason }))]
    .map((d) => `- ${d.wrong} => ${d.right}: ${d.decision} — ${d.reason}`),
].join("\n");
await artifact.markdown("report", md, { title: "术语回填报告", description: `${pairs.length} 对送审, 入库 ${keptCount} 条`, primary: true });

const findings: Finding[] =
  applied === null && curated.final.length > 0
    ? [
        {
          where: "scripts/seed_learned_terms.py",
          what: `有 ${curated.final.length} 条通过核查但 3 轮内未过门禁, 未落盘`,
          evidence: "数据卫生/全量测试存在持续失败",
          status: "verified",
          severity: "medium",
        },
      ]
    : [];
return {
  conclusion:
    curated.final.length === 0
      ? `${pairs.length} 对送审, 全部被查重或证据不足挡下, 无入库。`
      : applied !== null
        ? `${pairs.length} 对送审, 最终入库 ${keptCount} 条, 数据卫生与全量测试通过${doPush ? ", 已推送" : "。请走 pre-push 门禁后推送"}。`
        : `${pairs.length} 对送审, ${curated.final.length} 条拟入库但门禁 3 轮未过, 需要人工看门禁日志。`,
  findings,
  verified: ["每对独立核查(查重+上下文)", "跨条目语义查重", "python -m pytest 数据卫生 + 全量套件(世界命令门禁)"],
  notCovered: source ? [] : ["未提供源稿, 上下文核实只能到常识层面"],
};
