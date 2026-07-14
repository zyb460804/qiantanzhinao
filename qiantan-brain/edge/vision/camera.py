"""
CSI Camera capture module for Raspberry Pi 5.
Captures single frames for YOLO inference on button trigger.

优先级:
1. picamera2 (树莓派 CSI 摄像头)
2. cv2.VideoCapture (USB 摄像头)
3. 返回 None (都不可用时)
"""

import io
import logging


logger = logging.getLogger(__name__)


class CameraCapture:
    """Raspberry Pi CSI camera wrapper.

    优先使用 picamera2 (CSI 摄像头), 降级到 cv2.VideoCapture (USB 摄像头)。
    如果都不可用，capture() 返回 None。
    """

    def __init__(self, camera_id: int = 0, width: int = 640, height: int = 480):
        """
        Args:
            camera_id: USB 摄像头 ID (用于 cv2.VideoCapture)
            width: 采集图像宽度
            height: 采集图像高度
        """
        self.camera_id = camera_id
        self.width = width
        self.height = height
        self._picamera2 = None
        self._vcapture = None
        self._backend = None
        self._init_camera()

    def _init_camera(self):
        """初始化摄像头，按优先级尝试不同后端。"""
        # 1. 尝试 picamera2 (树莓派 CSI 摄像头)
        try:
            from picamera2 import Picamera2
            self._picamera2 = Picamera2()
            config = self._picamera2.create_still_configuration(
                main={"size": (self.width, self.height), "format": "RGB888"}
            )
            self._picamera2.configure(config)
            self._picamera2.start()
            self._backend = "picamera2"
            logger.info("摄像头初始化成功: picamera2 (CSI 摄像头)")
            return
        except ImportError:
            logger.debug("picamera2 未安装，尝试 OpenCV VideoCapture")
        except Exception as e:
            logger.warning(f"picamera2 初始化失败: {e}，尝试 OpenCV VideoCapture")
            self._picamera2 = None

        # 2. 尝试 cv2.VideoCapture (USB 摄像头)
        try:
            import cv2
            self._vcapture = cv2.VideoCapture(self.camera_id)
            if self._vcapture.isOpened():
                self._vcapture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                self._vcapture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                self._backend = "cv2"
                logger.info(f"摄像头初始化成功: cv2.VideoCapture (USB 摄像头 ID={self.camera_id})")
                return
            else:
                self._vcapture.release()
                self._vcapture = None
        except ImportError:
            logger.warning("opencv-python 未安装，无法使用 USB 摄像头")
        except Exception as e:
            logger.warning(f"cv2.VideoCapture 初始化失败: {e}")
            self._vcapture = None

        # 3. 都不可用
        self._backend = None
        logger.warning("摄像头不可用: picamera2 和 cv2.VideoCapture 均无法使用")
        logger.warning("安装指引:")
        logger.warning("  树莓派 CSI 摄像头: pip install picamera2")
        logger.warning("  USB 摄像头: pip install opencv-python")

    def capture(self) -> bytes:
        """Capture a single frame and return as JPEG bytes.

        Returns:
            JPEG 格式的图片字节。如果摄像头不可用或采集失败，返回 None。
        """
        if self._backend == "picamera2":
            return self._capture_picamera2()
        elif self._backend == "cv2":
            return self._capture_cv2()
        else:
            logger.warning("摄像头不可用，无法采集图片")
            return None

    def _capture_picamera2(self) -> bytes:
        """使用 picamera2 采集一帧。"""
        try:
            from PIL import Image
            frame = self._picamera2.capture_array()
            # picamera2 返回 numpy 数组 (RGB)
            img = Image.fromarray(frame)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            return buf.getvalue()
        except Exception as e:
            logger.error(f"picamera2 采集失败: {e}")
            return None

    def _capture_cv2(self) -> bytes:
        """使用 cv2.VideoCapture 采集一帧。"""
        try:
            import cv2
            ret, frame = self._vcapture.read()
            if not ret:
                logger.warning("cv2 采集失败: 无法读取帧")
                return None
            # cv2 默认 BGR，转为 RGB
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            from PIL import Image
            img = Image.fromarray(frame_rgb)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            return buf.getvalue()
        except Exception as e:
            logger.error(f"cv2 采集失败: {e}")
            return None

    def release(self):
        """Release camera resources."""
        if self._picamera2 is not None:
            try:
                self._picamera2.stop()
                self._picamera2.close()
            except Exception:
                pass
            self._picamera2 = None

        if self._vcapture is not None:
            try:
                self._vcapture.release()
            except Exception:
                pass
            self._vcapture = None

        self._backend = None
        logger.info("摄像头资源已释放")

    @property
    def backend(self) -> str:
        """当前使用的摄像头后端名称 (picamera2 / cv2 / None)。"""
        return self._backend

    @property
    def available(self) -> bool:
        """摄像头是否可用。"""
        return self._backend is not None

    def __del__(self):
        self.release()
