#!/usr/bin/env bash
# =============================================================================
# check.sh — 千摊智脑 本地质量门禁「一键体检」
#
# 在提交前或 CI 中运行，串联三道关卡：
#   1) ruff  lint + 格式化检查（Python 后端）
#   2) wxss-lint.sh  WXSS 兼容性红线扫描（小程序）
#   3) pytest  后端测试（可选，--skip-tests 跳过）
#
# 用法：
#   bash scripts/check.sh                 # 全量检查（含测试）
#   bash scripts/check.sh --skip-tests    # 仅静态检查，跑得快
#
# 退出码：任一门禁失败即非零，可直接挂到 pre-commit / CI。
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
MINI="$ROOT/miniprogram"
FAIL=0

# ---- 定位 ruff ----------------------------------------------------------------
# 优先用环境变量注入（CI / 沙箱常用），其次 PATH，其次项目 .venv
RUFF="${RUFF:-$(command -v ruff || true)}"
if [ -z "$RUFF" ] && [ -x "$BACKEND/.venv/bin/ruff" ]; then
  RUFF="$BACKEND/.venv/bin/ruff"
fi
if [ -z "$RUFF" ]; then
  echo "⚠️  未找到 ruff，请先: pip install -r backend/requirements-dev.txt"
  echo "    （或运行 RUFF=/path/to/ruff bash scripts/check.sh）"
  RUFF="ruff"   # 仍尝试调用，失败会被下面捕获
fi

# ---- 1) ruff ------------------------------------------------------------------
echo "========================================"
echo "🐍 [1/3] ruff — Python lint & format"
echo "========================================"
if (cd "$BACKEND" && "$RUFF" check .); then
  echo "✅ ruff lint 通过"
else
  echo "❌ ruff 发现问题（可用 'ruff check --fix .' 自动修复大部分）"
  FAIL=1
fi

# ---- 2) WXSS ------------------------------------------------------------------
echo ""
echo "========================================"
echo "🎨 [2/3] wxss-lint — 小程序样式兼容红线"
echo "========================================"
if bash "$ROOT/scripts/wxss-lint.sh" "$MINI"; then
  :
else
  echo "❌ WXSS 存在兼容性问题"
  FAIL=1
fi

# ---- 3) pytest ----------------------------------------------------------------
if [[ "${1:-}" != "--skip-tests" ]]; then
  echo ""
  echo "========================================"
  echo "🧪 [3/3] pytest — 后端测试"
  echo "========================================"
  if [ -n "${PYTHON:-}" ]; then
    PY="$PYTHON"
  elif [ -d "$BACKEND/.venv" ]; then
    PY="$BACKEND/.venv/bin/python"
  else
    PY="python"
  fi
  if (cd "$BACKEND" && "$PY" -m pytest -q); then
    echo "✅ 测试通过"
  else
    echo "❌ 测试未通过"
    FAIL=1
  fi
else
  echo ""
  echo "⏭️  已跳过测试（--skip-tests）"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "🎉 全部质量门禁通过！"
  exit 0
else
  echo "🚫 质量门禁未通过，请修复后重试。"
  exit 1
fi
