# 协作规范（CONTRIBUTING）

> 千摊智脑 · 2026-08-17 起生效。本文件是仓库协作的唯一权威规范。

## 一、分支与推送策略（核心规则）

**远程唯一推送分支：`develop`。**

- 所有对远程的更新一律通过 `git push origin develop` 完成。
- **禁止**推送任何其他分支到远程（包括 `git push -u origin <新分支>`）。
- 本地可以自由开特性分支做隔离实验，但发布时必须先合回 `develop`：

  ```bash
  git switch develop
  git merge <你的本地分支>
  git push origin develop
  ```

- 若在非 develop 分支上产生了提交：合回 develop 再推 develop，不要推送该分支本身。
- PR（如有需要）从 develop 向 main 发起，不从特性分支发起。

## 二、提交规范

- 格式：`<type>: <描述>`，类型取 `feat / fix / refactor / docs / test / chore / perf / ci`。
- 描述用中文，概括「哪一轮/哪个主题 + 关键改动」，例：`fix: 第六轮产品第一性审计全量修复（实测P0/语音金额/迁移对齐）`。
- 多主题大批量改动在正文分节列要点与验证结果（测试数字、门禁结果）。

## 三、质量门禁（提交前必须全绿）

| 范围 | 命令 | 要求 |
|---|---|---|
| 后端全量 | `cd backend && ./scripts/check.sh`（= ruff + mypy + pytest） | pytest 全绿，ruff `app/ tests/ migrations/` 零错误 |
| 单项快跑 | `.venv\Scripts\python -m pytest tests/ -q` | 无 failed |
| 管理后台 | `cd backend/admin-web && npm run build` | 构建成功、eslint 0 错误 |
| 小程序 | 对改动的 js 逐个 `node --check`；`app.json` JSON 校验 | 语法全过 |

- pre-commit 已配置（`cd backend && pre-commit install`），提交时自动执行 ruff / ruff-format / WXSS 兼容扫描 / 敏感文件检查。
- 钩子改写了文件时，重新 `git add` 后再次提交即可。

## 四、其他约定

- **不要把无关目录带进提交**：仓库根下的非本项目目录（如 `dsh-routing-suite/`）已在 `.git/info/exclude` 本地排除；提交前检查 `git status`，发现陌生目录先核实。
- 生成物不入库：`datasets/`（合成占位数据）、`reference/`（过时参考实现）、`edge/edge_data.db`、`edge/edge_config.json` 均已在 `.gitignore`。
- Windows 环境 venv 位于 `backend/.venv`（Python 3.13）。
- 文档数字（页面数/路由数/测试数等）改动后须同步 `README.md` 与 `docs/`，以实测值为准。
