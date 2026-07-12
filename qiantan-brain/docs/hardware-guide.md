# 硬件接线与边缘端手册 (Hardware Guide)

适用于 **Raspberry Pi 5** 边缘终端：摄像头识别商品 + HX711 称重 + 弱网离线缓存 + 恢复同步。

> ⚠️ 参考说明：本手册对应的 `edge/` 代码已实现完整逻辑（摄像头、YOLO 推理、HX711 称重、本地 SQLite 队列、离线同步、健康检查、设备配置、开机自启），但**真机验证待进行**——需要真实树莓派 + 摄像头 + 称重模块实跑确认。无硬件时 `edge/main.py` 以「模拟模式」运行，逻辑可在任意机器验证。

## 1. CSI 摄像头（树莓派官方摄像头）

- 模块：`edge/vision/camera.py` 优先使用 `picamera2`。
- 安装：`pip install picamera2`。
- 软依赖：`Pillow`（采集帧转 JPEG）。

## 2. USB 摄像头（降级方案）

- 当 `picamera2` 不可用时，自动降级到 `cv2.VideoCapture(camera_id)`。
- 安装：`pip install opencv-python`。

## 3. HX711 称重模块接线

| HX711 引脚 | 树莓派 GPIO | 说明 |
|--------------|----------------|------|
| VCC | 5V (Pin 2/4) | 电源 |
| GND | GND (Pin 6/9) | 地 |
| DT (DOUT) | GPIO5 (Pin 29) | 数据（默认 `dout_pin=5`） |
| SCK | GPIO6 (Pin 31) | 时钟（默认 `pd_sck_pin=6`） |

> 引脚可在 `edge/edge_config.json` 的 `hx711` 段修改。

### 校准流程

```python
from weighing.hx711 import HX711Sensor
hx = HX711Sensor(dout_pin=5, pd_sck_pin=6)
hx.tare(times=10)                 # 空载去皮
hx.calibrate(known_weight_grams=200.0)  # 放 200g 标准砝码标定
print(hx.read_weight_grams())     # 读取稳定重量（自动滑动平均滤波）
```

## 4. 离线运行与同步

- 每次采集写入本地 `edge_data.db` 的 `pending_records` 队列表。
- `check_connectivity()` 通过 `/api/v1/health` 判断后端可达。
- 不可达时记录留在队列；恢复后 `sync_pending_records()` 将记录 POST 到 `/api/v1/edge/ingest` 并标记已同步。
- 配置见 `edge_config.json`（`sync.interval_s` 采集间隔、`sync.max_retries` 重试次数）。

## 5. 开机自启（systemd）

已提供 `edge/qiantan-edge.service`：

```bash
sudo cp edge/qiantan-edge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now qiantan-edge
```

服务文件已设 `HX711_SIMULATE=0` 以启用真实 GPIO（开发机无硬件时置 `1` 进入模拟模式）。
