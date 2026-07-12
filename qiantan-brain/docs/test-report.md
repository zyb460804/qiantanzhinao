# 测试报告 (Test Report)

## 1. 运行方式

```bash
cd backend
python -m pytest -q
# 或指定模块
python -m pytest tests/test_advice_api.py -v
```

异步测试由 `pytest-asyncio`（`asyncio_mode = auto`）驱动；数据库使用测试 fixtures 的内存库。

## 2. 测试结果

```text
109 passed, 1 skipped in ~4.2s
```

| 测试模块 | 覆盖内容 |
|----------|----------|
| `test_advice_api.py` | 每日建议（三行式）、What-if 沙盘、数字孪生看板、天气接口降级链；校验建议输出含 `forecast` 字段（Prophet 在线接入） |
| `test_auth_api.py` | 微信登录、JWT 鉴权、刷新、吊销、商户隔离 |
| `test_batch.py` | 批次 FIFO 扣减、作废回滚、临期状态 |
| `test_edge_auth.py` | Edge 端点 JWT 鉴权与 merchant_id 一致性校验 |
| `test_env_engine.py` | 温度 / 降雨 / 周末 / 节假日系数 |
| `test_experience_cloud.py` | 差分隐私——商户数分桶、Laplace 噪声居中性与标度、查询预算闸 |
| `test_offline_sync.py` | 离线批量入账幂等、重复键返回 duplicate |
| `test_purchase_api.py` | 采购建议 → 清单 → 入库闭环、供应商应付流水 |
| `test_reports_api.py` | 经营报表聚合 |
| `test_simulator.py` | What-if 单调性（买更多 → 损耗率不降） |
| `test_stocktake_api.py` | 盘点发起 / 差异 / 历史 |
| `test_unit_service.py` | 单位换算规范化 |
| `test_vision_api.py` | 视觉识别接口（演示模式） |
| `test_voice_api.py` | 语音上传 / 解析接口 |
| `test_voice_parser.py` / `test_voice_parser_v2.py` | 领域语义解析（商品/数量/金额）、结构化 BusinessEvent |

## 3. 已覆盖

- 建议 / 批次 / 环境引擎 / 采购 / 报表 / 沙盘 / 库存盘点 / 视觉 / 语音 / 语音解析 / 经验云隐私。

## 4. 尚未覆盖（需在真机/真网络下补）

- **真实硬件**：树莓派摄像头、HX711 称重 GPIO（当前以模拟模式验证逻辑）。
- **真实讯飞 ASR 网络链路**：依赖外部凭证与网络。
- **YOLO 训练权重精度**：当前权重为占位，mAP/P/R/FPS 待真实训练后补。
- **端到端弱网同步**：`edge/main.py` 离线队列 + `/edge/ingest` 联调建议在真机做一轮。
- **前端小程序**：WXSS 兼容性（已人工核对红线）与真机交互验收。
