"""合成数据集生成脚本 (千摊智脑)。

⚠️ 重要说明（务必阅读）：
    本脚本生成的图片是「合成占位数据」——在中性背景上画一个带颜色的
    圆角矩形/椭圆来代表某一类商品。它仅用于验证训练管线的端到端可跑通
    （冒烟测试），生成的权重不具备任何真实识别能力。
    生产环境请收集真实菜摊商品照片，标注后替换 datasets/products 下内容，
    再运行 train_yolo.py。

仅依赖 Pillow (PIL)，无需 torch / ultralytics 即可生成。

用法:
    python ml/generate_synthetic_dataset.py --out datasets/products --per-class 30
"""

import argparse
import random
from pathlib import Path

from PIL import Image, ImageDraw

# ── 15 类商品（名称必须与 inference.py 中 CLASSES 完全一致）─────────────
CLASSES = [
    "白菜", "菠菜", "生菜", "土豆", "萝卜", "胡萝卜", "红薯", "洋葱",
    "豆腐", "豆皮", "黄瓜", "番茄", "西瓜", "茄子", "辣椒",
]

# 每个类别分配一个可区分的代表色 (R, G, B)。
# 大致参考真实商品颜色，使合成图在视觉上可粗略区分。
CLASS_COLORS = {
    "白菜": (220, 235, 200),
    "菠菜": (40, 140, 50),
    "生菜": (120, 200, 90),
    "土豆": (190, 150, 90),
    "萝卜": (235, 220, 225),
    "胡萝卜": (240, 130, 30),
    "红薯": (150, 80, 50),
    "洋葱": (180, 150, 200),
    "豆腐": (250, 250, 235),
    "豆皮": (225, 195, 120),
    "黄瓜": (80, 170, 60),
    "番茄": (220, 50, 40),
    "西瓜": (30, 150, 60),
    "茄子": (120, 60, 160),
    "辣椒": (210, 40, 40),
}

IMG_SIZE = 640
# 合成框：以图像中心为基准，尺寸约 0.4 x 0.4（归一化），YOLO 格式一行。
BOX_W, BOX_H = 0.4, 0.4
CX, CY = 0.5, 0.5  # 居中


def draw_rounded_rect(draw: ImageDraw.ImageDraw, box, color, radius=40):
    """在给定像素 box (x0,y0,x1,y1) 上画圆角矩形。"""
    x0, y0, x1, y1 = box
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=color)


def make_image(class_name: str, seed: int) -> Image.Image:
    """生成一张代表某类的合成图（中性灰背景 + 带色圆角矩形 + 类名牌）。"""
    rng = random.Random(seed)
    bg = (210, 210, 215)  # 中性背景
    img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), bg)
    draw = ImageDraw.Draw(img)

    color = CLASS_COLORS.get(class_name, (150, 150, 150))
    # 加入轻微随机抖动，让同类图像不完全相同
    jittered = tuple(
        max(0, min(255, c + rng.randint(-15, 15))) for c in color
    )

    # 像素级框（中心 0.5,0.5，尺寸 0.4x0.4 → 边距 0.3~0.7）
    m0, m1 = 0.30, 0.70
    x0 = int(m0 * IMG_SIZE)
    y0 = int(m0 * IMG_SIZE)
    x1 = int(m1 * IMG_SIZE)
    y1 = int(m1 * IMG_SIZE)
    draw_rounded_rect(draw, (x0, y0, x1, y1), jittered, radius=50)

    # 在顶部画一行类名牌（白色底条 + 类名文字）
    try:
        from PIL import ImageFont

        font = ImageFont.load_default()
        label = f"SYN: {class_name}"
        tw = draw.textlength(label, font=font)
        draw.rectangle([8, 8, 8 + int(tw) + 12, 28], fill=(255, 255, 255))
        draw.text((14, 12), label, fill=(0, 0, 0), font=font)
    except Exception:
        # 字体不可用时忽略牌面文字，不影响训练管线冒烟测试
        pass

    return img


def write_label(label_path: Path):
    """写一个居中的 YOLO 格式标签行: class_id cx cy w h。"""
    # 注意: 这里在外部调用时传入 class_id，故此处只负责写固定格式。
    # 实际 class_id 由调用方提供，见 generate()。
    pass


def generate(out_root: Path, per_class: int):
    splits = {
        "train": per_class,
        "val": max(1, per_class // 3),
    }
    total_images = 0
    total_labels = 0

    for split, count in splits.items():
        img_dir = out_root / "images" / split
        lbl_dir = out_root / "labels" / split
        img_dir.mkdir(parents=True, exist_ok=True)
        lbl_dir.mkdir(parents=True, exist_ok=True)

        for cls_id, cls_name in enumerate(CLASSES):
            for i in range(count):
                seed = hash((split, cls_id, i)) & 0xFFFF_FFFF
                img = make_image(cls_name, seed)
                stem = f"{cls_name}_{cls_id}_{split}_{i:04d}"
                img_path = img_dir / f"{stem}.png"
                lbl_path = lbl_dir / f"{stem}.txt"

                img.save(img_path, "PNG")
                # YOLO 标签: class_id cx cy w h (归一化)
                with open(lbl_path, "w", encoding="utf-8") as f:
                    f.write(f"{cls_id} {CX:.6f} {CY:.6f} {BOX_W:.6f} {BOX_H:.6f}\n")
                total_images += 1
                total_labels += 1

    print("=" * 50)
    print("合成数据集生成完成（⚠️ 占位数据，非真实商品照片）")
    print("=" * 50)
    print(f"  输出目录 : {out_root}")
    print(f"  类别数   : {len(CLASSES)}")
    print(f"  每类/训练: {splits['train']}  每类/验证: {splits['val']}")
    print(f"  图片总数 : {total_images}")
    print(f"  标签总数 : {total_labels}")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(
        description="生成合成占位数据集（仅用于管线冒烟测试）",
    )
    parser.add_argument(
        "--out", default="datasets/products",
        help="数据集根目录 (含 images/ 与 labels/)",
    )
    parser.add_argument(
        "--per-class", type=int, default=30,
        help="每个类别的训练图片数量 (默认 30)",
    )
    args = parser.parse_args()

    try:
        from PIL import Image, ImageDraw  # noqa: F401
    except ImportError:
        print("错误: 未安装 Pillow。请先安装: pip install pillow")
        raise SystemExit(1)

    out_root = Path(args.out).resolve()
    generate(out_root, args.per_class)


if __name__ == "__main__":
    main()
