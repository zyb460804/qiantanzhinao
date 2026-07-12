"""
数据集准备脚本 — 收集、标注、组织 15 类菜市场商品图片

数据集来源建议:
  1. 自拍 (手机拍摄菜市场摊位, 每个品类 100-200 张)
  2. 公开数据集: Open Images V7, Fruits-360, Grocery Dataset
  3. 数据增强: 本脚本内置自动增强

目录结构:
  dataset/
  ├── raw/              # 原始图片 (按品类分文件夹)
  │   ├── 白菜/
  │   ├── 土豆/
  │   └── ...
  ├── train/images/     # 训练集图像
  ├── train/labels/     # 训练集 YOLO 标注
  ├── val/images/       # 验证集图像
  ├── val/labels/       # 验证集 YOLO 标注
  ├── test/images/      # 测试集图像
  └── test/labels/      # 测试集 YOLO 标注

YOLO 标注格式 (每行一个目标):
  <class_id> <x_center> <y_center> <width> <height>
  所有坐标归一化到 [0, 1]

标注工具推荐:
  1. LabelImg  (pip install labelimg) — 图形界面, YOLO 格式导出
  2. LabelMe   (pip install labelme)  — JSON 格式, 需转换脚本
  3. Roboflow  (roboflow.com)         — 在线标注 + 自动增强
"""

import argparse
import os
import random
import shutil
from pathlib import Path

# ── 15 类商品 (与 train_yolo.py 保持一致) ──────────────────────────
CLASS_NAMES = [
    "白菜", "土豆", "黄瓜", "番茄", "苹果", "西瓜", "香蕉",
    "鸡蛋", "豆腐", "猪肉", "牛肉", "鸡肉", "鱼", "葱", "姜",
]

# 类别名 → 拼音文件夹名
PINYIN_MAP = {
    "白菜": "baicai", "土豆": "tudou", "黄瓜": "huanggua",
    "番茄": "fanqie", "苹果": "pingguo", "西瓜": "xigua",
    "香蕉": "xiangjiao", "鸡蛋": "jidan", "豆腐": "doufu",
    "猪肉": "zhurou", "牛肉": "niurou", "鸡肉": "jirou",
    "鱼": "yu", "葱": "cong", "姜": "jiang",
}


def create_dirtree(base_dir: str) -> dict:
    """Create YOLO dataset directory structure."""
    base = Path(base_dir)
    dirs = {}
    for split in ["train", "val", "test"]:
        img_dir = base / split / "images"
        lbl_dir = base / split / "labels"
        img_dir.mkdir(parents=True, exist_ok=True)
        lbl_dir.mkdir(parents=True, exist_ok=True)
        dirs[split] = {"images": img_dir, "labels": lbl_dir}
    return dirs


def split_dataset(
    raw_dir: str,
    output_dir: str,
    train_ratio: float = 0.7,
    val_ratio: float = 0.2,
    test_ratio: float = 0.1,
    seed: int = 42,
):
    """
    将 raw/ 下的图片按比例分配到 train/val/test。

    期望 raw/ 结构:
      raw/
      ├── 白菜/  (或 baicai/)
      │   ├── img001.jpg
      │   ├── img001.txt  (YOLO 标注文件, 同名 .txt)
      │   └── ...
      ├── 土豆/
      └── ...

    txt 标注格式: <class_id> <x> <y> <w> <h>
    """
    random.seed(seed)
    base = Path(raw_dir)
    output = Path(output_dir)
    dirs = create_dirtree(output_dir)

    stats = {}

    for class_id, name in enumerate(CLASS_NAMES):
        # 尝试中文名和拼音文件夹
        cls_dir = base / name
        if not cls_dir.exists():
            cls_dir = base / PINYIN_MAP.get(name, name)
        if not cls_dir.exists():
            print(f"  ⚠ 未找到品类 [{name}], 跳过")
            continue

        # 收集所有图片文件
        images = []
        for ext in ["*.jpg", "*.jpeg", "*.png", "*.JPG", "*.JPEG", "*.PNG"]:
            images.extend(list(cls_dir.glob(ext)))
        images = sorted(images)

        if not images:
            print(f"  ⚠ [{name}] 无图片, 跳过")
            continue

        # 过滤: 确保同名的 .txt 标注文件存在
        valid = []
        for img in images:
            lbl = img.with_suffix(".txt")
            if lbl.exists():
                valid.append(img)
        images = valid

        if not images:
            print(f"  ⚠ [{name}] 无有效图片+标注对, 跳过")
            continue

        # 打乱
        random.shuffle(images)
        n = len(images)
        n_train = max(1, int(n * train_ratio))
        n_val = max(1, int(n * val_ratio))

        splits = {
            "train": images[:n_train],
            "val": images[n_train:n_train + n_val],
            "test": images[n_train + n_val:],
        }

        # 复制到目标目录
        for split_name, imgs in splits.items():
            for img in imgs:
                lbl = img.with_suffix(".txt")
                # 复制图片
                dst_img = dirs[split_name]["images"] / f"{name}_{img.name}"
                shutil.copy2(img, dst_img)

                # 复制标注 (class_id 已在标注中)
                dst_lbl = dirs[split_name]["labels"] / f"{name}_{img.stem}.txt"
                shutil.copy2(lbl, dst_lbl)

        stats[name] = {
            "total": n,
            "train": len(splits["train"]),
            "val": len(splits["val"]),
            "test": len(splits["test"]),
        }
        print(f"  ✅ [{name}] {n} 张 → train:{len(splits['train'])} val:{len(splits['val'])} test:{len(splits['test'])}")

    # 打印汇总
    total = sum(s["total"] for s in stats.values())
    print(f"\n📊 数据集汇总: {total} 张图片, {len(stats)} 个品类")
    for name, s in stats.items():
        print(f"   {name}: {s['total']} 张")

    return stats


def generate_empty_labels(output_dir: str):
    """为无标注的图片生成空的占位标注文件 (YOLO 格式)."""
    output = Path(output_dir)
    for split in ["train", "val", "test"]:
        img_dir = output / split / "images"
        lbl_dir = output / split / "labels"
        if not img_dir.exists():
            continue
        lbl_dir.mkdir(parents=True, exist_ok=True)
        for img in img_dir.iterdir():
            if img.suffix.lower() in [".jpg", ".jpeg", ".png"]:
                lbl_path = lbl_dir / f"{img.stem}.txt"
                if not lbl_path.exists():
                    lbl_path.touch()  # 空文件 = 无目标


def main():
    parser = argparse.ArgumentParser(description="千摊智脑 数据集准备")
    parser.add_argument("--raw_dir", default="./dataset/raw", help="原始图片目录")
    parser.add_argument("--output_dir", default="./dataset", help="输出数据集目录")
    parser.add_argument("--train_ratio", type=float, default=0.7)
    parser.add_argument("--val_ratio", type=float, default=0.2)
    parser.add_argument("--test_ratio", type=float, default=0.1)
    args = parser.parse_args()

    print("千摊智脑 — 数据集准备")
    print(f"  原始目录: {args.raw_dir}")
    print(f"  输出目录: {args.output_dir}")
    print(f"  分配比例: {args.train_ratio}:{args.val_ratio}:{args.test_ratio}")
    print()

    split_dataset(
        raw_dir=args.raw_dir,
        output_dir=args.output_dir,
        train_ratio=args.train_ratio,
        val_ratio=args.val_ratio,
        test_ratio=args.test_ratio,
    )

    print("\n✅ 数据集准备完成!")
    print("   下一步: python train_yolo.py --data_dir ./dataset --epochs 100")


if __name__ == "__main__":
    main()
