"""Tests for media upload router (§5.9, §5.10).

Covers normal flow, MIME validation, auth, idempotency.
Note: file upload tests use httpx's file support.
"""

import uuid
from io import BytesIO

import pytest


class TestMediaUpload:
    async def test_upload_image(self, client):
        """Upload a valid JPEG image."""
        fake_jpeg = BytesIO(
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\x09\x09"
            b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f"
            b"\x1e\x1d\x1a\x1c\x1c $.\x27 \x1c\x1c(7),\x01\x08\x08\x08\n\x08"
            b"\x1c\n\n\x1c\x3f\x24\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20"
            b"\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20"
            b"\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20\x20"
            b"\xff\xd9"
        )
        res = await client.post("/api/v1/media/upload", files={
            "file": ("test.jpg", fake_jpeg, "image/jpeg"),
        }, data={
            "media_type": "image",
            "business_type": "waste_photo",
            "idempotency_key": str(uuid.uuid4()),
        })
        assert res.status_code == 200
        data = res.json()
        assert data["code"] == 0
        assert "file_id" in data["data"]
        assert data["data"]["media_type"] == "image"

    async def test_upload_idempotent(self, client):
        """Same idempotency_key returns duplicate (409)."""
        idem_key = str(uuid.uuid4())
        fake_jpeg = BytesIO(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00"
                            b"\x01\x00\x01\x00\x00\xff\xdb\x00C\x00\x08\x06\x06"
                            b"\x07\x06\x05\x08\x07\x07\x07\x09\x09\x08\n\x0c\x14"
                            b"\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f"
                            b"\x1e\x1d\x1a\x1c\x1c $.\x27 \x1c\x1c(7),1\xff\xd9")

        res1 = await client.post("/api/v1/media/upload", files={
            "file": ("photo.jpg", fake_jpeg, "image/jpeg"),
        }, data={
            "media_type": "image",
            "idempotency_key": idem_key,
        })
        assert res1.status_code == 200

        # Second upload with same key
        fake_jpeg2 = BytesIO(b"different content")
        res2 = await client.post("/api/v1/media/upload", files={
            "file": ("photo2.jpg", fake_jpeg2, "image/jpeg"),
        }, data={
            "media_type": "image",
            "idempotency_key": idem_key,
        })
        assert res2.status_code == 200
        assert res2.json()["code"] == 409
        assert res2.json()["data"]["duplicate"] == True  # noqa: E712

    async def test_upload_invalid_mime(self, client):
        """Upload with unsupported MIME type should be rejected."""
        fake_text = BytesIO(b"not a real image")
        res = await client.post("/api/v1/media/upload", files={
            "file": ("test.txt", fake_text, "text/plain"),
        }, data={
            "media_type": "image",
            "idempotency_key": str(uuid.uuid4()),
        })
        assert res.status_code == 400

    async def test_upload_unknown_media_type_rejected(self, client):
        """审计 H-2 fail-open 回归：未知 media_type 必须整体拒绝（422），
        不允许绕过扩展名/MIME 白名单把 .html 落盘。"""
        evil = BytesIO(b"<html><script>alert(1)</script></html>")
        res = await client.post("/api/v1/media/upload", files={
            "file": ("evil.html", evil, "text/html"),
        }, data={
            "media_type": "foo",
            "idempotency_key": str(uuid.uuid4()),
        })
        assert res.status_code == 422

    async def test_upload_html_with_valid_media_type_rejected(self, client):
        """合法 media_type 下，白名单外的扩展名/MIME 仍必须拒绝（fail-closed）。"""
        evil = BytesIO(b"<html><script>alert(1)</script></html>")
        res = await client.post("/api/v1/media/upload", files={
            "file": ("evil.html", evil, "text/html"),
        }, data={
            "media_type": "image",
            "idempotency_key": str(uuid.uuid4()),
        })
        assert res.status_code == 400

    async def test_upload_document_pdf_accepted(self, client):
        """合法 document 类型（PDF）照常可传 —— 白名单收紧不误伤正常流程。"""
        fake_pdf = BytesIO(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<</Type/Catalog>>endobj")
        res = await client.post("/api/v1/media/upload", files={
            "file": ("invoice.pdf", fake_pdf, "application/pdf"),
        }, data={
            "media_type": "document",
            "business_type": "purchase_cert",
            "idempotency_key": str(uuid.uuid4()),
        })
        assert res.status_code == 200
        data = res.json()
        assert data["code"] == 0
        assert data["data"]["media_type"] == "document"

    async def test_upload_no_file(self, client):
        """Missing file should be rejected."""
        res = await client.post("/api/v1/media/upload", data={
            "media_type": "image",
        })
        assert res.status_code == 422

    async def test_upload_without_idempotency_key(self, client):
        """Upload without idempotency key should still work."""
        fake_jpeg = BytesIO(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00"
                            b"\x01\x00\x01\x00\x00\xff\xdb\x00C\x00\x08\x06\x06"
                            b"\x07\x06\x05\x08\x07\x07\x07\x09\x09\x08\n\x0c\x14"
                            b"\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f"
                            b"\x1e\x1d\x1a\x1c\x1c $.\x27 \x1c\x1c(7),1\xff\xd9")
        res = await client.post("/api/v1/media/upload", files={
            "file": ("test.jpg", fake_jpeg, "image/jpeg"),
        }, data={
            "media_type": "image",
        })
        assert res.status_code == 200


class TestMediaListing:
    async def test_list_media_files(self, client):
        res = await client.get("/api/v1/media/files")
        assert res.status_code == 200
        data = res.json()
        assert "data" in data

    async def test_list_media_by_business_type(self, client):
        res = await client.get("/api/v1/media/files?business_type=waste_photo")
        assert res.status_code == 200


class TestUnauthenticated:
    async def test_upload_no_auth(self, auth_client):
        fake = BytesIO(b"test")
        res = await auth_client.post("/api/v1/media/upload", files={
            "file": ("test.jpg", fake, "image/jpeg"),
        }, data={"media_type": "image"})
        assert res.status_code == 401

    async def test_list_no_auth(self, auth_client):
        res = await auth_client.get("/api/v1/media/files")
        assert res.status_code == 401
