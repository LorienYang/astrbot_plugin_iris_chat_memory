# 项目开发指南

## 项目概览

Iris Chat Memory 是面向 AstrBot 的 Python 插件，入口为 `main.py`。主要业务代码位于 `iris_memory/`，按功能划分为配置、核心生命周期、记忆层、LLM、命令、工具和 Web API；管理界面位于 `iris_memory/web/frontend/`。Python 版本以 `.python-version` 为准（当前为 3.12）。

核心记忆流程为 L1 对话缓冲、L2 语义记忆（FAISS + SQLite）和 L3 知识图谱（SQLite）。改动时沿用对应模块的职责边界，并同步考虑组件生命周期、数据持久化和相关 Web API。

## 开发约定

- 遵循相邻代码的命名、类型标注、异步处理和日志风格；Python 代码使用 Ruff 检查和格式化。
- 新功能或行为修复应在 `tests/` 中添加或更新相应测试，测试按业务模块组织；跨模块流程放在集成或端到端测试中。
- 测试使用临时目录或现有 fixture，不要依赖、覆盖或清理真实插件数据。
- 修改持久化逻辑时保留既有数据兼容性，并优先使用项目已有的持久化工具。
- 前端改动沿用 Vue 3、TypeScript 和现有 API/组件组织方式；若改动前端源码或构建配置，执行前端构建检查。
- 用户可见的功能或配置变化，酌情同步更新 README 或 CHANGELOG。

## 验证命令

安装 Python 依赖：

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
```

运行测试和 Python 质量检查：

```bash
pytest tests/ -v
ruff check .
ruff format --check .
```

运行单个测试文件时，将 `tests/` 替换为对应文件路径。修改前端时，在仓库根目录执行：

```bash
npm --prefix iris_memory/web/frontend ci
npm --prefix iris_memory/web/frontend run build:check
```
