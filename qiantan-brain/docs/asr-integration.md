# 讯飞语音识别接入手册 (ASR Integration)

千摊智脑语音记账链路：

```text
真实麦克风录音
  → 上传后端 /api/v1/voice/upload
  → asr_iflytek.transcribe_audio() 调用讯飞转写
  → parse_voice_text() 领域语义解析
  → 用户确认 / 纠错 / 作废
  → 生成库存记录
```

> 状态：**后端已接讯飞 ASR 接口（`backend/app/routers/voice.py` → `backend/app/services/asr_iflytek.py`）。** 当缺少凭证时自动降级为演示模式。小程序 `pages/voice/voice.js` 仅在显式 `demoMode=true`（默认 `false`）时才使用随机模拟文本。

## 1. 配置项（.env）

| 变量 | 说明 |
|------|------|
| `ASR_APP_ID` | 讯飞开放平台应用 ID |
| `ASR_API_KEY` | API Key |
| `ASR_API_SECRET` | API Secret（WebSocket 签名用） |
| `ASR_API_URL` | 默认 `https://iat-api.xfyun.cn/v2/iat` |

## 2. 鉴权机制（WebSocket）

讯飞 IAT 使用 **WebSocket + HMAC-SHA256** 鉴权：

1. 构造 `date` 头（`RFC1123` 格式）；
2. 拼接签名原文：`host + date + "GET /v2/iat HTTP/1.1"`；
3. 用 `ASR_API_SECRET` 做 **HMAC-SHA256** 得到 `signature`；
4. Base64 后拼成 `authorization` 头：`api_key="..." algorithm="hmac-sha256" headers="host date request-line" signature="..."`；
5. 以 `?authorization=...&date=...&host=...` 作为 WebSocket 连接参数。

支持参数：`language=zh_cn`、`accent=dictrict`（方言，如 `mandarin` / 具体方言）、`domain=iat`。

## 3. 音频格式

- 建议上传 **16k / 单声道 / 16bit PCM WAV** 或小程序录音临时文件转码后的格式。
- 后端 `asr_iflytek.transcribe_audio(audio_bytes, ...)` 负责按讯飞要求分片推送并聚合返回文本。

## 4. 失败兜底

- 无凭证 / 网络失败 / 转写空结果 → 返回错误，由小程序提示用户**手动输入文本**，而不是随机模拟文本（避免误导演示）。
- 这是真实链路与演示模式的关键区别：演示模式仅在用户主动开启 `demoMode` 时存在。

## 5. 小程序侧

`pages/voice/voice.js` 的 `onUploadTap` 应：
1. 录音结束后将音频文件 `wx.uploadFile` 到 `/api/v1/voice/upload`；
2. 拿到 `transcript` 后走 `parse-text`；
3. 仅在 `demoMode` 为真时使用本地 `mockTexts` 走通流程。
