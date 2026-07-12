"""YOLOv8 商品识别训练脚本 (千摊智脑)。

⚠️ 诚实声明：本脚本本身只负责训练与导出，是否能产出可用的生产模型，
   完全取决于 datasets/products 里喂的是不是「真实菜摊商品照片」。
   若仍在使用 generate_synthetic_dataset.py 生成的合成占位数据，
   训练出的权重仅能跑通管线，不具备真实识别能力，切勿上生产。

用法:
    python ml/train_yolo.py --data datasets/products/data.yaml --epochs 3 --imgsz 640
    python ml/train_yolo.py --data datasets/products/data.yaml --epochs 100 --imgsz 640 --batch 16

训练完成后，best 模型会自动导出为 ONNX 并写入:
    edge/vision/model/yolov8n_products.onnx
（inference.py 期望的正是这个路径）

依赖:
    pip install ultralytics
"""

import argparse
import sys
from pathlib import Path

# 项目根目录: ml/ 的上一级
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# inference.py 期望的 ONNX 部署路径
ONNX_DEPLOY_PATH = PROJECT_ROOT / "edge" / "vision" / "model" / "yolov8n_products.onnx"


def check_ultralytics():
    """检查 ultralytics 是否安装，未安装则打印清晰指引并退出。"""
    try:
        import ultralytics  # noqa: F401
    except ImportError:
        print("=" * 60)
        print("错误: 未检测到 ultralytics，无法训练。")
        print("=" * 60)
        print("请先安装依赖:")
        print("    pip install ultralytics")
        print("（该命令会一并安装 torch / opencv 等较大依赖）")
        print("文档: https://docs.ultralytics.com/quickstart/")
        sys.exit(1)


def train(data_yaml: str, epochs: int, imgsz: int, batch: int, device: str):
    """训练 YOLOv8n 并将 best 模型导出为 ONNX 到部署路径。"""
    check_ultralytics()

    from ultralytics import YOLO

    data_path = Path(data_yaml).resolve()
    if not data_path.exists():
        print(f"错误: 数据集配置文件不存在: {data_path}")
        sys.exit(1)

    print(f"\n[1/4] 加载预训练模型: yolov8n.pt")
    model = YOLO("yolov8n.pt")

    print(f"[2/4] 开始训练: epochs={epochs}, imgsz={imgsz}, batch={batch}")
    train_kwargs = dict(
        data=str(data_path),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        project=str(PROJECT_ROOT / "ml" / "runs"),
        name="train",
        save=True,
        val=True,
        # 基础增强；合成数据下无需过度增强，真实数据可酌情调大
        hsv_h=0.015,
        hsv_s=0.4,
        hsv_v=0.2,
        fliplr=0.5,
        mosaic=1.0,
    )
    if device:
        train_kwargs["device"] = device
    model.train(**train_kwargs)

    print("\n[3/4] 验证模型...")
    try:
        metrics = model.val()
        print(f"  mAP50: {metrics.box.map50:.4f}   mAP50-95: {metrics.box.map:.4f}")
    except Exception as e:  # 验证失败不应阻断导出
        print(f"  验证跳过 (不影响导出): {e}")

    print(f"\n[4/4] 导出 ONNX 到: {ONNX_DEPLOY_PATH}")
    ONNX_DEPLOY_PATH.parent.mkdir(parents=True, exist_ok=True)
    onnx_path = model.export(
        format="onnx",
        imgsz=imgsz,
        half=False,      # FP32，树莓派 ONNX Runtime 推荐
        simplify=True,
        opset=12,
    )
    # 将导出结果复制到 inference.py 期望的部署路径
    import shutil

    shutil.copyfile(onnx_path, ONNX_DEPLOY_PATH)
    print("=" * 50)
    print("完成!")
    print(f"  ONNX 模型: {ONNX_DEPLOY_PATH}")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(
        description="千摊智脑 YOLOv8 商品识别训练 (nano)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python ml/train_yolo.py --data datasets/products/data.yaml --epochs 3 --imgsz 640\n"
            "  python ml/train_yolo.py --data datasets/products/data.yaml --epochs 100 --batch 16\n"
        ),
    )
    parser.add_argument(
        "--data", default=str(PROJECT_ROOT / "datasets" / "products" / "data.yaml"),
        help="data.yaml 路径 (默认 datasets/products/data.yaml)",
    )
    parser.add_argument("--epochs", type=int, default=3, help="训练轮数 (冒烟测试默认 3)")
    parser.add_argument("--imgsz", type=int, default=640, help="输入图像尺寸 (默认 640)")
    parser.add_argument("--batch", type=int, default=16, help="Batch size (默认 16)")
    parser.add_argument("--device", default="", help="训练设备 (0=cuda:0, cpu=cpu, 默认自动)")
    args = parser.parse_args()

    train(
        data_yaml=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
    )


if __name__ == "__main__":
    main()
