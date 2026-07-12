# 千摊智脑 算法设计文档

> 更新日期: 2026-07-11
> 设计原则: **先规则可解释,再数据驱动**

本文档描述五大核心算法,均与 [backend/app/services/](../backend/app/services/) 源码同步。

---

## 算法全景

```
用户语音 ──→ ① 语音语义解析 ──→ 结构化事件
                                      │
                                      ▼
环境数据 ──→ ② 环境增强销量估计 ←── 历史销量
                                      │
                    ┌─────────────────┤
                    ▼                 ▼
           ③ 经营建议生成    ④ 决策沙盘模拟
           (三行式可解释)    (What-if 分析)
                                      │
                                      ▼
                             ⑤ 商户行为学习
                             (采纳率→画像)
```

---

## ① 语音语义解析引擎

**源码**:[voice_parser.py](../backend/app/services/voice_parser.py)

### 1.1 处理流水线

```
ASR 文本 → 预处理 → 事件类型判定 → 字段抽取 → 缺失补全 → 置信度
```

### 1.2 预处理(三步)

**步骤 A — 去语气词**
```
移除: ["那个", "嗯", "啊", "哦", "呃", "就是", "然后", "这个"]
```

**步骤 B — 中文数字归一化**
```
"五十斤"    → "50斤"      (X十Y → X*10+Y)
"三十斤"    → "30斤"      (X十 → X*10)
"十五斤"    → "15斤"      (十Y → 10+Y)
"十斤"      → "10斤"      (十 → 10)
```

**步骤 C — 金额归一化**
```
"三毛钱"    → "0.3元"     (X毛 → X*0.1元)
"五分钱"    → "0.05元"    (X分 → X*0.01元)
"一块二毛"  → "1.2元"     (X块Y毛 → X+Y*0.1元)
"两块钱"    → "2元"       (X块 → X元)
"3元5角"    → "3.5元"     (X元Y角 → X.Y元)
```

> **关键修复**:中文数字"两"已加入金额正则字符类 `[\d一二三四五六七八九两]`,支持"两块钱一斤"。

### 1.3 事件类型判定(关键词触发)

| 类型 | 触发词 | 优先级 |
|------|--------|--------|
| `purchase` | 进了/进来/买的/买了/进货/上了/拉了/批了/采购 | 1(最高) |
| `waste` | 坏了/扔了/烂了/掉了/损耗/报废/不能卖了 | 2 |
| `sale` | 卖了/卖出/一共卖/卖了钱/收入/赚了/收成 | 3 |
| `unknown` | (无匹配) | 默认归为 purchase |

### 1.4 字段抽取(正则)

**商品名**:最长匹配优先(避免"白菜"被"白"截断),支持模糊匹配(前缀子串)。

**数量**:
```
(\d+(?:\.\d+)?)\s*(斤|公斤|千克|个|把|箱|袋|件)
```

**单价**:
```
(\d+(?:\.\d+)?)\s*元[一每]\s*(?:斤|个|把)    ← "1.5元一斤"
(\d+(?:\.\d+)?)\s*[块元]钱?[一每]\s*(?:斤|个|把)  ← "1块5一斤"
```

**总金额**:
```
(?:一共|总计|花了|总价)\s*(\d+(?:\.\d+)?)\s*[元块]  ← "一共卖了40块"
```

### 1.5 缺失补全

```
IF 有数量 AND 有总价 AND 缺单价:
    单价 = 总价 / 数量
IF 只有商品 AND 数量:
    (后续查商户最近一次该商品单价作默认)
```

### 1.6 置信度公式

```
confidence = 1.0 - 0.1 × len(missing_fields) - 0.05 × guessed_fields
clamped to [0.0, 1.0]
```

| 缺失字段数 | 推测字段数 | 置信度 | 前端行为 |
|-----------|-----------|--------|---------|
| 0 | 0 | 1.00 | 直接入库(success) |
| 0 | 1 | 0.95 | 直接入库 |
| 1 | 0 | 0.90 | 直接入库 |
| 1 | 1 | 0.85 | 需确认(confirm_needed) |
| 2 | 0 | 0.80 | 需确认 |

阈值:**confidence ≥ 0.8 → 直接入库,< 0.8 → 需用户确认**。

---

## ② 环境增强销量估计引擎

**源码**:[env_engine.py](../backend/app/services/env_engine.py)
**配置**:[env_coefficients.json](../backend/app/rules/env_coefficients.json)

### 2.1 核心公式

```
Predicted(D, P) = MA_7(P) × Temp(D, P) × Rain(D) × Holiday(D) × Weekend(D) × Trend(P)
```

### 2.2 系数定义

**移动平均 MA_7**:
```
MA_7(P) = Σ(最近7天有效日销量) / 有效天数
```
> 有效天数:排除断货日(销量=0 且库存=0 的那天不参与平均)

**温度系数 Temp**:
| 条件 | 品类 | 系数 |
|------|------|------|
| 15°C ≤ T ≤ 30°C | 全部 | 1.00 |
| T > 30°C | 西瓜/黄瓜/番茄(解暑组) | 1.20 |
| T > 30°C | 叶菜类/豆制品(易腐组) | 0.85 |
| T > 30°C | 其他 | 1.00 |
| T < 10°C | 火锅食材/肉类(暖食组) | 1.15 |
| T < 10°C | 其他 | 1.00 |

**降雨系数 Rain**:
| 降雨概率 | 系数 |
|---------|------|
| 0~30% | 1.00 |
| 30~50% | 0.90 |
| 50~70% | 0.75 |
| 70~100% | 0.60 |

**节假日系数 Holiday**:
| 节日类型 | 系数 |
|---------|------|
| 春节前3天 | 1.35 |
| 国定假日 | 1.20 |
| 假日前1天 | 1.10 |
| 假日后1天 | 0.80 |
| 平日 | 1.00 |

**周末系数 Weekend**:
| 星期 | 系数 |
|------|------|
| 周六 | 1.12 |
| 周日 | 1.15 |
| 工作日 | 1.00 |

**趋势系数 Trend**:
```
IF MA_7 < MA_30 × 0.9:  trend = 0.95  (下降趋势)
IF MA_7 > MA_30 × 1.1:  trend = 1.05  (上升趋势)
ELSE:                    trend = 1.00
```

### 2.3 边界裁剪

```
predicted = max(1.0, min(predicted, max_historical_daily × 1.3))
```
- **下限**:至少 1 斤(保证陈列需求)
- **上限**:不超过历史最大日销量 × 1.3(防极端值)

### 2.4 完整输出

```python
{
    "predicted_qty": 22.0,
    "coefficients": {
        "moving_avg_7d": 18.0,
        "temperature": 1.20,
        "rainfall": 0.90,
        "holiday": 1.00,
        "weekend": 1.15,
        "trend": 1.00
    }
}
```

---

## ③ 经营建议生成引擎

**源码**:[advisor.py](../backend/app/services/advisor.py)

### 3.1 三行式可解释格式

每条建议包含三个部分,确保商户"看得懂、信得过":

```
① 一句话建议:    "建议明日采购白菜22斤"
② 分析依据列表:  近7日平均销量 18斤 (+)
                 周末客流预计增加12% (+)
                 当前库存不足1天 (+)
③ 风险提示:      "若明日降雨概率超过50%，建议减少至15斤"
```

### 3.2 建议数量计算

```python
recommended_qty = max(0, predicted_qty - current_inventory_qty)
```

- `predicted_qty` 来自环境增强引擎
- 若 `recommended_qty = 0` → "库存充足,明日无需进货"

### 3.3 依据生成规则(6 类因子)

依据列表按影响方向标注 `+`(利好进货)/`-`(不利进货):

| 因子 | 触发条件 | 示例 |
|------|---------|------|
| 7日均销量 | 恒输出 | "18斤" (+) |
| 周末客流 | weekend ≠ 1.0 | "预计增加12%" (+) / "较周末减少12%" (-) |
| 气温影响 | temp ≠ 1.0 | "增加20%" (+) / "减少15%" (-) |
| 降雨影响 | rain < 1.0 | "预计减少25%" (-) |
| 当前库存 | days < 1 或 > 3 | "不足1天" (+) / "充足可销3天" (-) |
| 节假日 | holiday ≠ 1.0 | "增加35%" (+) |

### 3.4 风险提示生成

```python
IF rainfall_prob > 50:  warn("若降雨概率超50%，建议减少至{recommended×0.7}斤")
IF temp_high > 35:      warn("高温天气，请注意{product}保鲜")
IF predicted > max_hist × 1.2:  warn("预测值偏高，请注意控制风险")
```

### 3.5 置信度

当前规则引擎基础置信度 `0.78`,后续接入 Prophet 时按数据成熟度加权提升。

---

## ④ 决策沙盘模拟引擎

**源码**:[simulator.py](../backend/app/services/simulator.py)

### 4.1 What-if 计算流水线

```
输入(进货量/进价/售价)
    │
    ▼
Step 1: 获取基准销量 ← 环境增强引擎
    │
    ▼
Step 2: 价格弹性修正
    │
    ▼
Step 3: 损耗计算
    │
    ▼
Step 4: 净收益计算
    │
    ▼
Step 5: 基准对比 + 评语生成
```

### 4.2 价格弹性模型

```python
price_ratio = unit_price / avg_historical_price

IF price_ratio < 0.9:   # 降价促销
    sales_mult = 1 + (1 - price_ratio) × 1.5
    # 降10%价 → 销量 +15%
ELIF price_ratio > 1.1:  # 涨价
    sales_mult = max(0.7, 1 - (price_ratio - 1) × 1.0)
    # 涨10%价 → 销量 -10%,最低不低于基准70%
ELSE:
    sales_mult = 1.0

est_sales = min(est_base × sales_mult, purchase_qty)
# 实际销售不超过进货量
```

### 4.3 损耗计算

```python
# 保质期越短,剩余部分损耗概率越高
waste_prob = 0.95  if shelf_life_hours ≤ 24  # 豆腐/水产
            0.85  else                        # 其他品类

waste_qty = max(0, purchase_qty - est_sales) × waste_prob
waste_loss = waste_qty × unit_cost × 0.5
# 损耗按成本 50% 折算(残值回收)
```

### 4.4 净收益公式

```
revenue     = est_sales × unit_price
cost        = purchase_qty × unit_cost
waste_loss  = waste_qty × unit_cost × 0.5
net_profit  = revenue - cost - waste_loss
margin_rate = net_profit / cost
waste_rate  = waste_qty / purchase_qty
```

### 4.5 基准对比

基准方案 = "刚好进货 est_sales_base"(理论最优量)。对比当前方案:

```python
improvement = net_profit - baseline_profit

IF improvement > 0.5:  verdict = "有利：模拟方案优于基准方案"
IF improvement < -0.5: verdict = "不利：基准方案更优"
ELSE:                   verdict = "持平：两种方案收益接近"
```

### 4.6 智能评语

```
IF waste_rate > 0.30: "净收益X元，但损耗率30%偏高，建议控制进货量"
IF waste_rate > 0.15: "净收益X元，损耗率在可接受范围内"
ELSE:                  "净收益X元，损耗控制良好"
```

---

## ⑤ 商户行为学习引擎

**源码**:[behavior.py](../backend/app/services/behavior.py)

### 5.1 三型画像分类

基于历史采纳数据,将商户分为三类:

```python
adoption_rate = adopted_count / total_recommendations
avg_overbuy_ratio = AVG(actual_qty - recommended_qty) / AVG(recommended_qty)
```

| 画像 | 条件 | 含义 | 建议调整 |
|------|------|------|---------|
| `conservative`(保守型) | adoption_rate < 0.4 | 倾向少买 | 上调建议量 10%(防止断货) |
| `balanced`(平衡型) | 0.4 ≤ rate ≤ 0.7 | 理性采纳 | 维持原建议 |
| `aggressive`(激进型) | adoption_rate > 0.7 | 倾向多买 | 下调建议量 10%(控制损耗) |

### 5.2 个性化建议调整

```python
def personalize_recommendation(recommended_qty, profile):
    if profile == "conservative":
        return recommended_qty × 1.10  # 保守者建议多备货
    elif profile == "aggressive":
        return recommended_qty × 0.90  # 激进者建议控量
    else:
        return recommended_qty
```

### 5.3 闭环学习

```
生成建议 → 商户决策 → 记录 was_adopted + actual_deviation
    ↑                                         │
    └──── 更新画像 ← 统计采纳率 ←──────────────┘
```

---

## ⑥ 时序预测融合(Prophet + 规则引擎)

**源码**:[ml/prophet_predict.py](../ml/prophet_predict.py)

### 6.1 混合权重(按数据成熟度)

```python
w_prophet = min(0.7, data_days / 120)
w_rule    = 1 - w_prophet
blended   = w_prophet × prophet_qty + w_rule × rule_qty
```

| 数据天数 | Prophet 权重 | 规则权重 | 说明 |
|---------|-------------|---------|------|
| <30 天 | 0.25 | 0.75 | 数据不足,规则为主 |
| 60 天 | 0.50 | 0.50 | 均衡 |
| ≥120 天 | 0.70 | 0.30 | ML 为主 |

### 6.2 Prophet 回归变量

```python
model.add_regressor("temp_high")      # 温度
model.add_regressor("rainfall_prob")  # 降雨概率
model.add_regressor("is_weekend")     # 周末标记
```

加中国节假日(春节/劳动节/国庆)作为 holidays DataFrame。

---

## 算法验证方案

| 算法 | 验证方法 | 通过标准 |
|------|---------|---------|
| 语音解析 | 12 种典型句式(含方言) | 准确率 ≥ 85%(≥10/12 正确) |
| 环境增强 | 单调性测试(温度↑→解暑品销量↑) | 系数方向正确 |
| 模拟沙盘 | 同品类 3 组不同进货量 | 单调性:进更多→损耗更高 |
| 行为学习 | 模拟 30 条采纳记录 | 画像分类合理 |

详见 [backend/tests/](../backend/tests/)(21 个单元测试)。
