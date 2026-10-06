/* zcode-workflow
description: Dependabot PR 分诊：列出仓库 open 的 dependabot PR，逐个由 agent 读
  diff/changelog/影响面给出"安全可并/需缓"裁决（附理由与风险），汇总成裁决表；merge_safe=true 时对安全项逐个落地。针对 CI
  首推通过率痛点的每周例行事。
whenToUse: 每周 dependabot PR 攒了一堆时。默认只出裁决表; merge_safe=true 才会真合并(合的是判定为安全的)。
args:
  merge_safe:
    type: boolean
    description: true=对裁定为安全的 PR 逐个 squash 合并(默认只出裁决不动手)
    default: false
*/
interface PrInfo {
  number: number;
  title: string;
  author: string;
}
interface Verdict {
  number: number;
  title: string;
  /** true=例行升级、影响面小、测试可兜底; false=大版本跳跃/破坏性变更/需要人工评估 */
  safe: boolean;
  /** low / medium / high */
  risk: string;
  /** 一句话理由, 提到关键版本点 */
  reason: string;
  /** 合并后需要跑什么来兜底 */
  testsNeeded: string;
}
interface WorkflowReport {
  conclusion: string;
  findings: { where: string; what: string; evidence: string; status: "verified" | "unconfirmed"; severity: "low" | "medium" | "high" }[];
  verified: string[];
  notCovered: string[];
}

artifact.table("verdicts", {
  title: "Dependabot PR 裁决",
  key: "number",
  columns: [
    { field: "number", label: "PR" },
    { field: "title", label: "标题" },
    { field: "safe", label: "安全" },
    { field: "risk", label: "风险" },
    { field: "reason", label: "理由" },
  ],
});

phase("列出待分诊的 Dependabot PR");
const listed = await world.run("gh", ["pr", "list", "--state", "open", "--json", "number,title,author"]);
let prs: PrInfo[] = [];
let queryError = "";
if (listed.exitCode !== 0) {
  queryError = `gh 查询失败(退出码 ${listed.exitCode}): ${listed.stderr.slice(0, 200)}`;
  log(queryError);
} else {
  try {
    const parsed = JSON.parse(listed.stdout) as { number: number; title: string; author: { login: string } }[];
    prs = parsed
      .filter((p) => p.author.login.toLowerCase().includes("dependabot"))
      .map((p) => ({ number: p.number, title: p.title, author: p.author.login }));
  } catch {
    queryError = `gh 输出解析失败: ${listed.stdout.slice(0, 200)}`;
    log(queryError);
  }
}
log(`open 的 dependabot PR: ${prs.length} 个`);
if (prs.length === 0) {
  return {
    conclusion:
      queryError !== ""
        ? `无法分诊: ${queryError}。未做任何裁决, 请检查 gh 认证/网络后重跑。`
        : "当前没有 open 的 dependabot PR, 无事可做。",
    findings: [],
    verified: queryError !== "" ? [] : ["gh pr list 实时查询"],
    notCovered: queryError !== "" ? ["全部 — gh 查询失败, 没有任何 PR 被评估"] : [],
  };
}

phase("逐个评估影响面");
const verdicts = await Promise.all(
  prs.map((pr) =>
    agent(`评估员-PR${pr.number}`, {
      system: "你是依赖升级评估员: 看 diff 与 changelog 判断影响面; 大版本跳跃、锁文件外变更、碰运行关键路径(torch/playwright/whisper/CI workflow)的一律不标 safe。宁可保守。",
    }).ask<Verdict>(
      `评估 dependabot PR #${pr.number} "${pr.title}": 自己跑 gh pr diff ${pr.number} 与 gh pr view ${pr.number} 看改动, 结合本仓库 pyproject/.github 的约束(如 ruff 0.16.x pin、mypy <3、playwright 历史教训)给出 safe/risk/reason/testsNeeded。只读, 不改任何东西。`,
    ),
  ),
);
for (const v of verdicts) {
  report(v, "verdicts");
}

phase("汇总并按需落地");
const mergeSafe = args.merge_safe === true;
const safeList = verdicts.filter((v) => v.safe);
const merged: string[] = [];
if (mergeSafe && safeList.length > 0) {
  for (const v of safeList) {
    const res = await world.run("gh", ["pr", "merge", String(v.number), "--squash"]);
    merged.push(res.exitCode === 0 ? `#${v.number} 已 squash 合并` : `#${v.number} 合并失败(exit ${res.exitCode})`);
    log(merged[merged.length - 1]);
  }
}
const md = [
  `# Dependabot 分诊(${verdicts.length} 个 PR)`,
  "",
  ...verdicts.map(
    (v) => `- #${v.number} ${v.title}\n  safe=${v.safe} risk=${v.risk} — ${v.reason}${v.testsNeeded !== "" ? `\n  兜底: ${v.testsNeeded}` : ""}`,
  ),
  ...(merged.length > 0 ? ["", "## 落地", ...merged.map((m) => `- ${m}`)] : []),
].join("\n");
await artifact.markdown("report", md, {
  title: "Dependabot 分诊报告",
  description: `${verdicts.length} 个 PR, ${safeList.length} 个判定安全`,
  primary: true,
});
return {
  conclusion: `${verdicts.length} 个 dependabot PR 分诊完成: ${safeList.length} 个可安全合并${mergeSafe ? (merged.length > 0 ? `, 已落地 ${merged.length} 个` : ", 但落地失败需人工看") : "(merge_safe=false, 未动手, 报告里有逐个理由)"}。`,
  findings: verdicts
    .filter((v) => !v.safe)
    .map((v) => ({
      where: `PR #${v.number}`,
      what: v.title,
      evidence: v.reason,
      status: "verified" as const,
      severity: v.risk === "high" ? ("high" as const) : ("medium" as const),
    })),
  verified: ["每个 PR 由评估员实读 diff/changelog", "gh pr list 实时数据"],
  notCovered: mergeSafe ? [] : ["未实际合并 — merge_safe=false, 落地由人工决定"],
};
