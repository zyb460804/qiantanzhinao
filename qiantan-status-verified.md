# 千摊智脑 · 当前状态（代码核实版 · 2026-07-12 更新）

> 说明：以下每项均从实际代码文件核实，139 passed→109 passed 是因部分测试在重构中合并/移除，非功能回退。
> 测试基线：**109 passed, 1 skipped**（pytest，3s 内完成）

---

## 一、整体完成度

| 维度 | 状态 |
|------|------|
| 软件演示闭环 | ~92% |
| 计划书全部目标 | ~85%（缺口在真实训练权重 / 边缘实机 / 实地测试 / 论文实验 / 部分文档） |

---

## 二、已落地能力（逐条核实）

### 鉴权与安全
| 能力 | 证据 |
|------|------|
| 微信登录 + JWT 鉴权 | `core/security.py`：`get_current_merchant` + PyJWT + code2session + 令牌吊销 |
| 8 个路由模块身份均来自 token | auth/login/refresh/logout/me 全链路；advice/inventory/purchase/voice/behavior/reports/twin/vision 均走 `get_merchant_id` |
| Edge 鉴权收口 | `edge.py` 接入 JWT；body merchant_id ≠ token → 403 |
| 生产安全自检 | `config.py`：`validate_security()` — DEBUG=false 时强制 JWT_SECRET ≥32 字节 + auth_allow_fallback=false |

### 数据完整性
| 能力 | 证据 |
|------|------|
| 库存流水账 + 作废/冲正 | `models/inventory.py`：is_voided/voided_at/void_reason/voided_by/original_record_id/is_correction |
| 批次 FIFO 消耗 + 回滚 | `services/batch.py`：consume_batches_fifo / rollback_batch_on_void / create_batch |
| 盘点差异闭环 | `models/stocktake.py` + SQL 聚合算账面 + 调整记录生成 + 幂等 |
| 审计日志 | `models/audit.py`：不可变审计日志（action + before/after JSON） |
| 采购偏差追踪 | `models/purchase.py`：deviation_ratio + 状态机 draft→confirmed→purchased→cancelled |
| 供应商/客户往来账 | `models/accounts.py` + `services/accounts_service.py`：应付/应收 + 幂等键 |
| POS 收银 + 日结对账 | `models/pos.py`：SaleOrder/SaleOrderItem/Payment/DailySettlement/Reconciliation |
| 离线同步 client_id 幂等 | `inventory_records`/`voice_logs` 含 `client_id` 唯一索引，批量入账端点 |
| AI 行动追踪 | `models/ai_action.py`：建议→可执行任务→采纳/完成 |

### 业务引擎
| 能力 | 证据 |
|------|------|
| 讯飞 ASR 真实语音转写 | `services/asr_iflytek.py`：HMAC-SHA256 + WebSocket 流式 + 方言 + HTTP 降级 |
| ONNX 视觉推理框架 | `edge/vision/inference.py`：`_real_predict` ONNX Runtime，无权重降级 demo |
| 摄像头采集 | `edge/vision/camera.py`：picamera2 优先 → cv2 降级 → 无硬件优雅返回 |
| Prophet 预测服务 | `services/forecast.py`：<7天规则 / 7-30天移动平均 / >30天 Prophet+回归变量 / 自动回退 |
| 天气 + 节假日 | `services/weather.py`：QWeather API + 中国法定节假日 2025-2026 |
| YOLO 训练框架 | `ml/train_yolo.py` + `evaluate_yolo.py`：ultralytics 训练 + ONNX 导出 + mAP 评估 |

### 经营分析
| 能力 | 证据 |
|------|------|
| 利润口径（估算毛利） | `twin.py`：收入 − estimated_cogs（FIFO 已售成本），非现金流差值 |
| 六维风险雷达（真实计算） | capital_risk（现金流失衡）/ category_concentration_risk（HHI 指数）/ customer_flow_risk（7日交易笔数）… |
| 经营日报/周报/趋势 | `reports.py`：日报/周报/趋势/排行 + AI 总结 + 行动清单 + 健康评分 |
| AI 参谋（环境感知） | `advisor.py`：天气/节假日/周末感知 + 在线 Prophet / 移动平均 / 规则引擎自动择模 + 采购建议，失败回退规则引擎 |
| Dashboard 7/30 切换 + 客单价曲线 | `twin.py`：`/business-mirror` 返回 sales_7d/sales_30d + 客单价；小程序 dashboard 支持区间切换与双 Y 轴折线图 |

### 工程质量
| 能力 | 证据 |
|------|------|
| Alembic 数据库迁移 | `migrations/versions/5242218be814`：单基线，26 张业务表，空库独立建库 |
| 启动建表走 Alembic | `database.py`：`init_db()` → `alembic upgrade head`，存量库自动 stamp |
| 金额 Decimal 化 | 所有 Numeric 列 Mapped 为 Decimal，核心路由/服务 Decimal 运算 |
| SKU 查询层 | 库存聚合/批次 FIFO/POS 扣减优先按 sku_id |
| 时区规范 | `core/timezone.py`：utc_now/local_now/utc_today_start/local_today_start 统一入口 |
| pytest 测试套件 | 17 个测试文件，109 passed，覆盖鉴权/隔离/API/业务逻辑 |
| 差分隐私（经验云） | `experience_cloud.py`：epsilon 预算 + Laplace 噪声 + 查询预算 + 商户数量分桶 |

---

## 三、真实缺口（仍需做）

### 阻塞级（演示闭环依赖）
1. **YOLO 训练权重缺失** — `inference.py` 真推理代码就绪，但无 `best.pt`/`yolov8n_products.onnx`；当前无权重 → demo 模式。`datasets/products/` 只有 `data.yaml` + 15 类目名，无训练图片。
2. **边缘硬件未实机验证** — 树莓派 CSI、HX711 GPIO、本地 SQLite 离线队列、断网恢复同步均未在真机跑通。

### 重要级（论文/答辩/上线依赖）
3. **商户实地测试 + 算法消融实验** — 无实测数据（无环境 vs +天气 vs +节假日，证环境感知有效）。
4. **文档缺口** — deployment-guide / hardware-guide / model-training / asr-integration / demo-script / test-report / experiment-report / privacy-design 部分为骨架或空缺。

### P2 功能（后续迭代）
8. **消息通知** — 订阅消息 + 首页红点
9. **离线同步端到端** — 小程序离线队列 + 自动上传 + 冲突解决
10. **Web 经营后台** — 多摊管理 + 数据导出
11. **AI 经营问答** — 受控意图识别 + 工具调用
12. **预测评估看板** — 支撑论文和竞赛答辩

---

## 四、建议下一步（优先级排序）

1. **训 YOLO 权重**（先 5–10 类），放 `edge/vision/model/`，让视觉识别走真推理
2. **树莓派最小闭环**：camera → ONNX → HX711 → 本地保存 → 上传
3. **3–5 家商户实测 + 消融实验**
4. **补缺失文档 + 答辩/软著材料**

---

## 五、答辩/计划书表述建议

| 话题 | 建议表述 |
|------|----------|
| 语音 ASR | 已接讯飞 SDK 真实转写，无 key 降级文字输入 — "真实链路 + 优雅降级" |
| YOLO 视觉 | 推理框架/ONNX 链路完整，缺训练权重 — "框架就绪，权重训练中/当前演示降级" |
| 利润口径 | 已用 FIFO 估算毛利，口径正确（收入 − 已售成本） |
| 风险雷达 | 六维均真实计算，非占位 0，可直接展示 |
| 天气/节假日 | QWeather 真实接口，缺 key 走 mock；节假日已真实 |
| Prophet | "离线实验脚本已验证，在线服务按数据量自动择模并回退"（非全量 Prophet） |
| 鉴权 | JWT + 微信 code2session + 全路由覆盖 + 生产安全自检 |
| 数据完整性 | 批次 FIFO + 作废冲正 + 审计日志 + 盘点闭环 + 往来账 + 离线幂等 |
