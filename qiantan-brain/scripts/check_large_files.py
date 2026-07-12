#!/usr/bin/env python3
"""Reject real secrets and files larger than the repository size limit."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEFAULT_LIMIT = 1024 * 1024
IGNORED_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache"}
SECRET_SUFFIXES = {".pem", ".key"}


def is_secret(path: Path) -> bool:
    name = path.name.lower()
    return name == ".env" or path.suffix.lower() in SECRET_SUFFIXES


def collect_files(inputs: list[str], repo_root: Path) -> list[Path]:
    candidates: set[Path] = set()
    if not inputs:
        inputs = [str(repo_root)]

    for raw in inputs:
        path = Path(raw)
        if not path.is_absolute():
            path = repo_root / path
        if path.is_file():
            candidates.add(path)
        elif path.is_dir():
            for current_root, dirnames, filenames in os.walk(path):
                dirnames[:] = [name for name in dirnames if name not in IGNORED_DIRS]
                root_path = Path(current_root)
                candidates.update(root_path / name for name in filenames)
    return sorted(candidates)


def display_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="检查大文件和敏感密钥文件")
    parser.add_argument("paths", nargs="*", help="待检查文件；默认扫描仓库")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    violations: list[str] = []
    for path in collect_files(args.paths, repo_root):
        shown_path = display_path(path, repo_root)
        if is_secret(path):
            violations.append(f"{shown_path}: 禁止提交真实环境变量或私钥文件")
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            violations.append(f"{shown_path}: 无法读取文件大小（{exc}）")
            continue
        if size > args.max_bytes:
            violations.append(
                f"{shown_path}: {size / 1024 / 1024:.2f} MiB，超过 "
                f"{args.max_bytes / 1024 / 1024:.2f} MiB 限制"
            )

    if violations:
        print("[FAIL] 大文件/敏感文件检查失败：")
        for violation in violations:
            print(f"  {violation}")
        return 1

    print("[PASS] 大文件和敏感文件检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

