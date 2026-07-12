"""
API client for uploading recognition results to the cloud backend.
"""

import httpx


class APIClient:
    """HTTP client for the QianTan Brain backend API."""

    def __init__(self, base_url: str = "http://localhost:8000/api/v1"):
        self.base_url = base_url
        self.client = httpx.Client(timeout=10.0)

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
        files = {
            "image": ("product.jpg", image_bytes, "image/jpeg"),
        }
        data = {
            "merchant_id": merchant_id,
            "detections": str(detections),
        }
        response = self.client.post(
            f"{self.base_url}/vision/recognize",
            files=files,
            data=data,
        )
        return response.json()

    def close(self):
        self.client.close()
