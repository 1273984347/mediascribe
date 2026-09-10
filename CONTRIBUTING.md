# Contributing to MediaScribe / 贡献指南

[English](#english) | [中文](#中文)

---

## English

First off, thank you for considering contributing to MediaScribe! 🎉

### 🐛 Reporting Bugs

Before creating bug reports, please check the existing issues to avoid duplicates. When you create a bug report, please include:

- **Clear title and description**
- **Steps to reproduce**
- **Expected behavior**
- **Actual behavior**
- **Screenshots** (if applicable)
- **Environment**: OS, Python version, package versions
- **Logs** (run with `--log-level DEBUG`)

### 💡 Suggesting Enhancements

Enhancement suggestions are tracked as GitHub issues. When creating an enhancement suggestion, please include:

- **Use case**: What problem does it solve?
- **Proposed solution**: How should it work?
- **Alternatives considered**: What other approaches did you think about?

### 🔧 Pull Requests

1. **Fork the repo** and create your branch from `main`
2. **Install dev dependencies**: `pip install -r requirements-dev.txt`
3. **Make your changes** with clear commit messages
4. **Add tests** for new features
5. **Ensure tests pass**: `python run_tests.py`
6. **Update documentation** if needed
7. **Submit a pull request**

#### Code Style

- Follow [PEP 8](https://pep8.org/)
- Use type hints where appropriate
- Write docstrings (English)
- Keep functions focused and small
- Add comments for complex logic

#### Commit Messages

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

Example: `feat(downloader): add support for YouTube videos`

### 🌍 Internationalization

We support both English and Chinese. When adding user-facing strings:

- **English first** (in code)
- **Chinese equivalent** (in comments or docs)
- Use `gettext` or similar for runtime i18n

### 🧪 Testing

```bash
# Run all tests
python run_tests.py

# Run specific test
python -m unittest douyin_batch.tests.test_basic.TestConfig

# Run with coverage
coverage run -m unittest discover douyin_batch/tests/
coverage report
```

### 📁 Project Structure

When adding new files:

- **Core library** → `mediascribe/`
- **Batch processing** → `douyin_batch/`
- **Tests** → `douyin_batch/tests/`
- **Scripts** → project root
- **Docs** → project root or `docs/`

### 🔒 Security

For security vulnerabilities, please email us directly instead of opening a public issue.

---

## 中文

首先，感谢您考虑为 MediaScribe 做出贡献！🎉

### 🐛 报告 Bug

在创建 bug 报告之前，请先检查现有 issue 以避免重复。创建 bug 报告时，请包含：

- **清晰的标题和描述**
- **复现步骤**
- **预期行为**
- **实际行为**
- **截图**（如适用）
- **环境**：操作系统、Python 版本、依赖版本
- **日志**（使用 `--log-level DEBUG` 运行）

### 💡 功能建议

功能建议以 GitHub issue 形式跟踪。创建功能建议时，请包含：

- **使用场景**：解决什么问题？
- **建议方案**：应该如何工作？
- **备选方案**：考虑过哪些其他方法？

### 🔧 Pull Request 流程

1. **Fork 仓库**并从 `main` 创建分支
2. **安装开发依赖**：`pip install -r requirements-dev.txt`
3. **进行修改**，提交信息要清晰
4. **添加测试**
5. **确保测试通过**：`python run_tests.py`
6. **更新文档**（如需要）
7. **提交 Pull Request**

#### 代码风格

- 遵循 [PEP 8](https://pep8.org/)
- 适当使用类型提示
- 编写 docstring（英文）
- 保持函数简洁
- 为复杂逻辑添加注释

#### 提交信息格式

```
<类型>(<范围>): <主题>

<正文>

<页脚>
```

类型：`feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

### 🌍 国际化

我们支持英文和中文。添加面向用户的字符串时：

- **英文优先**（代码中）
- **中文对应**（注释或文档中）
- 使用 `gettext` 或类似机制进行运行时国际化

### 🧪 测试

```bash
# 运行所有测试
python run_tests.py

# 运行特定测试
python -m unittest douyin_batch.tests.test_basic.TestConfig
```

### 📁 项目结构

新增文件时请遵循：

- **核心库** → `mediascribe/`
- **批处理** → `douyin_batch/`
- **测试** → `douyin_batch/tests/`
- **脚本** → 项目根目录
- **文档** → 项目根目录或 `docs/`

### 🔒 安全

如有安全漏洞，请直接通过邮件联系我们，而不是公开 issue。

---

Thank you for your contribution! / 感谢您的贡献！🙏
