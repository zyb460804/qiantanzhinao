#!/usr/bin/env bash
# 千摊智脑后端一键质量门禁 —— 与 CI (../../.github/workflows/ci.yml) 同源。
# 用法:  ./scripts/check.sh
#
# 依次运行: ruff lint → ruff format 校验 → mypy → pytest
# 任一失败立即退出 (set -e)。本地跑通 ≈ CI 会绿。

set -euo pipefail

# 切到 backend/ (本脚本位于 backend/scripts/)
cd "$(dirname "$0")/.."

echo "── ruff check ────────────────────────────────────"
ruff check app/

echo "── ruff format --check ───────────────────────────"
ruff format app/ --check

echo "── mypy ──────────────────────────────────────────"
mypy app/

echo "── pytest ────────────────────────────────────────"
pytest tests/ -q

echo ""
echo "✅ 全部通过 (lint + format + type + test)"
