## 3.4.0a1 — 2026-10-29(预发布,发布链端到端验证)

对抗性审查(2026-10-29)修复 + 拍板五项落地。完整清单见 [CHANGELOG](https://github.com/1273984347/mediascribe/blob/main/CHANGELOG.md)。

### Security
- 平台路由改主机名精确匹配,微信 cookies 仅发腾讯系域名(P0-1 凭据外泄链封堵)
- MCP `validate_url` 改域名白名单检查;`TRUSTED_DOMAINS` 与平台指纹表一致性入测试

### Fixed
- 硬件自适应换引擎 whisper-cpp 前先验可用性,Mac/小内存机器不再转录期硬失败(P0-2)
- whisperx 模型缓存键补 language(Web 混语言不再静默错用模型)
- whisperx/whisper 模型缓存加 LRU 上限;whisper.cpp `.wav` 强制 16k 转码
- 快速上手/公众号文档按真实 CLI 重写
- CI:nightly 失败通知改独立 job(原实现 lint 失败零通知);runner 钉 ubuntu-24.04;
  release 链两处 bug(dispatch 默认 tag / release notes 选择)

### Changed
- 布尔 env 词表统一 `1/true/yes/on`;删除 whisper→faster-whisper 静默改写
  (Web 提交 engine=whisper 现在真跑 openai-whisper)
- 流式下载大小上限(默认 4096 MiB,`MEDIASCRIBE_MAX_DOWNLOAD_MB` 可调)
- 用户可见字符串全面走 i18n `t()`(AST 门禁强制),新增模块 docstring 全量英文
