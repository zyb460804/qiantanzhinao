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
872 tests collected（Python 3.13，pytest --collect-only 实测；通过数以当次 pytest 运行为准）
```

| 测试模块 | 覆盖内容 |
|----------|----------|
| `test_accounts_idempotency.py` | 往来账记账幂等（赊账/回款重复提交去重） |
| `test_accounts_statement_totals.py` | 往来账对账单合计口径 |
| `test_admin_auth.py` | 管理后台登录/登出/JWT Cookie |
| `test_admin_operations_api.py` | 管理后台运维监控接口 |
| `test_admin_permissions.py` | 管理后台 5 角色权限矩阵 |
| `test_admin_saas_api.py` | 租户/套餐/订阅/发票/用量 SaaS 接口 |
| `test_adversarial_audit_2026_08_16.py` | 2026-08-16 对抗审计回归用例 |
| `test_advice_api.py` | 每日建议（三行式）、What-if 沙盘、数字孪生看板、天气接口降级链；校验建议输出含 `forecast` 字段（按数据量自动选模型，Prophet 未安装时回落到移动平均） |
| `test_advice_cold_start.py` | 新商户冷启动建议 |
| `test_ai_actions_api.py` | AI Action 列表与状态流转 |
| `test_ai_actions_execute.py` | AI Action 执行链（改价/采购/清货/锁批次 + 审计） |
| `test_anomalies_api.py` | 销量异常检测接口 |
| `test_anomaly_detector.py` | 七检测器并联算法（z-score/MAD/IQR/均线/季节/零销量/数据错误） |
| `test_auth_api.py` | 微信登录、JWT 鉴权、刷新、吊销、商户隔离 |
| `test_batch.py` | 批次 FIFO 扣减、作废回滚、临期状态 |
| `test_behavior_api.py` | 行为埋点与反馈接口（`/feedback` 路径，位于 behavior 路由） |
| `test_catalog_api.py` | SKU/别名/规格/单位接口 |
| `test_catalog_sku_guardrails.py` | SKU 守卫（价格与单位红线） |
| `test_concurrency_regression.py` | 并发回归（行锁/幂等竞态） |
| `test_cst_day_alignment.py` | CST（UTC+8）日界对齐 |
| `test_dead_letter_replay.py` | 死信事件查看/重试/标记解决 |
| `test_device_api.py` | 设备注册/心跳/价签同步 |
| `test_dynamic_pricing.py` | 动态定价（Q10 衰减、阶梯折扣、底价） |
| `test_edge_auth.py` | Edge 端点 JWT 鉴权与 merchant_id 一致性校验 |
| `test_env_engine.py` | 温度 / 降雨 / 周末 / 节假日系数 |
| `test_environment_solar_term.py` | 节气与时令商品 |
| `test_expense_api.py` | 费用记账接口 |
| `test_food_safety_api.py` | 批次追溯 / 二维码 / 快检 / 锁批次接口 |
| `test_food_safety_state_machine.py` | 批次状态机转移约束 |
| `test_idempotency_middleware.py` | 写请求幂等中间件（登录响应豁免等） |
| `test_insights_api.py` | 「智能分析」页接口（异常检测/动态定价/报童模型） |
| `test_inventory_ledger_api.py` | 库存统一账本接口 |
| `test_inventory_optimizer.py` | 安全库存 / 再订货点 / 周期订货量 |
| `test_invoice_idempotency.py` | 发票生成幂等（跨入口去重） |
| `test_lifecycle.py` | 批次生命周期 |
| `test_main_exception_handlers.py` | 全局异常处理器与错误信封 |
| `test_market_admin_api.py` | 市场管理端（公告/巡检/投诉）接口 |
| `test_migration_alignment.py` | 模型与 Alembic 迁移对齐 |
| `test_offline_sync.py` | 离线批量入账幂等、重复键返回 duplicate |
| `test_operations_adversarial.py` | 运营中心对抗性用例 |
| `test_operations_api.py` | 运营中心（损耗/临期促销/赊账/导出）接口 |
| `test_operations_customer_ledger.py` | 客户赊账台账口径 |
| `test_operations_export_scope.py` | 导出数据范围与权限边界 |
| `test_operations_repay_and_waste_cost.py` | 赊账回款与损耗成本归集 |
| `test_pos_advanced_api.py` | POS 进阶（挂单/退款/组合支付） |
| `test_pos_api.py` | POS 开单 / 收款 / 日结 |
| `test_pos_money_flow.py` | POS 资金流一致性 |
| `test_pos_unit_conversion.py` | POS 单位换算（`unit_conversion` 服务） |
| `test_product_feedback_api.py` | 商品反馈接口 |
| `test_purchase_acceptance.py` | 采购到货验收与退货 |
| `test_purchase_api.py` | 采购建议 → 清单 → 入库闭环、供应商应付流水 |
| `test_quota_usage.py` | 配额检查与用量记录（先检查后记账） |
| `test_reconciliation_api.py` | 微信/支付宝渠道对账接口 |
| `test_reports_api.py` | 经营报表聚合 |
| `test_reports_period_params.py` | 日报/周报/月报历史日期参数 |
| `test_security_fixes_2026_08_16.py` | 2026-08-16 安全修复回归 |
| `test_settlement_lock.py` | 日结封账后开单/收款拦截 |
| `test_simulator.py` | What-if 单调性（买更多 → 损耗率不降） |
| `test_staff_owner_login_rejected.py` | 员工身份禁止以 owner 登录 |
| `test_staff_permissions.py` | 员工 6 角色 × 14 项权限矩阵 |
| `test_stocktake_api.py` | 盘点发起 / 差异 / 历史（含断点续盘） |
| `test_tenant_closure_2026_08_16.py` | 租户停服收口（suspended 后门禁拦截） |
| `test_tenant_portal_api.py` | 租户自助门户（订阅/用量/发票） |
| `test_timezone_boundaries.py` | 时区边界（UTC 存储与 CST 展示） |
| `test_unit_conversion.py` | 单位换算服务（SKU 专属因子优先） |
| `test_vision_api.py` | 视觉识别接口（演示模式） |
| `test_vision_onnx.py` | ONNX 推理管线 |
| `test_voice_api.py` | 语音上传 / 解析接口 |
| `test_voice_ledger_integrity.py` | 语音记账落账完整性 |
| `test_voice_multi_intent.py` | 多意图多笔记账（又/然后/再 切分） |
| `test_voice_p0_fixes.py` | 语音 P0 修复（汉字数字、口语金额抽取） |
| `test_voice_parser.py` | 领域语义解析（商品/数量/金额） |

## 3. 已覆盖

- 记账主链路（语音/POS/离线/边缘四通道）、批次与盘点、采购与验收、报表（含历史日期参数）、数字孪生与沙盘。
- SaaS 全链路（租户/套餐/订阅/发票/用量/配额/停服收口）、管理后台与市场管理端。
- 语音解析（汉字数字/口语金额/多意图）、单位换算、异常检测、动态定价、库存优化。
- 横切质量：幂等（中间件/POS/发票/离线/往来账）、并发回归、日结锁、时区边界、迁移对齐、安全修复回归、历轮对抗审计回归。

## 4. 尚未覆盖（需在真机/真网络下补）

- **真实硬件**：树莓派摄像头、HX711 称重 GPIO（当前以模拟模式验证逻辑）。
- **真实讯飞 ASR 网络链路**：依赖外部凭证与网络。
- **YOLO 训练权重精度**：当前权重为占位，mAP/P/R/FPS 待真实训练后补。
- **端到端弱网同步**：`edge/main.py` 离线队列 + `/edge/ingest` 联调建议在真机做一轮。
- **前端小程序**：WXSS 兼容性（已人工核对红线）与真机交互验收。
