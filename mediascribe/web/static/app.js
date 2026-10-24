// MediaScribe 工作台入口 —— 零构建，原生 ES modules。
// 导入即执行：各模块在导入时完成自己的初始化（主题、GPU 轮询、表单绑定）。
import "./modules/theme.js";
import "./modules/status.js";
import "./modules/gpu.js";
import "./modules/auth.js";
import "./modules/jobs.js";
