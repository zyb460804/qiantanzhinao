#!/usr/bin/env python3
"""Cross-platform WXSS compatibility lint used by pre-commit and CI."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
RULES = (
    ("@media 媒体查询", re.compile(r"^\s*@media", re.MULTILINE), "用类名或 JS 判定，不要写 @media"),
    (":root 伪类", re.compile(r":root"), "用 page { --x: ... } 定义全局 CSS 变量"),
    ("通配选择器 *", re.compile(r"\*\s?[{,:]"), "用明确的标签选择器或类名枚举，避免 *"),
    ("视口单位 vh/vw/rem", re.compile(r"\d(?:vh|vw|rem)\b", re.IGNORECASE), "改用 rpx / px / %（rpx 为主）"),
)
URL_RE = re.compile(r"url\((.*?)\)", re.IGNORECASE | re.DOTALL)
ALLOWED_URL_RE = re.compile(r"^(?:data:|https?:|//)", re.IGNORECASE)


def strip_comments(text: str) -> str:
    """Replace block comments with whitespace while preserving line numbers."""

    def replace(match: re.Match[str]) -> str:
        value = match.group(0)
        return "".join("\n" if char == "\n" else " " for char in value)

    return COMMENT_RE.sub(replace, text)


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def collect_files(inputs: list[str], repo_root: Path) -> list[Path]:
    if not inputs:
        inputs = [str(repo_root / "miniprogram")]

    files: set[Path] = set()
    for raw in inputs:
        path = Path(raw)
        if not path.is_absolute():
            path = repo_root / path
        if path.is_dir():
            files.update(path.rglob("*.wxss"))
        elif path.suffix.lower() == ".wxss" and path.exists():
            files.add(path)
    return sorted(files)


def display_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def lint_file(path: Path, repo_root: Path) -> list[str]:
    original = path.read_text(encoding="utf-8")
    text = strip_comments(original)
    issues: list[str] = []
    shown_path = display_path(path, repo_root)

    for name, pattern, hint in RULES:
        for match in pattern.finditer(text):
            issues.append(
                f"{shown_path}:{line_number(text, match.start())}: [{name}] {hint}"
            )

    for match in URL_RE.finditer(text):
        target = match.group(1).strip().strip("\"'").strip()
        if not ALLOWED_URL_RE.match(target):
            issues.append(
                f"{shown_path}:{line_number(text, match.start())}: "
                "[本地 url() 引用] 使用网络图片、data: 内联或 <image> 组件"
            )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描 WXSS 不兼容语法")
    parser.add_argument("paths", nargs="*", help="WXSS 文件或目录；默认扫描 miniprogram")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    files = collect_files(args.paths, repo_root)
    if not files:
        print("[PASS] 未发现需要扫描的 .wxss 文件")
        return 0

    issues = [issue for path in files for issue in lint_file(path, repo_root)]
    if issues:
        print("[FAIL] WXSS 兼容性扫描失败：")
        for issue in issues:
            print(f"  {issue}")
        print(f"[FAIL] 共发现 {len(issues)} 处问题")
        return 1

    print(f"[PASS] WXSS 扫描通过（{len(files)} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

