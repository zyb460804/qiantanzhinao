"""
API client for uploading recognition results to the cloud backend.

Uses device-level authentication with X-Api-Key / X-Device-Id headers
and proper JSON serialization for detection data.
"""

import hashlib
import time
import uuid
from datetime import UTC, datetime

import httpx


class APIClient:
    """HTTP client for the QianTan Brain backend API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000/api/v1",
        api_key: str = "",
        device_id: str = "",
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.device_id = device_id
        self.client = httpx.Client(timeout=10.0)

    def _auth_headers(self) -> dict:
        """Build device authentication headers."""
        if not self.api_key or not self.device_id:
            return {}
        return {
            "X-Api-Key": self.api_key,
            "X-Device-Id": self.device_id,
            "X-Timestamp": str(int(time.time())),
            "X-Nonce": str(uuid.uuid4()),
        }

    def upload_recognition(
        self, merchant_id: str, image_bytes: bytes, detections: list[dict]
    ) -> dict:
        """
        Upload image and recognition results to backend.

        Args:
            merchant_id: UUID of the merchant.
            image_bytes: JPEG image bytes.
            detections: List of detection dicts from YOLOInference.predict().

        Returns:
            API response dict.
        """
        # merchant_id is intentionally not sent: the backend derives ownership
        # from the registered device bound to this API key tenant.
        _ = merchant_id  # retained for backward-compatible callers
        payload = {
            "event_id": str(uuid.uuid4()),
            "device_id": self.device_id,
            "event_type": "vision",
            "occurred_at": datetime.now(UTC).isoformat(),
            "detections": detections,
            "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
        }
        response = self.client.post(
            f"{self.base_url}/edge/ingest/device",
            json=payload,
            headers=self._auth_headers(),
        )
        return response.json()

    def close(self):
        self.client.close()
