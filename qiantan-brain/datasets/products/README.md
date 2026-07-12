# 千摊智脑商品识别数据集 (datasets/products)

## ⚠️ 重要：当前数据是「合成占位数据」，不是真实商品照片

`images/train`、`images/val` 下的图片由 `ml/generate_synthetic_dataset.py`
生成：在中性灰背景上画一个带颜色的圆角矩形 + 类名标签，并配一个居中的
YOLO 标签框。**这些图片仅用于验证训练管线的端到端可跑通（冒烟测试）**，
训练出的 `yolov8n_products.onnx` 不具备任何真实识别能力，**切勿上生产**。

## 目录结构

```
datasets/products/
├── data.yaml            # YOLOv8 数据集配置 (15 类)
├── images/
│   ├── train/           # 训练图片 (当前为合成占位图)
│   └── val/             # 验证图片
└── labels/
    ├── train/           # YOLO 格式标签 txt
    └── val/
```

## 重新生成合成数据（仅冒烟测试用）

```bash
pip install pillow
python ml/generate_synthetic_dataset.py --out datasets/products --per-class 30
```

## 生产数据准备（替换合成数据）

1. 收集真实菜摊商品照片（建议每类 ≥ 100 张，覆盖不同光照/角度/遮挡）。
2. 用 LabelImg / Roboflow 等工具按 YOLO 格式标注，放入上述目录。
3. 删除或移走合成占位图片，避免污染训练集。
4. 训练并导出生产模型：

   ```bash
   pip install ultralytics
   python ml/train_yolo.py --data datasets/products/data.yaml --epochs 100 --imgsz 640
   ```

   训练完成后会自动把 best 模型导出为 `edge/vision/model/yolov8n_products.onnx`。

## 15 个类别

0:白菜 1:菠菜 2:生菜 3:土豆 4:萝卜 5:胡萝卜 6:红薯 7:洋葱
8:豆腐 9:豆皮 10:黄瓜 11:番茄 12:西瓜 13:茄子 14:辣椒
