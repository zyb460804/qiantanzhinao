# 支付渠道账单对账

## 操作流程

1. 在小程序“财务 -> 对账”选择账单日期和微信/支付宝。
2. 从微信聊天文件中选择渠道导出的 CSV 账单。
3. 系统按渠道交易号优先、商户订单号回退的规则逐笔匹配支付和退款。
4. 在任务中查看系统孤单、渠道孤单和金额不一致，填写核验结果后处理差异。
5. 同一文件重复上传会返回原导入批次，不重复写入明细，也不会覆盖已处理差异。

单个文件最大 10 MB、最多 50000 条有效支付/退款明细。文件可使用 UTF-8、UTF-8 BOM 或
GB18030 编码。

## 支持格式

系统可识别微信和支付宝常见账单字段，包括：

- 渠道交易号：`微信订单号`、`支付宝交易号`、`transaction_id`
- 商户订单号：`商户订单号`、`merchant_order_no`
- 金额：`应结订单金额`、`商家实收（元）`、`amount`
- 退款：`退款金额`、`微信退款单号`、`record_type=refund`
- 手续费：`手续费`、`服务费（元）`、`fee`

也可以使用统一格式：

```csv
transaction_id,merchant_order_no,amount,fee,status,record_type,occurred_at
420000000001,POS202607140001,10.00,0.06,SUCCESS,payment,2026-07-14 08:00:00
REFUND000001,POS202607140001,-2.00,0.00,SUCCESS,refund,2026-07-14 10:00:00
```

支付金额为正数。退款行可填写负数，或将 `record_type` 设为 `refund` 后填写正数。

## API

- `POST /api/v1/reconciliation/import/{date}?channel=wechat`
- `POST /api/v1/reconciliation/download/{date}?channel=wechat`
- `POST /api/v1/reconciliation/run/{date}?channel=wechat`
- `GET /api/v1/reconciliation/imports`
- `GET /api/v1/reconciliation/tasks`
- `GET /api/v1/reconciliation/tasks/{task_id}/differences`
- `POST /api/v1/reconciliation/differences/{difference_id}/resolve`

任务状态：

- `pending`：渠道账单尚未导入
- `balanced`：逐笔和汇总金额均一致
- `exception`：存在未处理差异
- `resolved`：全部差异已人工处理，但汇总金额仍有差异
