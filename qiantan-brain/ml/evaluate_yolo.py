"""YOLOv8 模型评估脚本。

在验证集上评估训练好的模型，输出各项指标并生成评估报告。

Usage:
    python evaluate_yolo.py
    python evaluate_yolo.py --model ml/runs/detect/train/weights/best.pt
    python evaluate_yolo.py --model yolov8n.pt --data datasets/products/data.yaml
"""

import argparse
import sys
import time
from pathlib import Path

# 项目根目录 (ml/ 的上一级)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_YAML = PROJECT_ROOT / "datasets" / "products" / "data.yaml"
RUNS_DIR = PROJECT_ROOT / "ml" / "runs"
DEFAULT_MODEL = RUNS_DIR / "detect" / "train" / "weights" / "best.pt"
REPORT_PATH = PROJECT_ROOT / "ml" / "evaluation_report.md"

CLASS_NAMES = [
    "白菜", "菠菜", "生菜", "土豆", "萝卜",
    "胡萝卜", "红薯", "洋葱", "豆腐", "豆皮",
    "黄瓜", "番茄", "西瓜", "茄子", "辣椒",
]


def check_ultralytics():
    """检查 ultralytics 是否安装。"""
    try:
        import ultralytics  # noqa: F401
        return True
    except ImportError:
        print("=" * 60)
        print("错误: ultralytics 未安装")
        print("=" * 60)
        print()
        print("请先安装依赖:")
        print("  pip install ultralytics")
        sys.exit(1)


def evaluate(model_path: str, data_yaml: str, imgsz: int = 640, batch: int = 16):
    """评估模型并生成报告。

    Args:
        model_path: 模型文件路径 (.pt)
        data_yaml: 数据集配置文件
        imgsz: 输入图像尺寸
        batch: batch size
    """
    check_ultralytics()
    from ultralytics import YOLO

    # 1. 加载模型
    model_file = Path(model_path)
    if not model_file.exists():
        print(f"错误: 模型文件未找到: {model_file}")
        print(f"默认路径: {DEFAULT_MODEL}")
        print("请先运行 train_yolo.py 训练模型，或指定 --model 参数")
        sys.exit(1)

    print(f"[1/4] 加载模型: {model_file}")
    model = YOLO(str(model_file))

    # 2. 在验证集上评估
    print(f"[2/4] 运行验证集评估 (data={data_yaml})...")
    metrics = model.val(data=data_yaml, imgsz=imgsz, batch=batch)

    # 3. 单张推理速度测试
    print(f"[3/4] 测试单张推理速度...")
    val_images_dir = PROJECT_ROOT / "datasets" / "products" / "images" / "val"
    test_images = list(val_images_dir.glob("*.jpg")) + list(val_images_dir.glob("*.png")) + list(val_images_dir.glob("*.jpeg"))

    inference_times = []
    if test_images:
        # 预热
        model.predict(str(test_images[0]), imgsz=imgsz, verbose=False)
        # 正式测速
        num_test = min(50, len(test_images))
        for img_path in test_images[:num_test]:
            t0 = time.perf_counter()
            model.predict(str(img_path), imgsz=imgsz, verbose=False)
            t1 = time.perf_counter()
            inference_times.append(t1 - t0)
        avg_time = sum(inference_times) / len(inference_times)
        fps = 1.0 / avg_time if avg_time > 0 else 0
        print(f"  测试图片数: {len(inference_times)}")
        print(f"  平均推理耗时: {avg_time * 1000:.1f} ms")
        print(f"  FPS: {fps:.1f}")
    else:
        avg_time = 0
        fps = 0
        print("  警告: 验证集为空，跳过推理速度测试")

    # 4. 生成报告
    print(f"[4/4] 生成评估报告: {REPORT_PATH}")
    report = _build_report(
        model_path=str(model_file),
        metrics=metrics,
        avg_inference_ms=avg_time * 1000,
        fps=fps,
        num_test=len(inference_times),
    )
    REPORT_PATH.write_text(report, encoding="utf-8")

    # 打印摘要
    print()
    print("=" * 60)
    print("评估结果摘要")
    print("=" * 60)
    print(f"  mAP50:      {metrics.box.map50:.4f}")
    print(f"  mAP50-95:   {metrics.box.map:.4f}")
    print(f"  Precision:  {metrics.box.mp:.4f}")
    print(f"  Recall:     {metrics.box.mr:.4f}")
    if avg_time > 0:
        print(f"  推理耗时:    {avg_time * 1000:.1f} ms / 张")
        print(f"  FPS:        {fps:.1f}")
    print("=" * 60)
    print(f"完整报告: {REPORT_PATH}")


def _build_report(model_path: str, metrics, avg_inference_ms: float, fps: float, num_test: int) -> str:
    """构建 Markdown 评估报告。"""
    names = metrics.names if hasattr(metrics, "names") else {i: c for i, c in enumerate(CLASS_NAMES)}

    lines = []
    lines.append("# YOLOv8 商品识别模型评估报告\n")
    lines.append(f"**模型路径**: `{model_path}`\n")
    lines.append(f"**评估时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"**类别数**: {len(CLASS_NAMES)}\n")

    # 总体指标
    lines.append("## 总体指标\n")
    lines.append("| 指标 | 值 |")
    lines.append("|------|------|")
    lines.append(f"| mAP50 | {metrics.box.map50:.4f} |")
    lines.append(f"| mAP50-95 | {metrics.box.map:.4f} |")
    lines.append(f"| Precision | {metrics.box.mp:.4f} |")
    lines.append(f"| Recall | {metrics.box.mr:.4f} |")
    lines.append("")

    # 推理速度
    lines.append("## 推理速度\n")
    if avg_inference_ms > 0:
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 测试图片数 | {num_test} |")
        lines.append(f"| 平均推理耗时 | {avg_inference_ms:.1f} ms |")
        lines.append(f"| FPS | {fps:.1f} |")
    else:
        lines.append("验证集为空，未进行推理速度测试。")
    lines.append("")

    # 每个类别指标
    lines.append("## 各类别指标\n")
    lines.append("| 类别 | Precision | Recall | mAP50 | mAP50-95 |")
    lines.append("|------|-----------|--------|-------|----------|")
    for i in range(len(CLASS_NAMES)):
        cls_name = names.get(i, CLASS_NAMES[i])
        p = metrics.box.p[i] if i < len(metrics.box.p) else 0
        r = metrics.box.r[i] if i < len(metrics.box.r) else 0
        ap50 = metrics.box.ap50[i] if i < len(metrics.box.ap50) else 0
        ap = metrics.box.ap[i] if i < len(metrics.box.ap) else 0
        lines.append(f"| {cls_name} | {p:.4f} | {r:.4f} | {ap50:.4f} | {ap:.4f} |")
    lines.append("")

    # 部署建议
    lines.append("## 部署建议\n")
    if metrics.box.map50 >= 0.85:
        lines.append("- 模型精度优秀 (mAP50 >= 0.85)，可以部署到生产环境。")
    elif metrics.box.map50 >= 0.70:
        lines.append("- 模型精度良好 (mAP50 >= 0.70)，建议增加数据继续优化。")
    else:
        lines.append("- 模型精度偏低 (mAP50 < 0.70)，建议增加数据量或调整超参数重新训练。")

    if fps > 0:
        if fps >= 15:
            lines.append(f"- 推理速度 {fps:.1f} FPS，满足实时识别需求。")
        elif fps >= 5:
            lines.append(f"- 推理速度 {fps:.1f} FPS，可用于准实时场景。")
        else:
            lines.append(f"- 推理速度 {fps:.1f} FPS 较慢，建议使用更小的模型或降低输入分辨率。")

    lines.append("\n## 导出 ONNX 部署\n")
    lines.append("```bash")
    lines.append("# 导出 ONNX 模型")
    lines.append(f"yolo export model={model_path} format=onnx imgsz=640 opset=12 simplify=True")
    lines.append("")
    lines.append("# 复制到边缘端")
    lines.append("cp ml/runs/detect/train/weights/best.onnx edge/vision/model/yolov8n_products.onnx")
    lines.append("```\n")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="YOLOv8 模型评估脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python evaluate_yolo.py
  python evaluate_yolo.py --model ml/runs/detect/train/weights/best.pt
  python evaluate_yolo.py --model yolov8n.pt --data datasets/products/data.yaml
        """,
    )
    parser.add_argument("--model", default=str(DEFAULT_MODEL),
                        help=f"模型文件路径 (默认: {DEFAULT_MODEL})")
    parser.add_argument("--data", default=str(DATA_YAML),
                        help=f"数据集配置文件 (默认: {DATA_YAML})")
    parser.add_argument("--imgsz", type=int, default=640, help="输入图像尺寸 (默认 640)")
    parser.add_argument("--batch", type=int, default=16, help="Batch size (默认 16)")
    args = parser.parse_args()

    evaluate(
        model_path=args.model,
        data_yaml=args.data,
        imgsz=args.imgsz,
        batch=args.batch,
    )


if __name__ == "__main__":
    main()
