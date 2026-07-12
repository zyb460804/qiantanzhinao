# YOLOv8 模型文件

将训练好的 ONNX 模型文件放在此目录下：
- `yolov8n_products.onnx` — YOLOv8-nano 导出的 ONNX 格式

## 获取模型

1. 收集商品图片（每类至少 100 张）
2. 使用 LabelImg 或 Roboflow 标注
3. 运行 `python ml/train_yolo.py --epochs 100`
4. 训练完成后 ONNX 文件会自动导出到 `ml/runs/detect/train/weights/best.onnx`
5. 复制到本目录：
   ```bash
   cp ml/runs/detect/train/weights/best.onnx edge/vision/model/yolov8n_products.onnx
   ```

## 支持的商品类别（15 类）

| ID | 名称 | ID | 名称 |
|----|------|----|------|
| 0 | 白菜 | 8 | 豆腐 |
| 1 | 菠菜 | 9 | 豆皮 |
| 2 | 生菜 | 10 | 黄瓜 |
| 3 | 土豆 | 11 | 番茄 |
| 4 | 萝卜 | 12 | 西瓜 |
| 5 | 胡萝卜 | 13 | 茄子 |
| 6 | 红薯 | 14 | 辣椒 |
| 7 | 洋葱 | | |

## 模型规格

- **架构**: YOLOv8-nano
- **输入尺寸**: 640 × 640 (RGB)
- **输出**: 15 类商品检测 (bbox + class + confidence)
- **推理后端**: ONNX Runtime (CPUExecutionProvider)
- **部署目标**: 树莓派 5

## 验证模型

```python
from edge.vision.inference import YOLOInference

infer = YOLOInference()
print(f"模型可用: {infer.model_available}")

with open("test.jpg", "rb") as f:
    results = infer.predict(f.read())

for det in results:
    print(f"{det['name']} ({det['confidence']:.2f}) bbox={det['bbox']}")
```
