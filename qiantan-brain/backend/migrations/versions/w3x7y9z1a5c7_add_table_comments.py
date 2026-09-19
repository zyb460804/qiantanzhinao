"""为全部业务表写入中文表注释（COMMENT ON TABLE）

Revision ID: w3x7y9z1a5c7
Revises: t2e3f4a5b7c8
Create Date: 2026-09-19

注释的权威来源是 app/models/*.py 各模型的 __table_args__["comment"]；
本迁移是该元数据的一次快照（显式列出表名→注释），不 import 模型——
模型后续演进不得追溯改变本迁移的行为。

SQLite 无 COMMENT ON 语法（dev 默认库），upgrade/downgrade 直接跳过：
注释仅存在于 ORM 元数据，create_all 路径同样不落库。PostgreSQL/MySQL
路径下 upgrade 写入 COMMENT，downgrade 用 drop_table_comment 清除。
"""

from collections.abc import Sequence

from alembic import op


revision: str = "w3x7y9z1a5c7"
down_revision: str | None = "t2e3f4a5b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_COMMENTS: dict[str, str] = {
    "merchants": "商户（摊主）账号：多租户归属、微信 openid 绑定与全局角色",
    "tenants": "SaaS 租户/组织：下辖多个商户的顶层实体，绑定套餐与试用到期",
    "plans": "订阅套餐定义：free/pro/enterprise 的价格、配额上限与功能开关",
    "subscriptions": "租户订阅：计费周期与状态流（trialing→active→past_due→canceled/expired）",
    "saas_invoices": "SaaS 账单：每订阅每计费周期唯一，含明细行、到期日与支付回执",
    "usage_records": "用量计量：租户×指标×日的聚合值（API 调用/存储/商户数/语音时长），用于配额计费",
    "api_keys": "租户 API 密钥：只存 SHA-256 哈希与展示前缀，支持权限范围与过期",
    "platform_admins": "平台管理员账号：Web 管理后台登录，不归属任何租户、全局权限",
    "admin_audit_logs": "平台管理员操作审计日志：登录及租户/套餐/计费等后台操作留痕",
    "auth_revoked_tokens": "已吊销的 JWT 令牌（按 jti 存储）：用户注销后令牌立即失效",
    "idempotency_records": "接口幂等记录：同租户+操作+幂等键缓存响应，重放请求直接返回原结果",
    "product_categories": "商品品类（旧扁平目录）：向后兼容保留，新业务请使用 product_skus",
    "product_skus": "商品 SKU：可经营的最小商品单元，库存/批次/账本的真正主键",
    "product_aliases": "商品别名映射：番茄/西红柿/洋柿子指向同一 SKU，语音识别的关键",
    "product_specifications": "商品规格：大果/精品等同 SKU 分级，含相对标准售价的加价",
    "units": "计量单位字典：斤/筐/件等，区分重量/包装/计件三类",
    "unit_conversions": "单位换算因子：to_base=数量×factor（筐→斤按商品定），sku 为空即通用换算",
    "suppliers": "供应商档案：联系方式/起订量/账期/证照，及缺斤率退货率等质量评分",
    "supplier_products": "供应商报价：某供应商对某 SKU 的近期价格与最小起订量，支撑比价",
    "price_history": "售价变更流水：改价动作可审计（AI 改价/手动/清货），支撑效果复盘",
    "inventory_records": "库存流水账：入库/出库/报损/盘点调整每一笔，含幂等键、撤销与冲正，余额靠聚合",
    "current_inventory": "当前库存汇总：按商户×商品聚合的现存量与移动均价，由流水刷新",
    "batch_lifecycles": "批次生命周期表（一批一码追溯）：状态机+临期促销+锁定召回+二维码数据",
    "purchase_lists": "采购单：AI 建议→确认→到货验收→入库→完成的闭环状态机与付款状态",
    "purchase_items": "采购明细：下单量/实收量、毛重皮重净重、缺斤破损退货与验收结果",
    "supplier_payables": "供应商应付账款流水：进货记应付、付款记核销，欠款余额由流水聚合得出",
    "customer_receivables": "客户应收账款流水：赊账记应收、回款记核销（饭店/食堂等赊账客户）",
    "customer_credit_profiles": "客户信用档案：信用额度、默认账期、停赊标记（余额由应收流水聚合）",
    "sale_orders": "POS 销售订单：零售/赊销/挂单/退款状态机，含离线幂等 client_id",
    "sale_order_items": "销售订单行项目：数量/单价/FIFO 成本，支持单品退款与回库",
    "payments": "支付流水：现金/微信/支付宝/卡/赊账，支持组合支付与退款关联订单",
    "daily_settlements": "每日日结：按渠道收款汇总与销售-实收差异，关闭时保存统计快照",
    "reconciliations": "日结对账记录：销售总额 vs 支付总额 vs 库存消耗成本的每日核对",
    "voice_logs": "语音记账日志：原始语音地址、ASR 文本、解析出的事件与确认状态",
    "recommendations": "AI 建议记录：采购/备货建议、依据与置信度，采纳后回写实际偏差",
    "ai_actions": "AI 建议的可执行动作：清货/采购/改价/备货任务及其执行状态与结果",
    "audit_logs": "商户数据变更审计日志：create/edit/void 操作前后快照，只增不改",
    "sensitive_operations": "敏感操作审计：改价/退款/删除等需授权操作的前后快照与授权人",
    "staff_members": "员工档案：角色权限、启用状态与 PIN bcrypt 哈希（登录用）",
    "merchant_preferences": "商户偏好设置：风险偏好、语音方言、常用商品与个性化画像数据",
    "merchant_feedback": "商户反馈：小程序「我的」页提交的意见与问题",
    "simulation_records": "经营沙盘推演记录：假设参数输入与模拟结果输出（定价/备货推演）",
    "media_files": "上传媒体文件登记：业务凭证归类、保留期限与断点续传幂等",
    "expenses": "经营费用：租金/水电/人工/手续费等支出流水，可关联发票",
    "invoices": "数电发票归档：发票号同商户唯一，金额/税额与影像文件地址",
    "environment_records": "环境与日历记录：温度/天气/降雨/节假日，销量预测的外部因子（每日每城一条）",
    "payment_channels": "支付渠道配置：微信/支付宝子商户号与费率（只存引用，不存密钥）",
    "reconciliation_tasks": "每日渠道对账任务：系统订单 vs 渠道账单的总额、笔数与差异汇总",
    "reconciliation_differences": "对账差异明细：系统单边/渠道单边/金额不符/重复收款逐条登记处理",
    "channel_bill_imports": "渠道账单导入批次：按文件哈希去重的不可变导入记录",
    "channel_bill_entries": "渠道账单流水行：标准化收退款明细及其与系统支付的对账匹配状态",
    "devices": "IoT 设备注册表：智能秤/摄像头/电子价签/打印机，商户内序列号唯一",
    "price_displays": "电子价签/顾客价目屏同步状态：当前价、价格来源与同步结果",
    "device_firmwares": "设备固件 OTA 版本管理：文件哈希、灰度发布比例、变更日志",
    "device_model_versions": "设备端模型版本上报：视觉/语音模型在设备上的实际运行版本",
    "device_remote_logs": "设备远程日志收集：DEBUG/INFO/WARN/ERROR 级别日志上报与排查",
    "edge_events": "边缘设备上报事件：称重/视觉/心跳，按商户+event_id 幂等去重",
    "dead_letter_events": "同步死信队列：处理失败的离线事件，按重试策略自动重试或人工处理",
    "markets": "菜市场实体：市场管理后台的顶层组织单元",
    "market_merchants": "商户入场登记：摊位号、证照、健康证到期日、食安评分与经营状态",
    "market_inspections": "市场巡检记录：食安/设备/卫生检查的结果、照片与备注",
    "market_complaints": "投诉处理：顾客投诉的受理、处理过程与关闭归档",
    "stocktake_sessions": "盘点场次：账面 vs 实盘的总量与盘亏金额汇总（进行中/完成/取消）",
    "stocktake_items": "盘点明细行：单商品账面量/实盘量/差异及原因（自然损耗/漏记/秤错等）",
}

_COMMENT_BACKENDS = ("postgresql", "mysql")


def _supports_comments() -> bool:
    return op.get_bind().dialect.name in _COMMENT_BACKENDS


def upgrade() -> None:
    if not _supports_comments():
        # SQLite 无 COMMENT ON 语法，注释仅存在于 ORM 元数据
        return
    for table, comment in TABLE_COMMENTS.items():
        op.create_table_comment(table, comment)


def downgrade() -> None:
    if not _supports_comments():
        return
    for table in TABLE_COMMENTS:
        op.drop_table_comment(table)
