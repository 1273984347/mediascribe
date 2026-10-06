/* zcode-workflow
description: 存量 wiki 双重校验：按篇 fan-out，每篇由一个 agent 做"解开"（逐条提取事实断言并核对）+
  "反证"（主动找与断言矛盾的证据），标"错误"的断言再由独立复核员确认，产出修订建议报告（只建议不改动，落不落由人工拍板）。甄嬛传等 4 篇 v2
  校验的批量化版本。
whenToUse: 批量校验存量转录稿/审校稿的事实准确性时。传 docs 路径数组; 只产出修订建议, 改不改由人工拍板。
args:
  docs:
    type: json
    description: 要校验的文档路径数组(如 ["output/wiki/甄嬛传.md", ...])
    required: true
*/
interface Issue {
  /** 被核验的断言 */
  claim: string;
  /** 错误=有反证; 存疑=找不到支撑; 成立=有依据 */
  verdict: "错误" | "存疑" | "成立";
  /** 证据位置(原文/外部事实来源) */
  evidence: string;
  /** 错误/存疑时给修订建议 */
  suggestion: string;
}
interface DocVerdict {
  doc: string;
  issues: Issue[];
}
interface WorkflowReport {
  conclusion: string;
  findings: { where: string; what: string; evidence: string; status: "verified" | "unconfirmed"; severity: "low" | "medium" | "high" }[];
  verified: string[];
  notCovered: string[];
}

const raw = args.docs;
if (!Array.isArray(raw) || raw.length === 0) {
  throw new Error("docs 参数必须是非空路径数组");
}
const docs = raw.map((d) => String(d));

phase("逐篇解开与反证");
const perDoc = await Promise.all(
  docs.map((doc, i) =>
    agent(`校验员-${i + 1}`, {
      system: "你是事实校验员, 做两层: 先'解开'(提取文中全部可核验断言), 再'反证'(主动找与断言矛盾的证据, 外部史实凭你的知识判断并注明不确定性)。不编辑文件。",
    }).ask<DocVerdict>(
      `对文档 ${doc} 做双重校验: 解开全部事实断言(人物/史实/引文/数字/因果), 逐条给出 verdict(错误/存疑/成立)+证据+修订建议。小事(纯观点/修辞)不进 issues。`,
    ),
  ),
);

phase("独立复核'错误'判定");
type Confirmed = { doc: string; issue: Issue; status: "verified" | "unconfirmed" };
const flagged: Confirmed[] = (
  await Promise.all(
    perDoc.map(async (dv, docIndex) =>
      Promise.all(
        dv.issues
          .filter((iss) => iss.verdict === "错误")
          .map(async (iss, i) => {
            const check = await agent(`复核员-文档${docIndex + 1}-${i + 1}`, {
              system: "你是独立复核员: 只凭证据重现判定, 判定站不住就标 unconfirmed; 不编辑文件。",
            }).ask<{ confirmed: boolean; note: string }>(
              `独立复核这条"错误"判定是否站得住: 文档 ${dv.doc} 的断言「${iss.claim}」, 判为错误的证据: ${iss.evidence}。返回 confirmed 与一句话 note。`,
            );
            return { doc: dv.doc, issue: iss, status: (check.confirmed ? "verified" : "unconfirmed") as "verified" | "unconfirmed", note: check.note };
          }),
      ),
    ),
  )
).flat();
const confirmedFlagged: Confirmed[] = flagged.map((f) => ({
  doc: f.doc,
  issue: { ...f.issue, evidence: `${f.issue.evidence} | 复核: ${"note" in f ? String((f as { note?: string }).note ?? "") : ""}` },
  status: f.status,
}));
for (const f of confirmedFlagged) {
  report({ doc: f.doc, claim: f.issue.claim, verdict: f.issue.verdict, status: f.status });
}

const allIssues = perDoc.flatMap((d) => d.issues);
const md = [
  `# 存量 wiki 双重校验报告(${docs.length} 篇)`,
  "",
  `断言 ${allIssues.length} 条: 错误 ${allIssues.filter((i) => i.verdict === "错误").length}(其中复核证实 ${confirmedFlagged.filter((f) => f.status === "verified").length}), 存疑 ${allIssues.filter((i) => i.verdict === "存疑").length}, 成立 ${allIssues.filter((i) => i.verdict === "成立").length}`,
  "",
  ...perDoc.map(
    (d) =>
      `## ${d.doc}\n` +
      d.issues
        .filter((iss) => iss.verdict !== "成立")
        .map((iss) => `- [${iss.verdict}] ${iss.claim}\n  证据: ${iss.evidence}\n  建议: ${iss.suggestion}`)
        .join("\n"),
  ),
].join("\n");
await artifact.markdown("report", md, {
  title: "wiki 双重校验报告",
  description: `${docs.length} 篇, ${allIssues.filter((i) => i.verdict !== "成立").length} 条待处理`,
  primary: true,
});
return {
  conclusion: `${docs.length} 篇校验完成: 判错 ${allIssues.filter((i) => i.verdict === "错误").length} 条(证实 ${confirmedFlagged.filter((f) => f.status === "verified").length}), 存疑 ${allIssues.filter((i) => i.verdict === "存疑").length}。修订建议只在报告里, 未改任何文件。`,
  findings: confirmedFlagged.map((f) => ({
    where: f.doc,
    what: f.issue.claim,
    evidence: f.issue.evidence,
    status: f.status,
    severity: "medium" as const,
  })),
  verified: ["每篇解开+反证双层校验", "判'错误'的断言经独立复核"],
  notCovered: ["'成立'与'存疑'判定未做二次复核", "文档只此清单, 其余存量未动"],
};
