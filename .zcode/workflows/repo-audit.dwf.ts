/* zcode-workflow
description: 全仓审查工作流：五个维度（代码质量/测试与CI/依赖与工具链/安全/架构与文档）并行审查，每条发现由独立复核员实证后才标记
  verified，未证实的保留并标注；最后由撰写员出分级报告。10-03 手工五代理审查的固化版，可季度重跑并与上次报告比对。
whenToUse: 季度体检或大改动后复查。可选 dimensions 子集与 base_ref(只看某 ref 以来改动)。产出与 10-03 同构的分级报告。
args:
  base_ref:
    type: string
    description: "可选: 只审该 ref 以来的改动(如上次审查的 commit), 缺省全仓扫描"
  dimensions:
    type: json
    description: "可选: 要审的维度子集, 缺省全部五个 [代码质量,测试与CI,依赖与工具链,安全,架构与文档]"
*/
interface Finding {
  /** 工作区相对路径, 尽量带行号: "mediascribe/x.py:42" */
  where: string;
  /** 一句话说清问题 */
  what: string;
  /** 证据: 读到的代码/配置, 或命令输出 */
  evidence: string;
  /** high 只留给会丢数据/崩溃/安全实际可利用 */
  severity: "low" | "medium" | "high";
}
interface WorkflowReport {
  conclusion: string;
  findings: (Finding & { status: "verified" | "unconfirmed" })[];
  verified: string[];
  notCovered: string[];
}

const allDims = ["代码质量", "测试与CI", "依赖与工具链", "安全", "架构与文档"];
const dims = Array.isArray(args.dimensions) && args.dimensions.length > 0 ? args.dimensions.map((d) => String(d)) : allDims;
const baseRef = String(args.base_ref ?? "").trim();
const focus: Record<string, string> = {
  代码质量: "巨型函数、平行重复实现、宽泛 except 吞错、死代码、类型债",
  "测试与CI": "测试盲区、marker 是否真生效、workflow 配置矛盾/过时 actions、门禁漏洞、Windows 特有坑",
  依赖与工具链: "版本 pin 漂移、EOL 支持、依赖组合雷、extra 缺漏、锁文件缺失",
  安全: "暴露面/鉴权默认、秘密、注入面、路径穿越、SSRF、依赖 CVE",
  "架构与文档": "目录布局错位、文档与实现漂移、幽灵特性、数据资产治理、入口耦合",
};

phase("五个维度并行审查");
const perDim = await Promise.all(
  dims.map(async (dim) => {
    const review = await agent(`审查员-${dim}`, {
      system: "你是该维度的资深审查者: 只报有 file:line 实证、可操作、确由当前代码引出的问题; 不报风格洁癖与臆测。宁可少报不虚报。",
    }).ask<{ findings: Finding[] }>(
      `对仓库做【${dim}】维度审查, 重点: ${focus[dim] ?? "综合"}。${
        baseRef !== "" ? `只关注 ${baseRef} 以来的改动(可自己跑 git diff/log)。` : "全仓扫描。"
      }
返回 findings 数组, 每条带 where(路径:行)/what/evidence/severity。不要编辑任何文件。`,
    );
    const confirmed = await Promise.all(
      review.findings.map(async (f, i) => {
        const check = await agent(`复核员-${dim}-${i + 1}`, {
          system: "你是独立复核员: 只凭证据自己重现发现, 复现不了就如实标 unconfirmed; 不编辑文件。",
        }).ask<{ confirmed: boolean; note: string }>(
          `独立复核这条审查发现是否属实: ${JSON.stringify(f)}\n自己读代码/跑只读命令验证, 返回 confirmed 与一句话 note。`,
        );
        return { ...f, status: (check.confirmed ? "verified" : "unconfirmed") as "verified" | "unconfirmed", evidence: `${f.evidence} | 复核: ${check.note}` };
      }),
    );
    for (const f of confirmed) {
      report({ where: f.where, what: f.what, severity: f.severity, status: f.status });
    }
    return confirmed;
  }),
);
const allFindings = perDim.flat();

phase("汇总分级报告");
const high = allFindings.filter((f) => f.severity === "high");
const synthesis = await agent("报告撰写员", {
  system: "你写审查报告: 结论先说、分级清楚、每条发现带路径与证据; unconfirmed 如实标注, 不夸大也不软化。",
}).ask<string>(
  `把以下审查发现(JSON)写成中文 markdown 审查报告正文(不加代码围栏): 一段总结(维度/条数/severity 分布/verified 占比) + "发现明细"逐条列出 [severity/status] 路径 — 问题, 下附证据。范围: ${
    baseRef !== "" ? `${baseRef} 以来的改动` : "全仓"
  }。发现:\n${JSON.stringify(allFindings)}`,
);
await artifact.markdown("report", synthesis, {
  title: "全仓审查报告",
  description: `${dims.join("/")} — ${allFindings.length} 条发现`,
  primary: true,
});
return {
  conclusion: `${dims.length} 个维度审出 ${allFindings.length} 条发现(${high.length} high), 其中 ${allFindings.filter((f) => f.status === "verified").length} 条已经独立复核证实。${high.length > 0 ? "high 项建议优先处理。" : ""}`,
  findings: allFindings,
  verified: ["每条发现由独立复核员实证", ...dims.map((d) => `${d}维度扫描`)],
  notCovered: allFindings.filter((f) => f.status === "unconfirmed").length > 0 ? ["标注 unconfirmed 的发现未通过复核, 需人工判断"] : [],
};
