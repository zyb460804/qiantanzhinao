"""Authenticated WeChat Pay v3 and Alipay statement download adapters."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import zipfile
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.config import Settings, settings


class ChannelBillDownloadError(RuntimeError):
    pass


MAX_DOWNLOADED_BILL_SIZE = 20 * 1024 * 1024


def _load_private_key(path_value: str):
    path = Path(path_value).expanduser()
    if not path_value or not path.is_file():
        raise ChannelBillDownloadError("支付渠道私钥文件未配置或不存在")
    try:
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (ValueError, TypeError) as exc:
        raise ChannelBillDownloadError("支付渠道私钥不是有效的 PEM RSA 私钥") from exc


def _rsa_sign(private_key, message: str) -> str:
    signature = private_key.sign(
        message.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def _validate_download_url(url: str, channel: str) -> None:
    parsed = urlparse(url)
    allowed_suffix = ".weixin.qq.com" if channel == "wechat" else ".alipay.com"
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        hostname == allowed_suffix[1:] or hostname.endswith(allowed_suffix)
    ):
        raise ChannelBillDownloadError("渠道返回了不受信任的账单下载地址")


async def _download_file(client: httpx.AsyncClient, url: str, channel: str) -> bytes:
    _validate_download_url(url, channel)
    response = await client.get(url)
    if response.status_code != 200:
        raise ChannelBillDownloadError(f"渠道账单文件下载失败，HTTP {response.status_code}")
    content = response.content
    if not content or len(content) > MAX_DOWNLOADED_BILL_SIZE:
        raise ChannelBillDownloadError("渠道账单文件为空或超过20MB")
    return _extract_csv(content)


def _extract_csv(content: bytes) -> bytes:
    if not content.startswith(b"PK"):
        return content
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            candidates = [
                info
                for info in archive.infolist()
                if not info.is_dir()
                and info.filename.lower().endswith((".csv", ".txt"))
                and "汇总" not in info.filename
            ]
            if not candidates:
                raise ChannelBillDownloadError("压缩账单中未找到交易明细 CSV")
            selected = max(candidates, key=lambda item: item.file_size)
            if selected.file_size > MAX_DOWNLOADED_BILL_SIZE:
                raise ChannelBillDownloadError("解压后的渠道账单超过20MB")
            return archive.read(selected)
    except zipfile.BadZipFile as exc:
        raise ChannelBillDownloadError("渠道返回的账单压缩包已损坏") from exc


async def download_wechat_bill(
    bill_date: date,
    *,
    config: Settings = settings,
    client: httpx.AsyncClient | None = None,
) -> bytes:
    if not all(
        (
            config.wechat_pay_mch_id,
            config.wechat_pay_serial_no,
            config.wechat_pay_private_key_path,
        )
    ):
        raise ChannelBillDownloadError("微信支付账单下载凭据未配置")
    private_key = _load_private_key(config.wechat_pay_private_key_path)
    request_path = f"/v3/bill/tradebill?bill_date={bill_date.isoformat()}&bill_type=ALL"
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    signature = _rsa_sign(
        private_key,
        f"GET\n{request_path}\n{timestamp}\n{nonce}\n\n",
    )
    authorization = (
        "WECHATPAY2-SHA256-RSA2048 "
        f'mchid="{config.wechat_pay_mch_id}",nonce_str="{nonce}",'
        f'timestamp="{timestamp}",serial_no="{config.wechat_pay_serial_no}",'
        f'signature="{signature}"'
    )
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    try:
        response = await http.get(
            config.wechat_pay_api_base.rstrip("/") + request_path,
            headers={"Authorization": authorization, "Accept": "application/json"},
        )
        if response.status_code != 200:
            raise ChannelBillDownloadError(f"微信支付账单申请失败，HTTP {response.status_code}")
        payload = response.json()
        download_url = payload.get("download_url")
        if not isinstance(download_url, str):
            raise ChannelBillDownloadError("微信支付未返回账单下载地址")
        content = await _download_file(http, download_url, "wechat")
        expected_hash = payload.get("hash_value")
        if payload.get("hash_type") == "SHA256" and isinstance(expected_hash, str):
            actual_hash = hashlib.sha256(content).hexdigest()
            if not secrets.compare_digest(actual_hash.lower(), expected_hash.lower()):
                raise ChannelBillDownloadError("微信支付账单文件哈希校验失败")
        return content
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise ChannelBillDownloadError("微信支付账单下载网络或响应异常") from exc
    finally:
        if owns_client:
            await http.aclose()


async def download_alipay_bill(
    bill_date: date,
    *,
    config: Settings = settings,
    client: httpx.AsyncClient | None = None,
) -> bytes:
    if not all((config.alipay_app_id, config.alipay_private_key_path)):
        raise ChannelBillDownloadError("支付宝账单下载凭据未配置")
    private_key = _load_private_key(config.alipay_private_key_path)
    params = {
        "app_id": config.alipay_app_id,
        "method": "alipay.data.dataservice.bill.downloadurl.query",
        "format": "JSON",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "biz_content": json.dumps(
            {"bill_type": "trade", "bill_date": bill_date.isoformat()},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    sign_content = "&".join(f"{key}={params[key]}" for key in sorted(params))
    params["sign"] = _rsa_sign(private_key, sign_content)
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    try:
        response = await http.post(config.alipay_gateway, data=params)
        if response.status_code != 200:
            raise ChannelBillDownloadError(f"支付宝账单申请失败，HTTP {response.status_code}")
        payload = response.json().get("alipay_data_dataservice_bill_downloadurl_query_response", {})
        if payload.get("code") != "10000":
            message = payload.get("sub_msg") or payload.get("msg") or "未知错误"
            raise ChannelBillDownloadError(f"支付宝账单申请失败: {message}")
        download_url = payload.get("bill_download_url")
        if not isinstance(download_url, str):
            raise ChannelBillDownloadError("支付宝未返回账单下载地址")
        return await _download_file(http, download_url, "alipay")
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise ChannelBillDownloadError("支付宝账单下载网络或响应异常") from exc
    finally:
        if owns_client:
            await http.aclose()


async def download_channel_bill(channel: str, bill_date: date) -> bytes:
    if channel == "wechat":
        return await download_wechat_bill(bill_date)
    if channel == "alipay":
        return await download_alipay_bill(bill_date)
    raise ChannelBillDownloadError(f"不支持的支付渠道: {channel}")
