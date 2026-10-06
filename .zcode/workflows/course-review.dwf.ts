/* zcode-workflow
description: 审校稿修订管线（多集课程）：按集 fan-out 跑四步修订（术语替换→幻觉重复块核对→尾部核对→按 output/wiki/
  统一格式整理），再对抽样集做蒸馏交叉抽检，汇总审校说明。纪律（agent 只读源稿、术语替换先核上下文、尾部核对逐集做）写进每集的门。
whenToUse: 多集课程批量审校时（如徐涛系列续更）。传 course_dir, 可选 episodes/sample_n/apply。课程更新后续跑只需再执行一次。
args:
  apply:
    type: boolean
    description: true=直接写入审校稿; false=只出报告不改文件
    default: true
  course_dir:
    type: string
    description: 课程目录(工作区相对路径), 如 output/wiki/徐涛考研政治基础班
    required: true
  episodes:
    type: json
    description: "可选: 显式列出要修订的源稿文件名(相对 course_dir); 缺省自动盘点"
  sample_n:
    type: number
    description: 蒸馏交叉抽检的集数
    default: 3
*/
interface EpisodeDoc {
  /** 相对 course_dir 的文件路径 */
  file: string;
  /** 源稿 / 审校稿 / 其他 */
  kind: string;
}
interface EpisodeReview {
  file: string;
  /** 替换的术语条数 */
  termsApplied: number;
  /** 修掉的幻觉重复块/段级问题数 */
  repetitionsFixed: number;
  /** 尾部核对发现的问题(空串=无) */
  tailIssue: string;
  /** 写出的审校稿路径; apply=false 时为空 */
  outputPath: string;
  /** 需要人工裁决的存疑点(可为空) */
  warnings: string[];
}
interface CrossCheck {
  file: string;
  /** 抽检发现的不实/存疑蒸馏点, 一条一句 */
  discrepancies: string[];
}
interface WorkflowReport {
  conclusion: string;
  findings: { where: string; what: string; evidence: string; status: "verified" | "unconfirmed"; severity: "low" | "medium" | "high" }[];
  verified: string[];
  notCovered: string[];
}

const dir = String(args.course_dir ?? "").trim();
if (dir === "") {
  throw new Error("course_dir 必填, 如 output/wiki/徐涛考研政治基础班");
}
const apply = args.apply !== false;
const sampleN = typeof args.sample_n === "number" && args.sample_n > 0 ? Math.floor(args.sample_n) : 3;
const explicit = Array.isArray(args.episodes) ? args.episodes.map((e) => String(e)) : [];

let sourceFiles: string[] = [];
if (explicit.length > 0) {
  sourceFiles = explicit.map((f) => `${dir}/${f}`);
  log(`使用显式指定的 ${sourceFiles.length} 个源稿`);
} else {
  phase("盘点课程稿件");
  const allMd = await files.glob(`${dir}/**/*.md`);
  const inventory = await agent("盘点员").ask<{ episodes: EpisodeDoc[] }>(
    `目录 ${dir} 下有以下 markdown 文件:\n${allMd.join("\n")}\n请按文件名与(抽查时的)内容把它们分类: kind=源稿(转写原始稿/待修订稿)、审校稿(已是修订交付物)、其他。`,
  );
  sourceFiles = inventory.episodes
    .filter((e) => e.kind === "源稿")
    .map((e) => (e.file.startsWith(dir) ? e.file : `${dir}/${e.file}`));
  log(`盘点出 ${sourceFiles.length} 个源稿`);
}
if (sourceFiles.length === 0) {
  return {
    conclusion: `在 ${dir} 没有盘点出可修订的源稿, 未做任何修改。`,
    findings: [],
    verified: [],
    notCovered: ["全部 — 没有源稿"],
  };
}

phase("逐集四步修订");
const reviews = await Promise.all(
  sourceFiles.map((f, i) =>
    agent(`修订员-${i + 1}`, {
      system:
        "你是审校稿修订员, 纪律: 只把源稿当事实依据(agent 只读源稿); 术语替换前先核上下文, 不确定的不替换; 尾部核对逐集必做; 修不动就如实说。若指令矛盾, 直接说明而不是硬做。",
    }).ask<EpisodeReview>(
      `对源稿 ${f} 执行四步修订:
1. 术语替换 — 术语表读 scripts/seed_learned_terms.py 的 SAFE_TERMS, 只替换上下文成立的;
2. 幻觉重复块核对 — 段级/句级复读、跨段重叠, 逐个处理;
3. 尾部核对 — 比对稿子末段时间戳/内容是否完整, 缺口如实记录;
4. 按仓库统一格式整理审校稿: 基本信息 / 蒸馏 / 审校版正文 / 审校说明。
${
  apply
    ? `把审校稿写到源稿同目录(文件名在原名基础上加"-审校"后缀), 并在 outputPath 返回该路径。`
    : `不要写任何文件, outputPath 留空, 只在返回里报告你本应做的修改。`
}
termsApplied/repetitionsFixed/tailIssue/warnings 按实际填写。`,
    ),
  ),
);

phase("蒸馏交叉抽检");
const stride = Math.ceil(reviews.length / sampleN);
const sampled = reviews.filter((_, i) => i % stride === 0).slice(0, sampleN);
const checks = await Promise.all(
  sampled.map((r, i) =>
    agent(`抽检员-${i + 1}`, {
      system: "你是蒸馏抽检员: 只核对审校稿的蒸馏/事实陈述是否被源稿支持, 找错而不是点头。",
    }).ask<CrossCheck>(
      `读源稿 ${r.file}${r.outputPath !== "" ? ` 与审校稿 ${r.outputPath}` : "(apply=false, 无审校稿, 跳过本项并返回空 discrepancies)"}, 核对审校稿蒸馏部分每条陈述是否有源稿依据, 列出全部不实/存疑点。`,
    ),
  ),
);

phase("汇总审校说明");
const findings: WorkflowReport["findings"] = [];
for (const c of checks) {
  for (const d of c.discrepancies) {
    findings.push({ where: c.file, what: d, evidence: "蒸馏交叉抽检", status: "verified", severity: "medium" });
  }
}
for (const r of reviews) {
  for (const w of r.warnings) {
    findings.push({ where: r.file, what: w, evidence: "修订员存疑标记", status: "unconfirmed", severity: "low" });
  }
}
const summary = [
  `# 审校说明（${sourceFiles.length} 集）`,
  "",
  ...reviews.map(
    (r) =>
      `- ${r.file}: 术语 ${r.termsApplied} 处, 重复块 ${r.repetitionsFixed} 处, 尾部${r.tailIssue === "" ? "完整" : `问题: ${r.tailIssue}`}${r.outputPath !== "" ? `, 产出 ${r.outputPath}` : ", 未写文件"}`,
  ),
  "",
  `## 抽检 (${checks.length} 集)`,
  ...checks.flatMap((c) =>
    c.discrepancies.length === 0 ? [`- ${c.file}: 蒸馏全部有据`] : c.discrepancies.map((d) => `- ${c.file}: ${d}`),
  ),
].join("\n");
if (apply) {
  const note = await agent("审校说明撰写员").ask<string>(
    `根据以下汇总写一份"审校说明"markdown 正文(不加代码围栏), 存到 ${dir}/审校说明.md, 并原样返回正文:\n${summary}`,
  );
  await artifact.markdown("report", note, { title: "审校说明", description: `${sourceFiles.length} 集修订 + ${checks.length} 集抽检`, primary: true });
} else {
  await artifact.markdown("report", summary, { title: "审校报告(未写文件)", primary: true });
}
return {
  conclusion: `${sourceFiles.length} 集完成四步修订${apply ? "并写出审校稿" : "(dry-run 未写文件)"}, 抽检 ${checks.length} 集发现 ${findings.filter((f) => f.status === "verified").length} 处蒸馏疑点。`,
  findings,
  verified: [`逐集四步修订(只读源稿纪律)`, `蒸馏交叉抽检 ${checks.length} 集`],
  notCovered: [`未抽检的 ${reviews.length - checks.length} 集蒸馏未做二次核验`],
};
