# 千摊智脑 API 接口文档

> 版本: 0.1.0 | 更新日期: 2026-07-11
> Base URL: `http://localhost:8000/api/v1`

所有接口遵循 REST 风格,返回统一信封格式。本文档与代码同步,共 **8 个路由模块、25 个端点**。

---

## 一、通用约定

### 1.1 统一响应信封

```json
{
  "code": 0,
  "message": "success",
  "data": { ... }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | int | `0` = 成功,非零 = 业务错误 |
| `message` | string | 状态描述 |
| `data` | object/array/null | 业务数据负载 |

### 1.2 错误处理

| HTTP 状态 | 含义 |
|-----------|------|
| 200 | 请求成功(可能 code≠0 表示业务错误) |
| 422 | 参数校验失败(缺失/类型错误) |
| 422 | 请求体解析失败 |
| 500 | 服务端内部错误 |

### 1.3 商户标识

几乎所有业务接口需要 `merchant_id`(UUID)。当前 MVP 默认商户 ID 由种子脚本创建。

---

## 二、语音记账模块 `/voice`

### POST `/voice/upload`
上传录音文件。MVP 阶段 ASR 模拟,需配合 `/parse-text` 完成解析。

**请求**:`multipart/form-data`
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `merchant_id` | UUID | 是 | 商户ID |
| `audio` | file | 是 | 音频文件(.mp3/.wav,≤60s) |

**响应**:
```json
{
  "code": 0,
  "data": {
    "voice_log_id": "uuid",
    "asr_text": "",
    "parsed": null
  }
}
```

### POST `/voice/parse-text`
直接提交文本进行语义解析(联调主入口)。

**请求**:`application/json`
```json
{
  "merchant_id": "uuid",
  "text": "今天进了白菜50斤，三毛钱一斤"
}
```

**响应**:
```json
{
  "code": 0,
  "data": {
    "voice_log_id": "uuid",
    "asr_text": "今天进了白菜50斤，三毛钱一斤",
    "parsed": {
      "event_type": "purchase",
      "product": "白菜",
      "product_id": 1,
      "quantity": 50,
      "unit": "斤",
      "unit_cost": 0.3,
      "total_cost": 15.0,
      "confidence": 0.85,
      "missing_fields": [],
      "voice_log_id": "uuid"
    }
  }
}
```

| parsed 字段 | 类型 | 说明 |
|-------------|------|------|
| `event_type` | string | `purchase`/`sale`/`waste`/`unknown` |
| `product` | string? | 商品名(可能为空) |
| `product_id` | int? | 数据库品类ID |
| `quantity` | float? | 数量 |
| `unit` | string | 单位(默认"斤") |
| `unit_cost` | float? | 进价(采购时) |
| `unit_price` | float? | 售价(销售时) |
| `total_cost`/`total_revenue` | float? | 总金额 |
| `confidence` | float | 0~1 置信度 |
| `missing_fields` | string[] | 缺失字段名 |

### GET `/voice/logs`
查询语音记账历史。

**查询参数**:
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `merchant_id` | UUID | — | 商户ID |
| `page` | int | 1 | 页码 |
| `limit` | int | 20 | 每页条数 |

**响应**:`data` 为 voice_log 数组,含 `asr_text`、`parsed_event`、`status`、`created_at`。

### POST `/voice/correct`
修正解析结果。

**请求**:
```json
{
  "voice_log_id": "uuid",
  "corrections": { "product": "菠菜" }
}
```

### POST `/voice/confirm`
确认解析结果,触发库存入库。

**请求**:
```json
{ "voice_log_id": "uuid" }
```

**效果**:写入 `inventory_records`,更新 `voice_logs.status = "confirmed"`。

---

## 三、视觉识别模块 `/vision`

### GET `/vision/categories`
获取支持的 15 类商品列表。

### POST `/vision/recognize`
上传商品图片返回识别结果(边缘端 YOLOv8-nano 推理,后端为代理)。

**请求**:`multipart/form-data`
| 字段 | 类型 | 说明 |
|------|------|------|
| `merchant_id` | UUID | 商户ID |
| `image` | file | 商品图片 |

**响应**:
```json
{
  "code": 0,
  "data": {
    "detections": [
      { "product_id": 1, "name": "白菜", "confidence": 0.92 }
    ],
    "suggested_product": { "product_id": 1, "name": "白菜" },
    "processing_time_ms": 85
  }
}
```

---

## 四、库存管理模块 `/inventory`

### GET `/inventory/current`
查询当前库存(聚合后)。

**查询参数**:`merchant_id`(UUID)

**响应**:
```json
{
  "code": 0,
  "data": [
    {
      "product_id": 1,
      "product_name": "白菜",
      "current_qty": 35.0,
      "unit": "斤",
      "avg_cost": 0.35
    }
  ]
}
```

### GET `/inventory/history`
查询库存变动历史。

**查询参数**:
| 参数 | 类型 | 说明 |
|------|------|------|
| `merchant_id` | UUID | 商户ID |
| `product_id` | int? | 品类筛选 |
| `start` | date? | 起始日期 |
| `end` | date? | 结束日期 |
| `page`/`limit` | int | 分页 |

### GET `/inventory/alerts`
临期/缺货预警列表。

---

## 五、经营建议模块 `/advice` + `/simulate`

### GET `/advice/daily`
今日经营建议(三行式可解释格式)。读取环境数据 + 库存 + 历史销量,为每个活跃品类生成建议。

**查询参数**:`merchant_id`(UUID)

**响应**:
```json
{
  "code": 0,
  "data": {
    "recommendations": [
      {
        "product_id": 1,
        "product_name": "白菜",
        "suggestion": "建议明日采购白菜22斤",
        "basis": [
          { "factor": "近7日平均销量", "value": "18斤", "impact": "+" },
          { "factor": "周末客流", "value": "预计增加12%", "impact": "+" },
          { "factor": "当前库存", "value": "不足1天", "impact": "+" }
        ],
        "risk_warning": "若明日降雨概率超过50%，建议减少至15斤",
        "recommended_qty": 22,
        "confidence": 0.78
      }
    ],
    "env_summary": {
      "temp_high": 28, "rainfall_prob": 20, "is_weekend": false
    },
    "recommendation_ids": ["uuid", "uuid"]
  }
}
```

### POST `/simulate/what-if`
单方案 What-if 模拟(价格弹性 + 损耗 + 基准对比)。

**请求**:
```json
{
  "merchant_id": "uuid",
  "product_id": 1,
  "scenario": {
    "purchase_qty": 50,
    "unit_cost": 0.3,
    "unit_price": 1.5
  }
}
```

**响应**:
```json
{
  "code": 0,
  "data": {
    "input": { "purchase_qty": 50, "unit_cost": 0.3, "unit_price": 1.5 },
    "output": {
      "estimated_sales": 35.0,
      "estimated_revenue": 52.50,
      "total_cost": 15.00,
      "waste_qty": 12.8,
      "waste_loss": 1.92,
      "net_profit": 35.58,
      "margin_rate": 2.37,
      "waste_rate": 0.26
    },
    "comparison": {
      "baseline_net_profit": 28.00,
      "improvement": 7.58,
      "verdict": "有利：模拟方案优于基准方案",
      "recommendation": "预计净收益36元，损耗率26%在可接受范围内"
    }
  }
}
```

### POST `/simulate/scenario`
多方案对比(保守/基准/激进三档)。

**请求**:同 what-if,返回 `data.multi` 数组含三个方案的净收益/损耗率对比。

---

## 六、环境数据模块 `/env`

### GET `/env/today`
今日环境数据(缓存优先 → 和风API → mock 降级)。

**查询参数**:`city`(string,默认"上海")

**响应**:
```json
{
  "code": 0,
  "data": {
    "date": "2026-07-11",
    "temp_high": 32.5,
    "temp_low": 26.0,
    "weather_type": "多云",
    "rainfall_prob": 30.0,
    "is_holiday": false,
    "is_weekend": false,
    "day_of_week": 4,
    "source": "qweather"
  }
}
```

`source` 取值:`cached` / `qweather` / `mock`

### GET `/env/forecast`
未来 3 日预报。

### GET `/env/history`
环境数据历史(分页)。

---

## 七、数字孪生模块 `/twin`

### GET `/twin/dashboard`
首页聚合 KPI(今日收入/支出/毛利/风险评分)。

**响应**:
```json
{
  "code": 0,
  "data": {
    "today_revenue": 156.0,
    "today_cost": 85.0,
    "today_profit": 71.0,
    "risk_score": 45,
    "total_inventory_qty": 235,
    "expiring_count": 3
  }
}
```

### GET `/twin/inventory-mirror`
库存镜像(按品类聚合)。

**响应**:
```json
{
  "code": 0,
  "data": {
    "by_category": [
      { "category": "叶菜类", "total_qty": 85, "product_count": 3 },
      { "category": "根茎类", "total_qty": 60, "product_count": 2 }
    ]
  }
}
```

### GET `/twin/business-mirror`
经营镜像(7/30 日趋势)。

**响应**:含 `sales_7d` 数组,每项 `{date, revenue, cost, profit}`。

### GET `/twin/risk-mirror`
风险镜像(六维度评分)。

**响应**:
```json
{
  "code": 0,
  "data": {
    "inventory_risk": 65,
    "weather_risk": 45,
    "waste_risk": 70,
    "capital_risk": 30,
    "category_concentration_risk": 40,
    "customer_flow_risk": 25,
    "overall_risk": 46
  }
}
```

---

## 八、经验云模块 `/cloud`

匿名跨商户知识聚合(隐私保护,最小样本数 3)。

### GET `/cloud/weather-rules`
天气对销量的影响规则(基于全平台数据相关性)。

### GET `/cloud/benchmarks`
品类经营基准(同行平均周转率/损耗率,需≥3商户样本)。

**查询参数**:`category_group`(string?)

### GET `/cloud/top-products`
本周热卖品类 Top N。

---

## 九、行为学习模块 `/behavior`

### GET `/behavior/profile`
商户行为画像(保守/平衡/激进三型)。

**查询参数**:`merchant_id`(UUID)

**响应**:
```json
{
  "code": 0,
  "data": {
    "purchase_style": "balanced",
    "style_label": "平衡型",
    "adoption_rate": 0.65,
    "avg_overbuy_ratio": 0.12,
    "total_recommendations": 40,
    "adopted_count": 26
  }
}
```

`purchase_style` 取值:`conservative` / `balanced` / `aggressive`

### POST `/behavior/feedback`
提交建议采纳反馈(用于行为学习)。

**请求**:
```json
{
  "recommendation_id": "uuid",
  "was_adopted": true,
  "actual_qty": 20
}
```

---

## 十、健康检查

### GET `/api/v1/health`
服务健康检查(无需认证)。

**响应**:
```json
{ "code": 0, "message": "ok", "data": { "version": "0.1.0" } }
```

---

## 附录:小程序请求约定

小程序 `app.js` 的 `request()` 封装:
- 自动拼接 Base URL + 路径
- 自动展开 `{code, data}` 信封,Promise resolve 时直接返回 `data`
- 默认商户 ID 通过 `getMerchantId()` 获取
