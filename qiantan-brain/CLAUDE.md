# CLAUDE.md — 千摊智脑项目守则

AI 助手（Claude Code 等）在本仓库工作时的强制约定。人类协作者请看 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 推送规则（最高优先级）

- **远程只推 `develop`**：`git push origin develop` 是唯一允许的推送形式。
- 禁止推送其他任何分支（包括 `push -u origin <新分支>`）。本地特性分支可以建、可以合并回 develop，但不上远程。
- 用户明确另行要求时除外。

## 环境速查

- Windows 11；git 仓库根在 `e:\千摊`，项目主体在 `qiantan-brain/`。
- 后端 venv：`qiantan-brain/backend/.venv`（Python 3.13）。
- 后端测试：`cd qiantan-brain/backend && .venv\Scripts\python -m pytest tests/ -q`。
- Lint：`.venv\Scripts\python -m ruff check app/ tests/ migrations/`（scripts/ 有历史遗留可忽略）。
- admin-web 构建：`cd backend/admin-web && npm run build`。
- 小程序为原生 WXML/WXSS，改动后对 js 跑 `node --check`，`app.json` 须 JSON 合法。
- pre-commit 钩子会自动 ruff-format；钩子改写文件后重新 `git add` 再提交。

## 工作约定

- 提交信息：`<type>: <中文描述>`（feat/fix/refactor/docs/test/chore/perf/ci），正文列要点与验证数字。
- 提交前门禁全绿（pytest / ruff / build / node --check）。
- `git add -A` 前先看 `git status`：仓库根下有非本项目目录（如 `dsh-routing-suite/`），已在 `.git/info/exclude` 排除，不要加进提交。
- 改动涉及数量统计（页面/路由/测试数）时同步 README 与 docs，用实测值。
- 数据库 `backend/qiantan_dev.db` 是开发库，可增量写测试数据，不可重置或覆盖。
