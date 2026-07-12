#!/usr/bin/env bash
# =============================================================================
# wxss-lint.sh — 微信小程序 WXSS 兼容性红线扫描
#
# 背景：WXSS 裁剪了大量 CSS 标准特性，直接 copy Web 端样式会编译报
#       「unexpected token」并导致模拟器白屏（见 team-code-quality-guidance.md）。
# 本脚本用正则扫描小程序样式文件，提前在提交/CI 阶段拦截这些坑。
#
# 设计要点（v2，降低误报）：
#   - 先剥离 /* ... */ 块注释（保留换行数，行号不漂移），避免把设计系统
#     说明注释里的 * / @media / url() 当成违规。
#   - 通配选择器 * 只匹配「作为选择器使用」的 *（*, * {, *, , *::），
#     不误伤 calc(var(--i) * 70ms) 里的乘法 *。
#   - 本地 url() 只拦截相对路径 / 根相对 / 裸文件名，放行 data: 与 http(s):。
#
# 用法：
#   bash scripts/wxss-lint.sh                 # 扫描 qiantan-brain/miniprogram
#   bash scripts/wxss-lint.sh path/to/dir     # 扫描指定目录
#
# 退出码：发现违规返回 1，干净返回 0（可直接用于 CI / pre-commit 门禁）。
# =============================================================================
set -uo pipefail

TARGET="${1:-miniprogram}"
# 兼容从仓库任意位置调用：相对路径则相对仓库根拼接；绝对路径（含盘符 E:/ 或 /e/）直接用
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
case "$TARGET" in
  /*|*:/*) SCAN_DIR="$TARGET" ;;
  *) SCAN_DIR="$REPO_ROOT/$TARGET" ;;
esac

if [ ! -d "$SCAN_DIR" ]; then
  echo "⚠️  扫描目录不存在: $SCAN_DIR"
  exit 1
fi

echo "🔍 扫描 WXSS 兼容性红线: $SCAN_DIR"

# 收集所有 .wxss 文件
mapfile -t FILES < <(find "$SCAN_DIR" -type f -name '*.wxss' 2>/dev/null)
if [ ${#FILES[@]} -eq 0 ]; then
  echo "✅ 未发现 .wxss 文件"
  exit 0
fi

# 预处理：把每个 .wxss 的块注释替换成等长的空格（保留换行），写入临时镜像目录，
# 这样后续 grep 的行号与原始文件一致，且注释里的 * / @media / url() 不再误报。
TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

for f in "${FILES[@]}"; do
  rel="${f#"$SCAN_DIR"/}"
  dest="$TMP/$rel"
  mkdir -p "$(dirname "$dest")"
  perl -0777 -pe 's{/\*.*?\*/}{ my $c=$&; my $nl=($c=~tr/\n//); " "x(length($c)-$nl)."\n"x$nl }ges' "$f" > "$dest"
done

VIOLATIONS=0

# 把临时镜像路径前缀安全地替换回原始扫描目录（用 bash 前缀替换，规避 sed 的
# 反斜杠/& 转义坑，路径含中文或特殊字符时也稳定）。
rewrite() {
  while IFS= read -r _line; do
    printf '%s\n' "${_line/#"$TMP"/$SCAN_DIR}"
  done
}

# check <名字> <grep -E 正则> <建议>
check() {
  local name="$1" pattern="$2" hint="$3"
  local hits
  hits="$(grep -rnE "$pattern" "$TMP" 2>/dev/null | rewrite)"
  if [ -n "$hits" ]; then
    VIOLATIONS=$((VIOLATIONS + 1))
    echo ""
    echo "❌ [$name] 命中 $(echo "$hits" | wc -l | tr -d ' ') 处"
    echo "   建议: $hint"
    echo "$hits" | sed 's/^/     /'
  fi
}

# 1) @media 媒体查询：WXSS 不支持（注释已剥离，仅匹配真正的规则）
check "@media 媒体查询" '^[[:space:]]*@media' '用类名（如 .reduce-motion）或 JS 判定，不要写 @media'

# 2) :root 伪类：WXSS 不支持，改用 page { --x: ... }
check ":root 伪类" ':root' '用 page { --x: ... } 定义全局 CSS 变量'

# 3) 通配选择器 *：仅匹配作为选择器使用的 *（*, * {, *::, *,）
#    不匹配 calc(var(--i) * 70ms) 这类乘法（* 后是数字）。
check "通配选择器 *" '\*[ ]?[{,:]' '用明确的标签选择器或类名枚举，避免 *'

# 4) 视口单位 vh/vw/rem：WXSS 不支持，用 rpx/px/%
check "视口单位 vh/vw/rem" '[0-9](vh|vw|rem)\b' '改用 rpx / px / %（rpx 为主）'

# 5) 本地 url() 引用（非 data:/http(s):）：WXSS 不允许引用本地图片
#    排除引号包裹的 data: 与 http(s):；命中则为相对/根相对/裸文件名引用。
hits_url="$(grep -rnE 'url\(' "$TMP" 2>/dev/null \
  | grep -viE 'url\(["'\''"]?(data:|https?:|//)' \
  | rewrite)"
if [ -n "$hits_url" ]; then
  VIOLATIONS=$((VIOLATIONS + 1))
  echo ""
  echo "❌ [本地 url() 引用] 命中 $(echo "$hits_url" | wc -l | tr -d ' ') 处"
  echo "   建议: 网络图片 / base64 内联（data:）/ 用 <image> 组件"
  echo "$hits_url" | sed 's/^/     /'
fi

echo ""
if [ "$VIOLATIONS" -gt 0 ]; then
  echo "🚫 共发现 $VIOLATIONS 类 WXSS 兼容性问题，请修复后再提交。"
  exit 1
else
  echo "✅ WXSS 扫描通过，未发现兼容性红线。"
  exit 0
fi
