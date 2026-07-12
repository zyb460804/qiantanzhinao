# 商品识别模型训练手册 (Model Training)

千摊智脑商品识别基于 **YOLOv8-nano**，在树莓派 5 上以 ONNX Runtime CPU 推理。

> ⚠️ 当前状态：**推理管线已接（`edge/vision/inference.py` 支持真实 ONNX 推理 + 演示降级），训练权重尚未生成**。仓库内 `datasets/products/` 下是**合成占位数据**，仅用于管线冒烟测试，**不可用于生产模型**。请收集真实菜摊商品照片替换后再训练。

## 1. 数据集布局

```text
datasets/products/
├── data.yaml                 # 类别与路径配置
├── images/
│   ├── train/               # 训练图片
│   └── val/                # 验证图片
└── labels/
    ├── train/               # YOLO 格式标签 (class cx cy w h 归一化)
    └── val/
```

`data.yaml` 当前定义 **15 类**：白菜, 菠菜, 生菜, 土豆, 萝卜, 胡萝卜, 红薯, 洋葱, 豆腐, 豆皮, 黄瓜, 番茄, 西瓜, 茄子, 辣椒。

## 2. 准备真实数据集

- 用手机/摄像头采集每类商品 50–200 张（不同光照、堆叠、角度）。
- 用标注工具（如 [LabelImg](https://github.com/HumanSignal/labelImg) 或 Roboflow）导出 YOLO 格式 txt。
- 按 8:2 划分 train / val，放入上述目录。

## 3. 训练

```bash
pip install ultralytics onnx
python ml/train_yolo.py --data datasets/products/data.yaml --epochs 50 --imgsz 640
```

脚本 `ml/train_yolo.py` 会：
1. 加载 `data.yaml`；
2. 训练 YOLOv8n；
3. 训练完成后导出 ONNX 到 `edge/vision/model/yolov8n_products.onnx`（与 `inference.MODEL_PATH` 对齐）。

> 真机资源有限时，可用 `--epochs 3` 先跑通管线，再加大轮数。

## 4. 合成数据冒烟测试（无需真实图片）

`ml/generate_synthetic_dataset.py` 用 Pillow 生成每类带色块占位图 + 对应标签，用于验证「数据集 → 训练 → 导出」整条链路可运行：

```bash
python ml/generate_synthetic_dataset.py --out datasets/products --per-class 30
python ml/train_yolo.py --data datasets/products/data.yaml --epochs 3 --imgsz 640
```

## 5. 评估指标

训练后请记录并写入 `docs/experiment-report.md`：
- mAP@0.5、Precision、Recall
- 单张推理耗时
- 树莓派端 FPS（ONNX Runtime CPU）

## 6. 部署

将导出的 `yolov8n_products.onnx` 放到 `edge/vision/model/`，边缘端 `YOLOInference` 会自动加载并切换到真实推理模式（否则保持演示模式）。
