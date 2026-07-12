"""iFlytek WebSocket ASR v2 service.

Provides `transcribe_audio()` for converting audio files to text via iFlytek's
real-time speech recognition API. Audio must be 16kHz, 16bit, mono PCM/WAV.

When credentials are missing, returns an empty string and logs a warning rather
than raising — callers can gracefully fall back to text input.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import wave
from datetime import datetime
from urllib.parse import quote, urlencode

from app.config import settings


logger = logging.getLogger(__name__)

# Dialect → iFlytek `pd` (language) parameter.
# `None` means普通话 uses default (no extra `pd` field needed).
DIALECT_MAP = {
    "mandarin": None,  # 普通话
    "southwest": "mandarin",  # 西南官话 (uses mandarin model)
    "henan": "mandarin",  # 河南话 (uses mandarin model)
    "cantonese": "cantonese",  # 粤语
    "sichuan": "sichuanese",  # 四川话
}

# Each audio frame sent to iFlytek must be 1280 bytes (40ms @ 16kHz/16bit/mono).
_FRAME_SIZE = 1280
# WebSocket read/overall timeout (seconds).
_TIMEOUT = 30
# iFlytek ASR host.
_HOST = "iat-api.xfyun.cn"


def _build_auth_url(api_url: str, api_key: str, api_secret: str) -> str:
    """Build authenticated WebSocket URL for iFlytek ASR v2.

    Uses HMAC-SHA256 signature over `host`, `date`, and request-line, per
    iFlytek's authentication spec.
    """
    timestamp = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    signature_origin = f"host: {_HOST}\ndate: {timestamp}\nGET /v2/iat HTTP/1.1"
    signature_sha = hmac.new(
        api_secret.encode("utf-8"),
        signature_origin.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = base64.b64encode(signature_sha).decode("utf-8")
    authorization_origin = (
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")

    params = {
        "authorization": authorization,
        "date": timestamp,
        "host": _HOST,
    }
    # Use https URL; websockets client converts to wss://
    return f"{api_url}?{urlencode(params, quote_via=quote)}"


def _read_pcm_frames(audio_path: str) -> tuple[list[bytes], int]:
    """Read audio file into 1280-byte PCM frames.

    Accepts WAV (header parsed) or raw PCM. Returns (frames, sample_rate).
    Raises ValueError if audio cannot be parsed or is not 16kHz/16bit/mono.
    """
    path = os.fspath(audio_path)
    raw: bytes
    sample_rate = 16000

    if path.lower().endswith(".wav"):
        with wave.open(path, "rb") as wf:
            sample_rate = wf.getframerate()
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            if sample_rate != 16000:
                raise ValueError(f"Audio sample rate must be 16kHz, got {sample_rate}Hz")
            if channels != 1:
                raise ValueError(f"Audio must be mono, got {channels} channels")
            if sample_width != 2:
                raise ValueError(f"Audio must be 16bit, got {sample_width * 8}bit")
            raw = wf.readframes(wf.getnframes())
    else:
        # Raw PCM — assume 16kHz/16bit/mono as required by spec.
        with open(path, "rb") as f:
            raw = f.read()

    frames: list[bytes] = []
    offset = 0
    total = len(raw)
    while offset < total:
        chunk = raw[offset : offset + _FRAME_SIZE]
        # Pad the last frame with silence so iFlytek gets a full 1280 bytes.
        if len(chunk) < _FRAME_SIZE:
            chunk = chunk + b"\x00" * (_FRAME_SIZE - len(chunk))
        frames.append(chunk)
        offset += _FRAME_SIZE

    if not frames:
        logger.warning("Audio file %s produced no PCM frames", path)
    return frames, sample_rate


def _build_request_frame(audio_chunk: bytes, is_first: bool, is_last: bool, pd: str | None) -> str:
    """Build a single JSON request payload for one audio frame."""
    payload: dict = {
        "common": {"app_id": settings.asr_app_id} if is_first else None,
        "business": (
            {
                "language": "zh_cn",
                "domain": "iat",
                "accent": "mandarin",
                "vad_eos": 5000,
                "dwa": "wpgs",
                **({"pd": pd} if pd else {}),
            }
            if is_first
            else None
        ),
        "data": {
            "status": (0 if is_first else (2 if is_last else 1)),
            "format": "audio/L16;rate=16000",
            "encoding": "raw",
            "audio": base64.b64encode(audio_chunk).decode("utf-8"),
        },
    }
    # Drop None top-level keys to keep payload compact.
    payload = {k: v for k, v in payload.items() if v is not None}
    return json.dumps(payload, ensure_ascii=False)


async def _transcribe_ws(auth_url: str, frames: list[bytes], pd: str | None) -> str:
    """Run the WebSocket transcription loop. Returns concatenated text."""
    import websockets  # imported lazily so missing dep only fails here

    text_parts: list[str] = []
    # Use ws/wss scheme based on auth_url's protocol.
    ws_url = auth_url.replace("https://", "wss://").replace("http://", "ws://")

    total = len(frames)
    try:
        async with websockets.connect(
            ws_url, max_size=None, ping_interval=None, close_timeout=5
        ) as ws:
            send_task = asyncio.create_task(_send_frames(ws, frames, pd))
            try:
                # Overall receive timeout via context manager (Py3.11+).
                async with asyncio.timeout(_TIMEOUT):
                    await _recv_loop(ws, text_parts, total)
            except TimeoutError:
                logger.warning("ASR receive loop timed out after %ds", _TIMEOUT)
            # Ensure sender completes.
            try:
                await asyncio.wait_for(send_task, timeout=5)
            except TimeoutError:
                send_task.cancel()
    except TimeoutError:
        logger.warning("ASR WebSocket connection timed out")
    except Exception as e:
        logger.error("ASR WebSocket error: %s", e, exc_info=True)

    return "".join(text_parts).strip()


async def _send_frames(ws, frames: list[bytes], pd: str | None) -> None:
    """Send all audio frames, with a small inter-frame delay to avoid backlog."""
    total = len(frames)
    for i, chunk in enumerate(frames):
        is_first = i == 0
        is_last = i == total - 1
        payload = _build_request_frame(chunk, is_first, is_last, pd)
        await ws.send(payload)
        # iFlytek recommends ~40ms between frames to match real-time playback.
        await asyncio.sleep(0.04)


async def _recv_loop(ws, text_parts: list[str], total_frames: int) -> None:
    """Receive messages until the final result frame arrives."""
    received_final = False
    while not received_final:
        try:
            message = await asyncio.wait_for(ws.recv(), timeout=_TIMEOUT)
        except TimeoutError:
            logger.warning("ASR recv timed out waiting for a frame")
            return
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("ASR returned non-JSON message: %r", message)
            continue

        code = data.get("code")
        if code != 0:
            message_text = data.get("message", "")
            logger.error("ASR error code=%s message=%s", code, message_text)
            return

        data_payload = data.get("data") or {}
        result_obj = data_payload.get("result") or {}
        ws_list = result_obj.get("ws") or []
        for w in ws_list:
            for c in w.get("cw") or []:
                text_parts.append(c.get("w", ""))

        if data_payload.get("status") == 2:
            received_final = True
            return


def _sync_http_fallback(audio_path: str, pd: str | None) -> str:
    """Synchronous fallback when websockets is unavailable.

    Uses httpx to POST audio to the iFlytek HTTP endpoint. This is a best-effort
    fallback; the primary path is WebSocket streaming.
    """
    import httpx

    auth_url = _build_auth_url(settings.asr_api_url, settings.asr_api_key, settings.asr_api_secret)
    frames, _ = _read_pcm_frames(audio_path)
    if not frames:
        return ""

    audio_b64 = base64.b64encode(b"".join(frames)).decode("utf-8")
    payload = {
        "common": {"app_id": settings.asr_app_id},
        "business": {
            "language": "zh_cn",
            "domain": "iat",
            "accent": "mandarin",
            "vad_eos": 5000,
            **({"pd": pd} if pd else {}),
        },
        "data": {
            "status": 2,
            "format": "audio/L16;rate=16000",
            "encoding": "raw",
            "audio": audio_b64,
        },
    }

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(auth_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        if data.get("code") != 0:
            logger.error("ASR HTTP fallback error: %s", data.get("message", ""))
            return ""
        result_obj = (data.get("data") or {}).get("result") or {}
        parts: list[str] = []
        for w in result_obj.get("ws") or []:
            for c in w.get("cw") or []:
                parts.append(c.get("w", ""))
        return "".join(parts).strip()
    except Exception as e:
        logger.error("ASR HTTP fallback failed: %s", e, exc_info=True)
        return ""


async def transcribe_audio(audio_path: str, dialect: str = "mandarin") -> str:
    """Transcribe an audio file via iFlytek ASR v2.

    Args:
        audio_path: Path to a 16kHz/16bit/mono WAV or raw PCM file.
        dialect: One of DIALECT_MAP keys (mandarin, southwest, henan,
            cantonese, sichuan).

    Returns:
        Recognized text (empty string on any failure or missing credentials).
    """
    if not (settings.asr_api_key and settings.asr_api_secret and settings.asr_app_id):
        logger.warning(
            "iFlytek ASR credentials not configured "
            "(asr_app_id/asr_api_key/asr_api_secret empty); "
            "returning empty transcription"
        )
        return ""

    pd = DIALECT_MAP.get(dialect, DIALECT_MAP["mandarin"])
    if dialect not in DIALECT_MAP:
        logger.warning("Unknown dialect '%s', falling back to mandarin", dialect)

    try:
        frames, sample_rate = _read_pcm_frames(audio_path)
    except (ValueError, OSError) as e:
        logger.error("ASR audio read failed for %s: %s", audio_path, e)
        return ""

    if not frames:
        logger.warning("No audio frames to transcribe from %s", audio_path)
        return ""

    duration = (len(frames) * _FRAME_SIZE) / (sample_rate * 2)  # 16bit = 2 bytes/sample
    logger.info("ASR start: %d frames, ~%.1fs, dialect=%s", len(frames), duration, dialect)

    auth_url = _build_auth_url(
        settings.asr_api_url,
        settings.asr_api_key,
        settings.asr_api_secret,
    )

    try:
        import websockets  # noqa: F401
    except ImportError:
        logger.warning("websockets library unavailable; using HTTP fallback")
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, _sync_http_fallback, audio_path, pd)
    else:
        text = await _transcribe_ws(auth_url, frames, pd)

    if text:
        logger.info(
            "ASR success: %.1fs audio -> %d chars: %s",
            duration,
            len(text),
            text[:80],
        )
    else:
        logger.warning("ASR returned empty text for %s", audio_path)
    return text
