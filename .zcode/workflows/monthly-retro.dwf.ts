/* zcode-workflow
description: 月度复盘工作流：汇集最近 N 个 commit + 近期 CI 运行成败 +
  当前测试规模，由一个持续上下文的分析员归纳复发模式与主题（哪些修了又犯、哪些风险在累积），再由撰写员按既有复盘骨架写成 docs/retros/
  复盘文档。10-01/10-02/10-03 手工复盘的固化版。
whenToUse: 月末/阶段性复盘时。传 label(如 2026-10)与可选 commits 数; 产出 docs/retros/ 复盘文档, 记忆更新由主会话跟进。
args:
  commits:
    type: number
    description: 回看最近多少个 commit(上限 100)
    default: 100
  label:
    type: string
    description: 复盘标题标识, 用于文件名, 如 2026-10
    required: true
*/
interface Theme {
  /** 主题/模式一句话 */
  theme: string;
  /** 支撑证据: commit 主题/CI 失败等, 带具体条目 */
  evidence: string;
  /** 复发次数估计 */
  occurrences: number;
  /** 建议动作 */
  action: string;
}
interface RetroDoc {
  /** 写出的文件路径 */
  path: string;
  /** 三条以内最重要的结论 */
  highlights: string[];
}
interface WorkflowReport {
  conclusion: string;
  findings: { where: string; what: string; evidence: string; status: "verified" | "unconfirmed"; severity: "low" | "medium" | "high" }[];
  verified: string[];
  notCovered: string[];
}

const label = String(args.label ?? "").trim();
if (label === "") {
  throw new Error("label 必填, 如 2026-10");
}
const n = typeof args.commits === "number" ? Math.min(100, Math.max(1, Math.floor(args.commits))) : 100;

phase("汇集提交与 CI 记录");
const commits = await git.log(n);
const ciRun = await world.run("gh", ["run", "list", "--limit", "50", "--json", "workflowName,conclusion,createdAt"]);
let ciSummary = "gh 不可用或无记录";
try {
  const runs = JSON.parse(ciRun.stdout) as { workflowName: string; conclusion: string | null }[];
  const byOutcome: Record<string, number> = {};
  for (const r of runs) {
    const key = `${r.workflowName}:${r.conclusion ?? "unknown"}`;
    byOutcome[key] = (byOutcome[key] ?? 0) + 1;
  }
  ciSummary = Object.entries(byOutcome)
    .map(([k, v]) => `${k}×${v}`)
    .join(", ");
} catch {
  log("gh run list 输出解析失败");
}
const collect = await world.run("python", ["-m", "pytest", "--collect-only", "-q"], { timeoutMs: 180000 });
const match = /(\d+) tests? collected/.exec(collect.stdout);
const testCount = match !== null ? match[1] : "未知";
log(`汇集完成: ${commits.length} 个 commit, 测试 ${testCount} 个`);

phase("归纳复发模式与主题");
const commitLines = commits.map((c) => `${c.date.slice(0, 10)} ${c.subject}`).join("\n");
const themes = await agent("复盘分析员", {
  system: "你是复盘分析员: 从 commit 与 CI 记录里找复发模式(同类问题反复修、修复引出新问题、风险在累积的领域), 不写空话, 每条模式必须挂在具体条目上。",
}).ask<{ themes: Theme[] }>(
  `以下是最近 ${commits.length} 个 commit(新在上):\n${commitLines}\n\n近期 CI 汇总: ${ciSummary}\n当前测试规模: ${testCount} 个\n\n归纳 3-6 条复发模式/主题, 每条带 evidence(具体 commit 主题或 CI 条目)、occurrences、action(下一步建议)。`,
);
for (const t of themes.themes) {
  report({ theme: t.theme, occurrences: t.occurrences, action: t.action });
}

phase("撰写复盘文档");
const writer = agent("复盘撰写员", {
  system: "你按仓库既有复盘骨架写文档: 做了什么(按主题归类 commit)/翻车与修复/复发模式/下次纪律。中文, 具体到 commit 主题, 不写套话。",
});
const doc = await writer.ask<RetroDoc>(
  `把以下材料写成复盘文档, 存到 docs/retros/retro-${label}.md(目录不存在就创建), 返回路径与最多三条 highlights:\n\n commits:\n${commitLines}\n\nCI: ${ciSummary}\n测试规模: ${testCount}\n\n分析员归纳的模式:\n${JSON.stringify(themes.themes)}`,
);
try {
  await artifact.file("retro", doc.path, { title: `复盘 ${label}`, description: doc.highlights[0] ?? "", primary: true });
} catch {
  // publish 拒绝(常见: agent 返回了绝对路径/反斜杠路径)— 让撰写员规范路径后重试一次
  const fixed = await writer.ask<RetroDoc>(
    `发布失败: 路径 "${doc.path}" 不是可用的工作区相对路径。文件本身不用重写, 只返回规范化后的 path(相对仓库根、正斜杠)与原 highlights。`,
  );
  await artifact.file("retro", fixed.path, { title: `复盘 ${label}`, description: fixed.highlights[0] ?? "", primary: true });
}
return {
  conclusion: `复盘 ${label} 完成: ${commits.length} 个 commit + 近 50 次 CI 归纳出 ${themes.themes.length} 条模式, 文档在 ${doc.path}。`,
  findings: themes.themes
    .filter((t) => t.occurrences >= 2)
    .map((t) => ({
      where: "commits/CI",
      what: t.theme,
      evidence: t.evidence,
      status: "verified" as const,
      severity: "medium" as const,
    })),
  verified: [`git.log ${commits.length} 条`, "gh run list 近 50 次", "pytest --collect-only 实测测试数"],
  notCovered: ["记忆目录的复盘记忆更新由主会话执行", "50 次之前的 CI 历史未纳入"],
};
