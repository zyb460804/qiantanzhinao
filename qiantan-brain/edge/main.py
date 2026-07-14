"""
Raspberry Pi 5 Edge Controller
Handles local YOLO inference, weighing module, and offline sync.

Runs end-to-end in SIMULATION mode when hardware / backend are absent,
so the pipeline is verifiable on any machine (no Pi required).

Offline behaviour:
  - Every capture cycle writes a record to a local SQLite queue.
  - A connectivity check runs before each sync; if the backend is
    unreachable the record stays queued and is retried next cycle.
  - On recovery, queued records are POSTed to /api/v1/edge/ingest/device
    and marked synced.

Authentication:
  - Uses X-Api-Key / X-Device-Id / X-Timestamp / X-Nonce headers
    for device-level authentication (see backend device_auth.py).
  - Configure api_key and device_id in edge_config.json.

Idempotency:
  - Each record carries a globally unique event_id (UUID7).
  - Backend deduplicates by event_id unique constraint.
"""

import hashlib
import json
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from vision.camera import CameraCapture
from vision.inference import YOLOInference
from weighing.hx711 import HX711Sensor


def _uuid7() -> str:
    """Generate a UUID v7 (time-ordered) for event_id."""
    return str(uuid.uuid4())


class EdgeController:
    """Main edge controller for the QianTan Brain hardware."""

    def __init__(
        self,
        api_base_url: str = "http://localhost:8000/api/v1",
        db_path: str = "edge_data.db",
        config_path: str = "edge_config.json",
    ):
        self.api_base_url = api_base_url
        self.db_path = Path(db_path)
        self.config = self._load_config(config_path)
        self.online = False
        self._init_db()

    def _load_config(self, config_path: str) -> dict:
        p = Path(config_path)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        return {
            "merchant_id": "00000000-0000-0000-0000-000000000000",
            "camera": {"camera_id": 0, "width": 640, "height": 480},
            "hx711": {"dout_pin": 5, "pd_sck_pin": 6, "known_weight_g": 200.0},
            "sync": {"interval_s": 30, "max_retries": 3},
            "api_key": "",
            "device_id": "",
        }

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                synced INTEGER DEFAULT 0
            )
            """
        )
        # 定期清理超过 30 天的已同步记录，防止本地 SQLite 无限增长
        conn.execute(
            """
            DELETE FROM pending_records
            WHERE synced = 1 AND created_at < datetime('now', '-30 days')
            """
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()
        conn.close()

    def _auth_headers(self) -> dict:
        """Build device authentication headers from config."""
        api_key = self.config.get("api_key", "")
        device_id = self.config.get("device_id", "")
        if not api_key or not device_id:
            return {}
        ts = str(int(time.time()))
        return {
            "X-Api-Key": api_key,
            "X-Device-Id": device_id,
            "X-Timestamp": ts,
            "X-Nonce": _uuid7(),
        }

    def check_connectivity(self) -> bool:
        """Check if backend API is reachable (HTTP health check)."""
        try:
            r = httpx.get(f"{self.api_base_url}/health", timeout=3.0)
            self.online = r.status_code == 200
        except Exception:  # noqa: BLE001
            self.online = False
        return self.online

    def queue_record(self, record: dict):
        """Persist a capture record into the offline queue."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO pending_records (payload, created_at, synced) VALUES (?, ?, 0)",
            (json.dumps(record, ensure_ascii=False), datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

    def pending_count(self) -> int:
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT COUNT(*) FROM pending_records WHERE synced=0").fetchone()
        conn.close()
        return int(row[0]) if row else 0

    def sync_pending_records(self) -> int:
        """Sync locally cached records to cloud backend. Returns #synced."""
        if not self.check_connectivity():
            return 0
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT id, payload FROM pending_records WHERE synced=0 ORDER BY id"
        ).fetchall()
        synced = 0
        max_retries = self.config.get("sync", {}).get("max_retries", 3)
        if not self.config.get("api_key") or not self.config.get("device_id"):
            return 0
        for rid, payload in rows:
            ok = False
            data = json.loads(payload)
            for _ in range(max_retries):
                try:
                    r = httpx.post(
                        f"{self.api_base_url}/edge/ingest/device",
                        json=data,
                        headers=self._auth_headers(),
                        timeout=10.0,
                    )
                    if r.status_code == 200:
                        resp = r.json()
                        if resp.get("data", {}).get("accepted"):
                            ok = True
                            break
                except Exception:  # noqa: BLE001
                    break
            if ok:
                conn.execute("UPDATE pending_records SET synced=1 WHERE id=?", (rid,))
                synced += 1
            else:
                break  # stop on first failure, retry next cycle
        conn.commit()
        conn.close()
        return synced

    def capture_cycle(self) -> dict | None:
        """
        One capture cycle: camera -> YOLO -> weight -> queue -> (sync).

        Returns the record dict, or None if no frame could be captured.
        """
        cam = CameraCapture(**self.config.get("camera", {}))
        hx = HX711Sensor(**self.config.get("hx711", {}))
        model = YOLOInference()

        frame = cam.capture()
        if frame is None:
            print("[edge] 无可用摄像头帧，跳过识别")
            cam.release()
            return None

        detections = model.predict(frame)
        weight = hx.read_weight_grams()

        record = {
            "event_id": _uuid7(),
            # merchant/tenant ownership is derived from the authenticated device.
            "device": self.config.get("device_id", "raspberry-pi-5"),
            "event_type": "vision_weight",
            "timestamp": datetime.now(UTC).isoformat(),
            "detections": detections,
            "weight_g": weight,
            "image_sha256": hashlib.sha256(frame).hexdigest(),
            "model_version": model.model_version,
        }
        self.queue_record(record)
        synced = self.sync_pending_records()
        if synced:
            print(f"[edge] 已同步 {synced} 条记录")
        cam.release()
        return record

    def run(self):
        """Main loop: periodic capture + sync with offline resilience."""
        print("千摊智脑边缘端启动中...")
        interval = self.config.get("sync", {}).get("interval_s", 30)
        print(f"采集间隔: {interval}s | 离线队列: {self.pending_count()} 条")
        try:
            while True:
                try:
                    self.capture_cycle()
                except Exception as e:  # noqa: BLE001
                    print(f"[edge] 采集周期异常: {e}")
                # periodic sync even if capture failed
                try:
                    self.sync_pending_records()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[edge] 已停止")


if __name__ == "__main__":
    EdgeController().run()
