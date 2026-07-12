# 千摊智脑 QianTan Brain

面向小微商户的环境感知型AI经营智能体与数字孪生决策系统。

## 项目概述

千摊智脑是一个为菜市场摊位、社区夫妻店等小微商户打造的AI经营助手。通过语音记账、视觉识别、环境感知和决策模拟，帮助不会打字的老板完成经营数字化。

## 技术栈

| 模块 | 技术 |
|------|------|
| 前端 | 微信小程序原生 |
| 后端 | FastAPI + Python 3.12 |
| 数据库 | PostgreSQL 16 |
| 语音识别 | 讯飞语音识别 API |
| 商品识别 | YOLOv8-nano (ONNX Runtime) |
| 边缘硬件 | Raspberry Pi 5 (8GB) | 🚧 代码补全 / 真机待验证 |

> 图例：✅ 已完成 · 🟡 接口已接/演示模式 · 🚧 开发中

## 实现状态

| 功能 | 状态 | 说明 |
|------|------|------|
| 微信小程序 | ✅ | 首页/语音/库存/建议/沙盘/数字孪生/盘点/采购/报表 |
| FastAPI 后端 | ✅ | 异步接口、数据模型、业务服务（84 测试通过） |
| 方言语音 ASR | 🟡 | 讯飞接口已接；无凭证或显式 demoMode 时走演示模式 |
| 语音领域解析 | ✅ | 商品/数量/金额/买卖类型 + 确认/纠错/作废 |
| YOLO 商品识别 | 🟡 | 推理管线已接（ONNX Runtime + 演示降级）；权重为占位，需真实训练 |
| 天气接口 | 🟡 | 和风天气已接；默认 Key 为空走 Mock |
| 环境增强引擎 | ✅ | 温度/降雨/周末/节假日系数进需求估算 |
| 商户偏好学习 | ✅ | 建议采纳反馈 + 商户画像 + 个性化 |
| 商品生命周期 | ✅ | 批次/FIFO/临期/损耗预警 |
| 三行式建议 | ✅ | 建议/依据/风险结构完整 |
| What-if 沙盘 | ✅ | 进货量/价格/天气模拟 |
| 库存柱状图 | ✅ | 小程序自绘 Canvas |
| 库存热力图 | ✅ | 后端生命周期矩阵 + 前端热力图 |
| 经营趋势 7/30 日 | ✅ | 后端 sales_7d/sales_30d；前端可切换 |
| 客单价曲线 | ✅ | 后端 customer_price；前端 AOV 曲线 |
| 风险雷达 | ✅ | 六维均为真实计算（含资金/品类集中度） |
| 千摊经验云 | 🟡 | 阈值匿名聚合 + Laplace 差分隐私噪声；联邦学习规划中 |
| Prophet 预测 | 🟡 | 脚本 + 在线接线已完成（按数据量自动回退） |
| PostgreSQL | 🟡 | Docker 可部署（PG16）；本地默认 SQLite |
| 边缘端 | 🚧 | 摄像头/称重/离线同步代码补全；真机待验证 |
| 文档 | 🟡 | 部署/硬件/训练/ASR/答辩/测试/实验/隐私 已补 |

## 项目结构

```
qiantan-brain/
├── miniprogram/        # 微信小程序
│   ├── pages/          # 首页/记账/库存/AI参谋/设置
│   └── components/     # 通用组件
├── backend/            # FastAPI 后端
│   ├── app/
│   │   ├── models/     # SQLAlchemy 数据模型
│   │   ├── schemas/    # Pydantic 请求/响应模型
│   │   ├── routers/    # API 路由
│   │   ├── services/   # 业务逻辑层
│   │   └── rules/      # 规则配置文件
│   └── tests/
├── edge/               # 树莓派边缘端
│   ├── vision/         # 摄像头 + YOLO 推理
│   └── weighing/       # HX711 称重模块
├── scripts/            # 工具脚本
└── docs/               # 文档
```

## 快速开始

### 1. 启动后端

```bash
cd backend
docker compose up -d        # 启动 PostgreSQL + FastAPI
# 或本地开发:
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API 文档: http://localhost:8000/docs

### 2. 初始化数据

```bash
python scripts/seed_data.py > seed.sql
# 执行 seed.sql 在 PostgreSQL 中
```

### 3. 运行测试

```bash
cd backend
python -m pytest tests/ -v
```

### 4. 微信小程序

使用微信开发者工具打开 `miniprogram/` 目录。

## API 概览

| 模块 | 端点 | 说明 |
|------|------|------|
| 语音记账 | POST `/api/v1/voice/parse-text` | 文本解析（MVP） |
| 语音记账 | POST `/api/v1/voice/confirm` | 确认入库 |
| 库存 | GET `/api/v1/inventory/current` | 当前库存 |
| 经营建议 | GET `/api/v1/advice/daily` | 每日建议 |
| 沙盘模拟 | POST `/api/v1/simulate/what-if` | What-if 分析 |
| 数字孪生 | GET `/api/v1/twin/dashboard` | 仪表盘数据 |

## 开发进度

- [x] Phase 1 W1-2: 项目脚手架搭建
- [x] Phase 1 W3-4: 数据库建表 + 语音记账 API
- [x] Phase 1 W5-6: 语音规则引擎 + 小程序页面
- [x] Phase 1 W7-8: 树莓派部署代码 + 联调（真机待验证）
- [x] Phase 2: 经营建议 + 沙盘模拟 + 数字孪生 + Prophet 在线接线 + 经验云差分隐私
- [ ] Phase 3: 实地测试 + 论文 + 比赛（详见 docs/experiment-report.md、docs/demo-script.md）

## 团队

- 成员A: 算法与硬件 (YOLO、规则引擎、树莓派)
- 成员B: 全栈开发 (小程序、后端、数据库、可视化)
- 成员C: 产品与文档 (用户调研、答辩材料、商业计划书)

## License

MIT — 本项目为大学生创新创业训练计划作品
