# 🔍 GitHub 对标项目深度学习报告

> 生成日期: 2026-07-14 | 研究对象: 10+ 个 GitHub 开源项目
> 目的: 学习业界最佳实践，完善千摊智脑

---

## 一、已克隆并逐行分析的项目

### 1.1 FreshStock AI — 生鲜库存 AI 优化系统

**仓库**: [roshnrf/FreshStock-AI](https://github.com/roshnrf/FreshStock-AI---Smart-Inventory-Management-System)
**技术栈**: Python + scikit-learn (Random Forest) + Pandas + Chart.js

#### 核心代码分析

```python
class FreshStockAI:
    def __init__(self):
        self.model = None
        self.label_encoders = {}
        self.data = None
```

**特征工程 Pipeline**（12 个特征）:
```python
feature_cols = [
    'price',                          # 价格
    'day_of_week', 'is_weekend',      # 日历特征
    'month', 'day_of_year',           # 季节性特征
    'product_encoded', 'weather_encoded',  # 类别编码
    'demand_lag_1', 'demand_lag_2',   # 滞后特征 (1/2/3/7天前)
    'demand_lag_3', 'demand_lag_7',
    'demand_rolling_7',               # 7天滚动均值
    'demand_rolling_30'               # 30天滚动均值
]
```

**模型训练**:
```python
self.model = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)
self.model.fit(X_train, y_train)
# 评估: MAE, RMSE, MAPE
mape = np.mean(np.abs((y_test - y_pred) / y_test)) * 100
# 准确率: 87.3%, MAPE: 12.7%
```

**关键公式 — 推荐补货量**:
```python
recommended_reorder = int(total_predicted * 1.1)  # 10% 安全库存
```

**业务洞察自动生成**:
```python
# 周末 boost 分析
weekend_boost = (self.data[self.data['is_weekend']]['revenue'].mean() /
                 self.data[~self.data['is_weekend']]['revenue'].mean() - 1) * 100

# 需求变化检测 (最近7天 vs 历史平均)
for product in recent_demand.index:
    change = (recent - historical) / historical * 100
    if abs(change) > 20:
        trend = "↗️ INCREASING" if change > 0 else "↘️ DECREASING"
```

**借鉴要点**:
| 千摊现有 | FreshStock 做法 | 改进方向 |
|---------|----------------|---------|
| Prophet 预测 | Random Forest (87.3%准确率) | 增加 RF/XGBoost 作为备选模型，对比评估 |
| 单一 advisor | 自动业务洞察生成 | 增加趋势变化检测（7天 vs 30天均值对比） |
| 语音输入 | 12维特征工程 | 补充 lag/rolling/季节性特征 |
| 无天气因子 | weather_effect 直接参与预测 | 天气从"展示"升级为"预测因子" |
| 无安全库存 | 10% safety stock | 增加可配置的安全库存比例 |

---

### 1.2 Grocery Tracking Agent — Multi-Agent 架构

**仓库**: [Abby263/grocery-tracking-agent](https://github.com/Abby263/grocery-tracking-agent)
**技术栈**: CrewAI + Google Gemini + StillTasty.com + America's Test Kitchen

#### 5 Agent 架构

```
Receipt Image (照片)
    │
    ▼
┌─────────────────────────┐
│ 1. Receipt Interpreter   │  ← Gemini Vision: 图片→结构化数据
│    收据图片解析器         │
└───────────┬─────────────┘
            │ JSON {items, date}
            ▼
┌─────────────────────────┐
│ 2. Expiry Estimator      │  ← WebsiteSearchTool(stilltasty.com)
│    保质期估算器           │     查询每种食材的冷藏保存天数
└───────────┬─────────────┘
            │ JSON + expiration_date
            ▼
┌─────────────────────────┐
│ 3. Grocery Tracker       │  ← 用户输入消耗量 → 更新库存
│    库存追踪器             │     human_input=True
└───────────┬─────────────┘
            │ 更新后的库存 JSON
            ▼
┌─────────────────────────┐
│ 4. Recipe Recommender    │  ← WebsiteSearchTool(americastestkitchen)
│    菜谱推荐器             │     优先使用快过期的食材
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│ 5. Expense Tracker       │  ← 价格趋势分析 + 预算优化
│    消费分析师             │     分类汇总 + 环比/同比
└─────────────────────────┘
```

#### CrewAI 编排代码

```python
# Agent 定义
receipt_interpreter_agent = Agent(
    role="Receipt Markdown Interpreter",
    goal="Accurately extract items, counts, weights from receipt markdown...",
    allow_delegation=False,
    verbose=True,
    llm=gemini_model       # gemini-1.5-flash
)

# 带 Web 搜索工具的 Agent
expiration_date_search_agent = Agent(
    role="Expiration Date Estimation Specialist",
    goal="Estimate expiration dates using online sources...",
    tools=[WebsiteSearchTool(website='https://www.stilltasty.com/')],
    llm=gemini_model
)

# 带人工输入的任务
grocery_tracking_task = Task(
    agent=grocery_tracker_agent,
    description="Update inventory based on user consumption input...",
    context=[expiration_date_search_task],   # 依赖上游任务
    human_input=True,                         # 需要人工确认
    output_file="data/grocery_tracker.json"
)

# 编排执行
crew = Crew(
    agents=[receipt_interpreter_agent, expiration_date_search_agent,
            grocery_tracker_agent, rest_grocery_recipe_agent,
            expense_tracking_agent],
    tasks=[read_receipt_task, expiration_date_search_task,
           grocery_tracking_task, recipe_recommendation_task,
           expense_tracking_task],
    verbose=True
)
result = crew.kickoff()
```

**借鉴要点**:
| 千摊现有 | GTA 做法 | 改进方向 |
|---------|---------|---------|
| 单一 advisor 服务 | 5 个独立 Agent | 拆分 advisor → 预测Agent + 库存Agent + 风险Agent + 定价Agent |
| 规则引擎语音解析 | Gemini Vision 图片识别 | 增加拍照记账能力（收据/商品照片→自动录入） |
| 无保质期联网查询 | StillTasty.com 联网查询 | 增加食材保质期知识库（联网或本地规则库） |
| 无菜谱/搭配推荐 | America's Test Kitchen | 对生鲜摊主不太适用，但思路可迁移为"搭配销售建议" |
| 无消费趋势分析 | Expense Tracker Agent | 增加品类消费趋势/价格波动分析 |

---

### 1.3 ForecastIQ — FastAPI 需求预测平台（技术栈最接近！）

**仓库**: [Tushar0326/ForecastIQ](https://github.com/Tushar0326/ForecastIQ-AI-Demand-Forecasting-Supply-Chain-Planning-Platform)
**技术栈**: FastAPI + XGBoost + Prophet + Streamlit + Docker

#### 架构流程

```
Historical Sales Data
        ↓
Time Series Feature Engineering (lag, rolling, calendar)
        ↓
Forecasting Models (Naive → Prophet → Random Forest → XGBoost)
        ↓
Demand Prediction (daily_forecast)
        ↓
Inventory Optimization (ROP & Safety Stock)
        ↓
FastAPI Service (+ Pydantic Schema)
        ↓
Streamlit Planning Dashboard
```

#### FastAPI 端点实现

```python
from fastapi import FastAPI
from pydantic import BaseModel
import joblib, numpy as np

app = FastAPI(title="ForecastIQ API")

class ForecastInput(BaseModel):
    store: int
    item: int
    horizon_days: int = 30

# 全局常量
LEAD_TIME_DAYS = 7        # 提前期（从下单到到货的天数）
SERVICE_LEVEL_Z = 1.65    # 95% 服务水平对应的 Z 值

@app.post("/forecast")
def forecast(payload: ForecastInput):
    # 1. 查询该 store+item 的历史数据
    sub = df[(df["store"] == payload.store) & (df["item"] == payload.item)]

    # 2. 预测日需求
    latest = sub.tail(1)
    X = latest[FEATURES]
    daily_forecast = float(model.predict(X)[0])

    # 3. 计算安全库存
    avg = sub["sales"].mean()
    std = sub["sales"].std()
    safety_stock = SERVICE_LEVEL_Z * std * np.sqrt(LEAD_TIME_DAYS)
    #              ↑              ↑     ↑
    #              Z值          需求标准差  √提前期

    # 4. 计算再订货点
    reorder_point = avg * LEAD_TIME_DAYS + safety_stock
    #                ↑                    ↑
    #            提前期内的期望需求      安全库存

    return {
        "daily_forecast": round(daily_forecast, 2),
        "horizon_days": payload.horizon_days,
        "expected_demand": round(daily_forecast * payload.horizon_days, 2),
        "reorder_point": round(reorder_point, 2),
        "safety_stock": round(safety_stock, 2)
    }
```

#### Docker 部署

```dockerfile
FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 10000
CMD ["python","-m","uvicorn","app.main:app","--host","0.0.0.0","--port","10000"]
```

#### Streamlit 仪表盘

```python
import streamlit as st
import requests

st.set_page_config(page_title="ForecastIQ", layout="centered")
st.title("📦 ForecastIQ — Demand & Inventory Planner")

store = st.number_input("Store ID", min_value=1, step=1)
item = st.number_input("Item ID", min_value=1, step=1)
horizon = st.slider("Forecast Horizon (days)", 7, 90, 30)

if st.button("Run Forecast"):
    payload = {"store": store, "item": item, "horizon_days": horizon}
    res = requests.post("http://127.0.0.1:8000/forecast", json=payload).json()
    st.metric("Daily Demand Forecast", res["daily_forecast"])
    st.metric("Expected Demand", res["expected_demand"])
    st.metric("Reorder Point", res["reorder_point"])
    st.metric("Safety Stock", res["safety_stock"])
```

**借鉴要点**:
| 千摊现有 | ForecastIQ 做法 | 改进方向 |
|---------|----------------|---------|
| Prophet 预测 | Prophet + XGBoost 多模型对比 | 增加 XGBoost 作为备选，自动选最优模型 |
| 无安全库存公式 | `SS = Z × σ × √LT` | 这是最重要的公式！直接应用于进货建议 |
| 无再订货点 | `ROP = avg×LT + SS` | 自动提醒何时需要进货 |
| 无 Streamlit 仪表盘 | Streamlit 集成 | 为管理员提供独立的 ML 仪表盘 |
| FastAPI + Pydantic | 完全一致的架构 | 可直接复用 schema 和端点设计模式 |

---

## 二、核心数学公式汇总（可直接复用）

### 2.1 安全库存 (Safety Stock)

```
SS = Z × σ_D × √LT

其中:
  Z    = 服务水平对应的标准正态分位数
  σ_D  = 日需求标准差
  LT   = 提前期（天）

Z 值对照表:
  90% → 1.28
  95% → 1.65  ← ForecastIQ 使用
  97.5% → 1.96
  99% → 2.33
```

### 2.2 再订货点 (Reorder Point)

```
ROP = D̄ × LT + SS

其中:
  D̄ = 日均需求
  LT = 提前期
  SS = 安全库存
```

### 2.3 推荐补货量

```
Q = F × H + SS - I_current

其中:
  F    = 预测日需求
  H    = 补货周期（天）
  SS   = 安全库存
  I_current = 当前库存
```

### 2.4 经济订货批量 (EOQ) — 适用于非生鲜品类

```
EOQ = √(2 × D × S / H)

其中:
  D = 年需求量
  S = 每次订货成本
  H = 单位持有成本
```

### 2.5 生鲜损耗率模型

```
W = Q × r(T)

其中:
  Q    = 初始库存量
  r(T) = 在温度 T 下的损耗率函数
         r(T) ∝ e^(kT)   (Arrhenius 方程)

不同品类的衰减曲线:
  叶菜类: t½ = 48-72h  @ 25°C
  根茎类: t½ = 7-14d   @ 25°C
  水果类: t½ = 3-7d    @ 25°C
  肉类:   t½ = 24-48h  @ 4°C
  豆制品: t½ = 12-24h  @ 25°C
```

---

## 三、FunASR — 替代 iFlyTek 的本地 ASR 方案

**仓库**: [modelscope/FunASR](https://github.com/modelscope/FunASR)
**Docker 方案**: [neosun100/fun-asr-docker](https://github.com/neosun100/fun-asr-docker)

### 一键部署

```bash
# GPU 版本 (需要 NVIDIA GPU, 4GB+ VRAM)
docker run -d --name fun-asr --gpus '"device=0"' -p 8189:8189 neosun/fun-asr:latest

# CPU 版本
docker run -d --name fun-asr -p 8189:8189 neosun/fun-asr:latest
```

### OpenAI 兼容 API (可直接替换 iFlyTek)

```bash
# 同步转写
curl -X POST http://localhost:8189/v1/audio/transcriptions \
  -F "file=@audio.wav" \
  -F "language=zh" \
  -F "hotwords=白菜,土豆,猪肉" \
  -F "itn=true"

# 响应
{
  "text": "今天进了白菜五十斤三毛钱一斤",
  "duration": 0.771,
  "audio_duration": 5.62
}
```

### WebSocket 实时流 (适合按住说话场景)

```
ws://localhost:8189/ws/transcribe
→ 发送: {"action": "config", "language": "zh"}
→ 流式发送: 音频二进制块
← 接收: {"type": "final", "text": "转写结果...", "time": 1.23}
```

### 优势对比

| 维度 | iFlyTek (当前) | FunASR (推荐) |
|------|---------------|---------------|
| 成本 | 按调用量收费 | 免费 (自部署) |
| 隐私 | 数据上传第三方 | 完全本地 |
| 方言 | 有限支持 | 7大方言+26种口音 |
| 延迟 | 网络往返 ~500ms | 本地 <100ms |
| 热词 | 支持 | 支持 (hotwords参数) |
| 部署 | 无需部署 | Docker 一行命令 |

---

## 四、针对千摊智脑的具体改进方案

### 🔴 P0 — 立即实施

#### 4.1 安全库存 + 再订货点计算

**新增文件**: `backend/app/services/inventory_optimizer.py`

```python
"""库存优化引擎 — 基于 ForecastIQ 的公式"""
import numpy as np
from dataclasses import dataclass

# 服务水平 Z 值表
SERVICE_LEVEL_Z = {
    0.90: 1.28,
    0.95: 1.65,
    0.975: 1.96,
    0.99: 2.33,
}

@dataclass
class ReorderRecommendation:
    product_name: str
    daily_demand_forecast: float
    safety_stock: float
    reorder_point: float
    recommended_order_qty: float
    current_inventory: float
    days_until_stockout: float
    urgency: str  # "urgent" | "soon" | "ok"

class InventoryOptimizer:
    """库存优化计算引擎"""

    def __init__(self, service_level: float = 0.95, lead_time_days: int = 1):
        self.z = SERVICE_LEVEL_Z.get(service_level, 1.65)
        self.lead_time = lead_time_days

    def calc_safety_stock(self, demand_std: float) -> float:
        """安全库存 = Z × σ × √LT"""
        return self.z * demand_std * np.sqrt(self.lead_time)

    def calc_reorder_point(self, avg_demand: float, safety_stock: float) -> float:
        """再订货点 = D̄ × LT + SS"""
        return avg_demand * self.lead_time + safety_stock

    def calc_order_quantity(self, daily_forecast: float, horizon_days: int,
                            safety_stock: float, current_inventory: float) -> float:
        """推荐补货量 = F × H + SS - I"""
        return max(0, daily_forecast * horizon_days + safety_stock - current_inventory)

    def recommend(self, product_name: str, daily_forecast: float,
                  demand_std: float, current_inventory: float,
                  horizon_days: int = 7) -> ReorderRecommendation:
        """生成一条补货建议"""
        ss = self.calc_safety_stock(demand_std)
        rop = self.calc_reorder_point(daily_forecast, ss)
        qty = self.calc_order_quantity(daily_forecast, horizon_days, ss, current_inventory)

        # 库存耗尽天数
        days_left = current_inventory / max(daily_forecast, 0.01)
        if days_left <= self.lead_time:
            urgency = "urgent"
        elif days_left <= 3:
            urgency = "soon"
        else:
            urgency = "ok"

        return ReorderRecommendation(
            product_name=product_name,
            daily_demand_forecast=round(daily_forecast, 2),
            safety_stock=round(ss, 2),
            reorder_point=round(rop, 2),
            recommended_order_qty=round(qty, 2),
            current_inventory=current_inventory,
            days_until_stockout=round(days_left, 1),
            urgency=urgency,
        )
```

#### 4.2 多模型预测对比（增加 Random Forest + XGBoost）

**改进文件**: `backend/app/services/forecast.py`

```python
"""多模型需求预测 — 自动选择最优模型"""
from sklearn.ensemble import RandomForestRegressor
from xgboost import XGBRegressor
from prophet import Prophet
import numpy as np

class MultiModelForecaster:
    """多模型需求预测器"""

    MODELS = {
        "prophet": Prophet,
        "random_forest": RandomForestRegressor,
        "xgboost": XGBRegressor,
    }

    def __init__(self, model_type: str = "xgboost"):
        self.model_type = model_type
        self.model = None
        self.metrics = {}

    def train_all(self, X, y, X_test, y_test):
        """训练所有模型并比较"""
        results = {}
        for name, ModelClass in self.MODELS.items():
            model = ModelClass()
            model.fit(X, y)
            y_pred = model.predict(X_test)

            mae = np.mean(np.abs(y_test - y_pred))
            mape = np.mean(np.abs((y_test - y_pred) / y_test)) * 100

            results[name] = {"mae": mae, "mape": mape, "model": model}

        # 自动选择 MAPE 最低的模型
        best = min(results, key=lambda k: results[k]["mape"])
        self.model = results[best]["model"]
        self.metrics = {k: v["mape"] for k, v in results.items()}

        return best, self.metrics

    def predict(self, features) -> float:
        return float(self.model.predict(features)[0])
```

#### 4.3 增强的特征工程

**改进文件**: `backend/app/services/feature_engineering.py`

```python
"""需求预测特征工程 — 基于 FreshStock AI 的 12 维特征"""
import pandas as pd
import numpy as np

class DemandFeatureEngineer:
    """需求预测特征工程"""

    LAG_DAYS = [1, 2, 3, 7]        # 滞后天数
    ROLLING_WINDOWS = [7, 30]       # 滚动窗口

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """从原始销售数据生成特征"""
        df = df.sort_values(["product_id", "date"]).copy()

        # 1. 时间特征
        df["day_of_week"] = df["date"].dt.dayofweek
        df["is_weekend"] = df["day_of_week"] >= 5
        df["month"] = df["date"].dt.month
        df["day_of_year"] = df["date"].dt.dayofyear

        # 2. 滞后特征 (1天前、2天前...的需求量)
        for lag in self.LAG_DAYS:
            df[f"demand_lag_{lag}"] = (
                df.groupby("product_id")["quantity"].shift(lag)
            )

        # 3. 滚动统计
        for window in self.ROLLING_WINDOWS:
            df[f"demand_rolling_{window}"] = (
                df.groupby("product_id")["quantity"]
                .rolling(window, min_periods=1).mean().values
            )
            df[f"demand_std_{window}"] = (
                df.groupby("product_id")["quantity"]
                .rolling(window, min_periods=1).std().values
            )

        # 4. 趋势特征
        df["demand_momentum"] = df["demand_lag_1"] - df["demand_lag_7"]

        return df.dropna()
```

---

### 🟡 P1 — 短期规划

#### 4.4 FunASR 本地 ASR 替代 iFlyTek

**新增**: `docker-compose.yml` 增加 FunASR 服务

```yaml
services:
  funasr:
    image: neosun/fun-asr:v1.3.1
    container_name: qiantan-asr
    restart: unless-stopped
    ports:
      - "8189:8189"
    # GPU 版本:
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           device_ids: ["0"]
    #           capabilities: [gpu]
```

**改进**: `backend/app/services/asr_iflytek.py` → 新增 FunASR 后端

```python
class FunASRBackend:
    """本地 FunASR 语音识别后端"""

    def __init__(self, base_url: str = "http://funasr:8189"):
        self.base_url = base_url

    async def transcribe(self, audio_bytes: bytes, hotwords: list[str] = None) -> str:
        """OpenAI 兼容 API"""
        files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
        data = {
            "language": "zh",
            "itn": "true",
            "hotwords": ",".join(hotwords or []),
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/v1/audio/transcriptions",
                files=files, data=data
            )
            return resp.json()["text"]

    async def transcribe_streaming(self, audio_chunks, hotwords=None):
        """WebSocket 实时流式转写"""
        # 适合"按住说话"场景
        ...
```

#### 4.5 Multi-Agent 拆分 Advisor

**改进**: `backend/app/services/advisor.py` → 拆分为多个子服务

```python
# 当前: 单一 advisor
# 改进: 4 个独立 Agent

class DemandForecastAgent:
    """需求预测 Agent — 负责预测未来 N 天需求"""
    def predict(self, product_id, days=7) -> ForecastResult: ...

class InventoryOptimizationAgent:
    """库存优化 Agent — 计算安全库存/ROP/补货量"""
    def optimize(self, forecast, current_inv) -> ReorderRecommendation: ...

class RiskWarningAgent:
    """风险预警 Agent — 保质期/库存不足/天气风险"""
    def assess(self, inventory, weather) -> list[RiskAlert]: ...

class PricingAgent:
    """定价建议 Agent — 基于库存/保质期的动态调价"""
    def suggest_price(self, product, inventory_level, days_left) -> PriceSuggestion: ...

class AdvisorOrchestrator:
    """编排器 — 协调 4 个 Agent，生成综合建议"""
    def __init__(self):
        self.forecast = DemandForecastAgent()
        self.inventory = InventoryOptimizationAgent()
        self.risk = RiskWarningAgent()
        self.pricing = PricingAgent()

    async def advise(self, merchant_id: UUID) -> Advice:
        forecast = await self.forecast.predict(...)
        reorder = self.inventory.optimize(forecast, ...)
        risks = self.risk.assess(...)
        prices = self.pricing.suggest_price(...)

        return Advice(
            forecast=forecast,
            reorder=reorder,
            risks=risks,
            pricing=prices,
        )
```

---

### 🟢 P2 — 长期优化

#### 4.6 Streamlit ML 仪表盘

```python
# 新增: backend/dashboard/app.py
# 为管理员提供一个独立的 ML 数据分析仪表盘
# 展示: 模型准确率趋势、特征重要性、预测 vs 实际对比
```

#### 4.7 生鲜损耗率预测模型

```python
class SpoilagePredictor:
    """基于 Arrhenius 方程的生鲜损耗预测"""

    # 不同品类的半衰期 (25°C)
    HALF_LIFE = {
        "叶菜类": 60,     # 小时
        "根茎类": 168,
        "水果类": 96,
        "肉类": 24,
        "豆制品": 18,
    }

    def predict_remaining(self, product_category: str,
                          hours_since_purchase: float,
                          temperature: float) -> float:
        """预测剩余可售比例"""
        t_half = self.HALF_LIFE.get(product_category, 72)
        # 温度修正 (Q10 = 2, 即每升高10°C, 速率翻倍)
        temp_factor = 2 ** ((temperature - 25) / 10)
        adjusted_half = t_half / temp_factor

        # 指数衰减模型
        k = np.log(2) / adjusted_half
        remaining = np.exp(-k * hours_since_purchase)
        return max(0, remaining)
```

---

## 五、实施优先级路线图

```
Week 1-2 (P0): 安全库存 + 再订货点
├── [Day 1-2] 实现 inventory_optimizer.py
├── [Day 3-4] 集成到 advisor 服务
├── [Day 5-6] 在进货建议中展示安全库存/ROP
└── [Day 7]   测试 + 小程序 UI 更新

Week 3-4 (P0): 多模型预测
├── [Day 1-2] 实现 MultiModelForecaster
├── [Day 3-4] 训练 XGBoost + Random Forest
├── [Day 5-6] 实现自动模型选择
└── [Day 7]   评估 + 对比报告

Week 5-6 (P1): FunASR 本地 ASR
├── [Day 1-2] Docker 部署 FunASR
├── [Day 3-4] 实现 FunASRBackend
├── [Day 5]   切换 iFlyTek → FunASR
└── [Day 6-7] 热词优化 + 性能测试

Week 7-8 (P1): Multi-Agent 拆分
├── [Day 1-2] 拆分 4 个 Agent
├── [Day 3-4] 实现 AdvisorOrchestrator
├── [Day 5-6] 集成测试
└── [Day 7]   代码审查 + 上线

Week 9-12 (P2): 长期优化
├── Streamlit ML 仪表盘
├── 生鲜损耗率预测
├── E2E 测试
└── CI/CD Pipeline
```

---

## 六、关键参考项目清单

| 项目 | Stars | 许可证 | 学习重点 |
|------|-------|--------|---------|
| [FreshStock AI](https://github.com/roshnrf/FreshStock-AI---Smart-Inventory-Management-System) | ⭐ | MIT | RF预测+特征工程+业务洞察 |
| [Grocery Tracking Agent](https://github.com/Abby263/grocery-tracking-agent) | ⭐ | MIT | CrewAI多Agent架构 |
| [ForecastIQ](https://github.com/Tushar0326/ForecastIQ-AI-Demand-Forecasting-Supply-Chain-Planning-Platform) | ⭐ | - | FastAPI+XGBoost+安全库存 |
| [FunASR](https://github.com/modelscope/FunASR) | 7k+ | MIT | 中文ASR+Docker部署 |
| [Smart Grocery Assistant](https://github.com/DATUMBRIGHT/SMART_GROCERY_SHOPING-ASSISTANT) | ⭐ | - | 收据图片AI识别 |
| [SaaS Template](https://github.com/dim-pan/the-saas-template) | ⭐ | MIT | FastAPI多租户+Stripe |
| [inventorize](https://github.com/haythamomar/inventorize) | ⭐ | - | EOQ/安全库存Python库 |
| [supply-chain-optimization](https://github.com/AndreDavis-SCM/supply-chain-optimization) | ⭐ | - | EOQ+安全库存+Tableau |

---

> 📝 **总结**: 千摊智脑的架构已经很完整。最有价值的改进是：
> 1. **安全库存公式** (从 ForecastIQ) — 让进货建议从"凭经验"变成"数据驱动"
> 2. **多模型预测** (从 FreshStock AI) — 从单一 Prophet 升级为多模型对比
> 3. **本地 ASR** (从 FunASR) — 零成本、更隐私、支持方言
> 4. **Multi-Agent 拆分** (从 Grocery Tracking Agent) — 更可维护的 AI 建议系统

---

## 七、第三轮学习 (2026-07-14) — 4 个新方向

### 7.1 动态定价引擎 (Dynamic Pricing)

**参考项目**:
- [normanrz/dynamic-prices](https://github.com/normanrz/dynamic-prices) — 生鲜动态定价仿真系统
- [amattas/retail-demo #250](https://github.com/amattas/retail-demo/pull/250) — 基于规则的降价引擎
- [shrrl/Dynamic_Noshinom](https://github.com/shrryl/Dynamic_Noshinom) — 电子价签+Firebase实时价格
- 腾讯云 DQN 动态定价 — 200行深度强化学习实战

**已实现**: [`app/services/dynamic_pricing.py`](../backend/app/services/dynamic_pricing.py) (~400行)

核心能力:
- **4种定价策略**: AGE_BASED / INVENTORY_BASED / COMBINED / CLEARANCE
- **Q10 温度修正**: `temp_correction()` — 温度每升高10°C, 变质速率翻倍
- **质量衰减模型**: `quality_factor(t) = exp(-λt)` — 指数衰减
- **需求弹性**: 不同品类价格弹性系数 (蔬菜1.8/水产0.8/干货0.3)
- **降价阶梯**: 5级阶梯 (全价→9折→8折→7折→5折出清)
- **底价约束**: 不低于 `max(成本×50%, 成本/(1-最低毛利率))`
- **批量建议 + What-If 仿真**: 模拟9种折扣率的利润/损耗/收入
- **3种档位**: CONSERVATIVE/BALANCED/AGGRESSIVE

关键公式:
```
quality(t) = exp(-ln(2) * t / half_life)
half_life = shelf_life / (3 * Q10_correction)
demand_uplift = 1 + elasticity * discount_pct
floor_price = max(unit_cost * 0.5, unit_cost / (1 - min_margin))
```

### 7.2 供应商绩效评分 (Supplier Scoring)

**参考项目**:
- [ghazalna/Supplier-Performance-Evaluation-Clustering](https://github.com/ghazalna/Supplier-Performance-Evaluation-Clustering) — K-Means供应商分级
- vendor_leadtime_scorecard (Odoo) — 0-100评分 + A-F等级
- [asgard-ai-platform/skills](https://github.com/asgard-ai-platform/skills) — QCDS加权打分框架
- Dual-Band Lead Time Predictor (HuggingFace) — 双模型: 均值 + 95分位

**已实现**: [`app/services/supplier_scorer.py`](../backend/app/services/supplier_scorer.py) (~450行)

核心能力:
- **5维度加权**: 质量25% + 交期25% + 价格20% + 稳定性15% + 服务15%
- **提前期预测** (Dual-Band): Model A (期望均值) + Model B (安全缓冲 = 2σ)
- **综合评分卡**: 0-100分 + A/B/C/D/F 等级 + LOW/MEDIUM/HIGH/CRITICAL 风险
- **自动分析**: 强弱项识别 + 维度级建议 + 数据可信度评估
- **批量对比**: 自动百分位排名 + 各维度最佳供应商 + 完整排行榜
- **特殊信号**: 缺斤率>20% → CRITICAL, 准时率<30% → HIGH

评分体系:
```
质量(25%): 合格率50分 - 缺斤扣分(20) - 破损扣分(15) - 拒收扣分(15)
交期(25%): 准时率40分 + 稳定性30分 + 承诺偏差30分
价格(20%): 竞争力60分 + 波动性40分
稳定性(15%): 规模30分 + 一致性40分 + 长期性30分
服务(15%): 响应速度40分 + 灵活度30分 + 沟通30分
```

### 7.3 库存异常检测 (Anomaly Detection)

**参考项目**:
- [yzhao062/pyod](https://github.com/yzhao062/pyod) — 60+检测器, 统一API, 集成学习
- [SeldonIO/alibi-detect](https://github.com/SeldonIO/alibi-detect) — Prophet/Spectral Residual/Seq2Seq
- [linkedin/luminol](https://github.com/linkedin/luminol) — 轻量级时序异常检测
- [datamllab/pyodds](https://github.com/datamllab/pyodds) — 端到端异常检测系统

**已实现**: [`app/services/anomaly_detector.py`](../backend/app/services/anomaly_detector.py) (~450行)

核心能力:
- **7个检测器**: Z-Score / Modified Z-Score (MAD) / IQR / Moving Average / Seasonal Decomposition / Zero Sales / Data Error
- **集成投票**: 至少N个检测器共识才报警 (可配置)
- **8种异常类型**: SPIKE/DROP/TREND_BREAK/STOCKOUT_RISK/OVERSTOCK/ZERO_SALES/DATA_ERROR/PATTERN_SHIFT
- **4级严重度**: LOW/MEDIUM/HIGH/CRITICAL
- **库存专项**: `check_stockout_risk()` + `check_overstock()`
- **完整报告**: 分类统计 + 严重度统计 + AI建议

检测器对比:
| 检测器 | 方法 | 优点 | 适用场景 |
|--------|------|------|---------|
| Z-Score | (x-μ)/σ | 简单快速 | 正态分布数据 |
| Modified Z | 0.6745(x-median)/MAD | 对离群值鲁棒 | 有历史异常的数据 |
| IQR | Q1-1.5IQR / Q3+1.5IQR | 非参数 | 偏态分布 |
| Moving Avg | 当前vs滑动均值 | 捕捉短期偏离 | 趋势变化 |
| Seasonal | 周期分解+残差 | 考虑周模式 | 有季节性的数据 |

### 7.4 食品安全合规 (Food Safety / HACCP-lite)

**参考项目**:
- [zavora-ai/mcp-qms](https://github.com/zavora-ai/mcp-qms) — 完整QMS: HACCP + NCR + CAPA (31个MCP工具)
- [zavora-ai/mcp-lims](https://github.com/zavora-ai/mcp-lims) — LIMS: 样本链 + OOS检测 + ALCOA+审计追踪 (27个MCP工具)

**已实现**: [`app/services/food_safety.py`](../backend/app/services/food_safety.py) (~450行)

核心能力:
- **6个CCP** (关键控制点):
  - CCP-1: 冷藏温度 (肉类<4°C, 蔬菜<8°C)
  - CCP-2: 热柜温度 (熟食>60°C)
  - CCP-3: 加工时间 (室温<4h, 高温<2h)
  - CCP-4: 清洁消毒 (每日收摊后)
  - CCP-5: 来源可追溯 (供应商证照齐全)
  - CCP-6: 交叉污染防控 (生熟分离)
- **自动NCR生成**: CCP超标 → 自动创建不合格报告 + 纠正措施
- **保质期追踪**: 进货时间 + 品类货架期 + 过期预警 + 质量因子
- **综合评分卡**: 5维度评分 (温度25 + 卫生25 + 来源20 + 保质期15 + 记录15)
- **每日检查清单**: 按品类自动生成差异化检查项
- **温度阈值**: 按品类不同 (肉类<4°C, 蔬菜<8°C, 水果<10°C)

NCR严重度自动判定:
```
CCP-1 冷柜温度: >10°C → CRITICAL, >7°C → MAJOR, >4°C → MINOR
CCP-2 热柜温度: <45°C → CRITICAL, <55°C → MAJOR, <60°C → MINOR
CCP-3 加工时间: >6h → CRITICAL, >4h → MAJOR
MAJOR/CRITICAL → 需CAPA (纠正预防措施)
```

---

## 八、第三轮新增文件清单

| 文件 | 行数 | 说明 |
|------|------|------|
| [`services/dynamic_pricing.py`](../backend/app/services/dynamic_pricing.py) | ~400 | 动态定价引擎 (4策略+仿真) |
| [`services/supplier_scorer.py`](../backend/app/services/supplier_scorer.py) | ~450 | 供应商5维评分+提前期预测 |
| [`services/anomaly_detector.py`](../backend/app/services/anomaly_detector.py) | ~450 | 7检测器集成+库存专项 |
| [`services/food_safety.py`](../backend/app/services/food_safety.py) | ~450 | 6CCP HACCP+自动NCR |
| [`tests/test_dynamic_pricing.py`](../backend/tests/test_dynamic_pricing.py) | ~250 | 30个测试用例 |
| [`tests/test_supplier_scorer.py`](../backend/tests/test_supplier_scorer.py) | ~250 | 30个测试用例 |
| [`tests/test_anomaly_detector.py`](../backend/tests/test_anomaly_detector.py) | ~250 | 36个测试用例 |
| [`tests/test_food_safety.py`](../backend/tests/test_food_safety.py) | ~250 | 33个测试用例 |

**测试结果: 547 passed ✅** (413 + 134 new)

---

## 九、第三轮参考项目清单

| 项目 | Stars | 许可证 | 学习重点 |
|------|-------|--------|---------|
| [normanrz/dynamic-prices](https://github.com/normanrz/dynamic-prices) | ~65 | MIT | 生鲜动态定价+需求弹性+仿真 |
| [amattas/retail-demo](https://github.com/amattas/retail-demo) | ⭐ | - | 三阶段降价规则引擎 |
| [Dynamic_Noshinom](https://github.com/shrryl/Dynamic_Noshinom) | ⭐ | - | Firebase实时价格+电子价签 |
| [PyOD](https://github.com/yzhao062/pyod) | 8k+ | BSD | 60+异常检测器统一框架 |
| [alibi-detect](https://github.com/SeldonIO/alibi-detect) | 2k+ | Apache-2.0 | Prophet/Spectral Residual检测 |
| [Supplier Clustering](https://github.com/ghazalna/Supplier-Performance-Evaluation-Clustering) | ⭐ | - | K-Means供应商分级 |
| [mcp-qms](https://github.com/zavora-ai/mcp-qms) | ⭐ | Apache-2.0 | HACCP+NCR+CAPA全流程 |
| [mcp-lims](https://github.com/zavora-ai/mcp-lims) | ⭐ | Apache-2.0 | OOS检测+ALCOA+审计追踪 |
| [inventory-streamlit-app](https://github.com/samirsaci/inventory-streamlit-app) | ⭐ | - | Streamlit库存仿真仪表盘 |
| [Streamlit Inventory Dashboard](https://github.com/Pragati928/Inventory-Management-Dashboard) | ⭐ | - | 实时KPI+自动补货+Plotly |

---

## 十、累计成果总览 (三轮学习)

| 维度 | Round 1 | Round 2 | Round 3 | 累计 |
|------|---------|---------|---------|------|
| 研究项目 | 10+ | 5+ | 10+ | **25+** |
| 新增代码文件 | 3 | 2 | 4 | **9** |
| 新增代码行数 | ~1000 | ~340 | ~1750 | **~3090** |
| 新增测试文件 | 1 | 1 (扩展) | 4 | **6** |
| 新增测试用例 | 30 | 11 | 134 | **175** |
| 全量测试 | 402 → | 413 → | **547** | **547 passed** |

### 千摊智脑完整能力矩阵

| 能力 | 来源 | 状态 |
|------|------|------|
| 安全库存计算 (SS = Z×σ×√LT) | ForecastIQ | ✅ |
| 再订货点预警 (ROP = D̄×LT + SS) | ForecastIQ | ✅ |
| 自动补货量 (Q = F×H + SS - I) | ForecastIQ | ✅ |
| 生鲜损耗预估 (Arrhenius + 半衰期) | FreshStock AI | ✅ |
| 12维特征工程 (lag/rolling/calendar) | FreshStock AI | ✅ |
| 报童模型 (Newsvendor Normal/Poisson) | inventorize | ✅ |
| PostgreSQL RLS (22表策略生成器) | sqlalchemy-tenants | ✅ |
| 动态定价 (4策略+Q10温度修正) | dynamic-prices | ✅ |
| 供应商5维评分 (Dual-Band提前期) | Supplier Clustering | ✅ |
| 7检测器异常检测 (集成投票) | PyOD | ✅ |
| 6CCP食品安全 (HACCP-lite+NCR) | mcp-qms | ✅ |
| Multi-Agent 拆分方案 (4 Agent) | Grocery Tracking | 📖 |
| FunASR 本地语音识别 | FunASR | 📖 |
| 冷启动 Bayesian 预测 | TSB-HB | 📖 |
| 收据 OCR (Gemini Vision) | KFinan11 | 📖 |
| Streamlit ML 仪表盘 | 多个项目 | 📖 |
