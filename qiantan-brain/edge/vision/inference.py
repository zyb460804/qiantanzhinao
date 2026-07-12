"""
YOLOv8-nano ONNX inference engine.
Runs on Raspberry Pi 5 CPU with ONNX Runtime.

Modes:
- Real: loads ONNX model and runs inference when model file exists
- Demo: returns simulated results when model file is missing
"""

import io
import random
from pathlib import Path


class YOLOInference:
    """YOLOv8-nano ONNX inference for product recognition.

    Modes:
    - Real: loads ONNX model and runs inference when model file exists
    - Demo: returns simulated results when model file is missing
    """

    MODEL_PATH = Path(__file__).parent / "model" / "yolov8n_products.onnx"
    CLASSES = [
        "白菜",   # 0
        "菠菜",   # 1
        "生菜",   # 2
        "土豆",   # 3
        "萝卜",   # 4
        "胡萝卜", # 5
        "红薯",   # 6
        "洋葱",   # 7
        "豆腐",   # 8
        "豆皮",   # 9
        "黄瓜",   # 10
        "番茄",   # 11
        "西瓜",   # 12
        "茄子",   # 13
        "辣椒",   # 14
    ]
    INPUT_SIZE = 640
    CONF_THRESHOLD = 0.5
    NMS_THRESHOLD = 0.45

    def __init__(self):
        self._session = None
        self._model_available = self.MODEL_PATH.exists()
        if self._model_available:
            try:
                import onnxruntime as ort
                self._session = ort.InferenceSession(
                    str(self.MODEL_PATH),
                    providers=["CPUExecutionProvider"],
                )
                print(f"[YOLOInference] 模型已加载: {self.MODEL_PATH.name}")
            except ImportError:
                print("[YOLOInference] 警告: onnxruntime 未安装，降级为演示模式")
                print("  安装: pip install onnxruntime")
                self._model_available = False
                self._session = None
            except Exception as e:
                print(f"[YOLOInference] 警告: 模型加载失败 ({e})，降级为演示模式")
                self._model_available = False
                self._session = None
        else:
            print(f"[YOLOInference] 模型文件未找到: {self.MODEL_PATH}")
            print("  当前为演示模式，将返回模拟识别结果")
            print("  训练并部署模型后可启用真实推理")

    def predict(self, image_bytes: bytes) -> list[dict]:
        """Run inference on an image.

        Args:
            image_bytes: JPEG/PNG image bytes.

        Returns:
            List of detections:
            [{product_id: int, name: str, confidence: float, bbox: [x, y, w, h]}]
            bbox in [x, y, w, h] format (pixel coordinates relative to original image).
        """
        if self._session:
            return self._real_predict(image_bytes)
        return self._demo_predict(image_bytes)

    # ── 真实推理 ──────────────────────────────────────────────────

    def _real_predict(self, image_bytes: bytes) -> list[dict]:
        """使用 ONNX Runtime 执行真实推理。"""
        import numpy as np

        # 1. 解码图片
        img, orig_h, orig_w = self._decode_image(image_bytes)
        if img is None:
            return []

        # 2. 预处理: resize + normalize + NCHW
        input_tensor, ratio, pad = self._preprocess(img, orig_w, orig_h)

        # 3. 推理
        input_name = self._session.get_inputs()[0].name
        outputs = self._session.run(None, {input_name: input_tensor})
        output = outputs[0]  # shape: [1, 4+nc, num_boxes] for YOLOv8

        # 4. 后处理: 解析输出 + NMS
        detections = self._postprocess(output, ratio, pad, orig_w, orig_h)

        return detections

    def _decode_image(self, image_bytes: bytes):
        """解码图片字节为 numpy 数组。"""
        try:
            from PIL import Image
        except ImportError:
            print("[YOLOInference] 错误: Pillow 未安装，无法解码图片")
            print("  安装: pip install pillow")
            return None, 0, 0

        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            return np_array(img), img.height, img.width
        except Exception as e:
            print(f"[YOLOInference] 图片解码失败: {e}")
            return None, 0, 0

    def _preprocess(self, img, orig_w: int, orig_h: int):
        """预处理: letterbox resize 到 640x640, 归一化, NCHW 格式。"""
        import numpy as np

        # letterbox: 保持宽高比缩放，填充灰色
        ratio = min(self.INPUT_SIZE / orig_w, self.INPUT_SIZE / orig_h)
        new_w, new_h = int(orig_w * ratio), int(orig_h * ratio)
        pad_w = (self.INPUT_SIZE - new_w) / 2
        pad_h = (self.INPUT_SIZE - new_h) / 2

        # resize
        from PIL import Image
        if isinstance(img, np.ndarray):
            img_pil = Image.fromarray(img)
        else:
            img_pil = img
        img_resized = img_pil.resize((new_w, new_h), Image.BILINEAR)

        # 创建 640x640 画布，填充灰色 (114)
        canvas = Image.new("RGB", (self.INPUT_SIZE, self.INPUT_SIZE), (114, 114, 114))
        canvas.paste(img_resized, (int(pad_w), int(pad_h)))

        # 转为 numpy, 归一化, NCHW
        arr = np_array(canvas).astype(np.float32) / 255.0  # HWC
        arr = arr.transpose(2, 0, 1)  # CHW
        arr = np.expand_dims(arr, axis=0)  # NCHW [1, 3, 640, 640]

        return arr, ratio, (pad_w, pad_h)

    def _postprocess(self, output, ratio: float, pad, orig_w: int, orig_h: int) -> list[dict]:
        """后处理: 解析 YOLOv8 输出, 过滤低置信度, NMS, 映射到原图坐标。"""
        import numpy as np

        # YOLOv8 输出: [1, 4+nc, num_boxes]
        # 转置为 [num_boxes, 4+nc]
        if output.ndim == 3:
            output = output[0]  # [4+nc, num_boxes]
        output = output.T  # [num_boxes, 4+nc]

        num_classes = len(self.CLASSES)
        boxes = output[:, :4]        # cx, cy, w, h
        scores = output[:, 4:4 + num_classes]  # class scores

        # 每个 box 的最大类别分数和类别 ID
        class_ids = np.argmax(scores, axis=1)
        max_scores = np.max(scores, axis=1)

        # 置信度过滤
        mask = max_scores >= self.CONF_THRESHOLD
        boxes = boxes[mask]
        class_ids = class_ids[mask]
        max_scores = max_scores[mask]

        if len(boxes) == 0:
            return []

        # NMS
        indices = self._nms(boxes, max_scores, self.NMS_THRESHOLD)
        boxes = boxes[indices]
        class_ids = class_ids[indices]
        max_scores = max_scores[indices]

        # 坐标变换: 640x640 -> 原图
        pad_w, pad_h = pad
        detections = []
        for i in range(len(boxes)):
            cx, cy, w, h = boxes[i]
            # 去除 padding
            cx = (cx - pad_w) / ratio
            cy = (cy - pad_h) / ratio
            w = w / ratio
            h = h / ratio
            # 转为 x, y, w, h (左上角)
            x = cx - w / 2
            y = cy - h / 2
            # 裁剪到图像范围内
            x = max(0, min(orig_w, x))
            y = max(0, min(orig_h, y))
            w = max(0, min(orig_w - x, w))
            h = max(0, min(orig_h - y, h))

            cid = int(class_ids[i])
            detections.append({
                "product_id": cid,
                "name": self.CLASSES[cid] if cid < len(self.CLASSES) else f"未知_{cid}",
                "confidence": float(max_scores[i]),
                "bbox": [float(x), float(y), float(w), float(h)],
            })

        # 按置信度降序排列
        detections.sort(key=lambda d: d["confidence"], reverse=True)
        return detections

    def _nms(self, boxes, scores, iou_threshold: float):
        """非极大值抑制 (NMS)。

        Args:
            boxes: [N, 4] (cx, cy, w, h)
            scores: [N]
            iou_threshold: IoU 阈值
        Returns:
            保留的索引列表
        """
        import numpy as np

        # 转为 x1, y1, x2, y2
        x1 = boxes[:, 0] - boxes[:, 2] / 2
        y1 = boxes[:, 1] - boxes[:, 3] / 2
        x2 = boxes[:, 0] + boxes[:, 2] / 2
        y2 = boxes[:, 1] + boxes[:, 3] / 2
        areas = (x2 - x1) * (y2 - y1)

        order = scores.argsort()[::-1]
        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)
            if order.size == 1:
                break
            # 计算当前 box 与其余 box 的 IoU
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
            union = areas[i] + areas[order[1:]] - inter
            iou = inter / (union + 1e-9)
            # 保留 IoU 小于阈值的
            inds = np.where(iou <= iou_threshold)[0]
            order = order[inds + 1]
        return keep

    # ── 演示模式 ──────────────────────────────────────────────────

    def _demo_predict(self, image_bytes: bytes) -> list[dict]:
        """演示模式: 返回模拟识别结果。

        基于图片字节哈希随机选择商品，模拟置信度和边界框。
        """
        # 使用图片字节数作为随机种子，保证同一图片结果一致
        seed = sum(image_bytes[:1024]) if image_bytes else 0
        rng = random.Random(seed)

        # 随机选择 1-3 个商品
        num_detections = rng.randint(1, 3)
        detections = []
        for _ in range(num_detections):
            cid = rng.randint(0, len(self.CLASSES) - 1)
            confidence = rng.uniform(0.65, 0.98)
            # 模拟边界框 (假设原图 640x640)
            w = rng.uniform(80, 300)
            h = rng.uniform(80, 300)
            x = rng.uniform(0, 640 - w)
            y = rng.uniform(0, 640 - h)
            detections.append({
                "product_id": cid,
                "name": self.CLASSES[cid],
                "confidence": round(confidence, 4),
                "bbox": [round(x, 1), round(y, 1), round(w, 1), round(h, 1)],
            })

        # 按置信度降序
        detections.sort(key=lambda d: d["confidence"], reverse=True)
        return detections

    @property
    def model_available(self) -> bool:
        """模型是否可用 (真实推理模式)。"""
        return self._model_available


def np_array(img):
    """将 PIL Image 转为 numpy 数组。"""
    import numpy as np
    return np.array(img)
