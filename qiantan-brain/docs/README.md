# 千摊智脑 · 文档中心

> 面向菜市场小微商户的 AI 多模态智能经营辅助系统。
>
> 本索引将项目所有文档按"快速理解 → 项目文档 → 技术设计 → 工程规范 → 成果报告"五层组织，供不同读者按需阅读。

---

## 🚀 快速了解（先看这些）

| 文档 | 内容 | 读者 |
|---|---|---|
| [系统架构总览](architecture.md) | 总体架构图、数据流、分层说明、技术栈 | **所有人首选** |
| [演示脚本](demo-script.md) | 演示流程与话术 | 答辩 / 路演 |
| [项目根 README](../README.md) | 项目一句话介绍、快速启动 | 仓库访客 |

---

## 📋 项目文档（大创 / 立项 / 调研）

| 文档 | 内容 | 状态 |
|---|---|---|
| [大创立项申请书](大创立项申请书.md) | 创新训练立项全套材料（研究背景/创新点/技术路线/进度/预算） | ✅ 骨架完成 |
| [用户调研问卷](用户调研问卷.md) | 22 题问卷模板 + 数据分析指引 | 📋 模板就绪，待发放回收 |
| [每日报告推送 PRD](prd-daily-report-push.md) | 功能产品需求文档 | ✅ |
| [每日报告 PRD 总览](overview-daily-report-prd.md) | 功能产品需求文档 | ✅ |

---

## 🏗️ 技术设计（开发者深入）

| 文档 | 内容 |
|---|---|
| [API 规范](api-spec.md) | 后端 REST 接口定义 |
| [数据库设计](db-schema.md) | 数据模型与表结构 |
| [算法设计](algorithm-design.md) | 核心算法原理 |
| [ASR 集成](asr-integration.md) | 讯飞语音识别接入与方言适配 |
| [模型训练](model-training.md) | YOLO 商品识别训练流程 |
| [隐私设计](privacy-design.md) | 差分隐私经验云设计 |

---

## 🔧 工程规范（团队协作）

| 文档 | 内容 |
|---|---|
| [前端代码规范](frontend-code-standards.md) | 小程序开发规范 |
| [部署指南](deployment-guide.md) | 环境部署与配置 |
| [硬件指南](hardware-guide.md) | 智能秤/价目屏等硬件接入 |
| [全面升级实施规范](千摊智脑-全面升级实施规范与Codex提示词.md) | 项目演进规划 |

---

## 📊 成果报告（验证与质量）

| 文档 | 内容 |
|---|---|
| [全面体检报告 2026-07-26](full-project-audit-2026-07-26.md) | 12 智能体并行审计 + 对抗复核，549 测试全绿 |
| [参赛提升方案 2026-07-27](competition-readiness-2026-07-27.md) | 大创/计设/iCAN 三赛差距分析与行动表 |
| [测试报告](test-report.md) | 系统测试结果（549 passed） |
| [实验报告](experiment-report.md) | 算法与性能实验（状态：待实地采集） |
| [代码评审记录](code-review-2026-07-12.md) | 历次代码评审 |
| [用户手册](user-manual.md) | 终端用户使用说明 |
| [摊主故事包](摊主故事包.md) | 答辩叙事素材（⚠️ 合成人物，非真实用户证据） |
| [支付对账说明](payment-reconciliation.md) | 微信/支付宝渠道账单对账 |
| [GitHub 对标学习报告](github-learning-report.md) | 三轮开源项目对标分析 |

---

## 📁 代码模块速查

| 模块 | 路径 | 说明 |
|---|---|---|
| 小程序前端 | [`miniprogram/`](../miniprogram/) | 24 页面，微信原生 |
| 后端服务 | [`backend/app/`](../backend/app/) | FastAPI，30 路由模块，250+ 端点 |
| 管理后台 | [`backend/admin-web/`](../backend/admin-web/) | React + Ant Design |
| 边缘端 | [`edge/`](../edge/) | 视觉推理 + 称重 + OTA |
| ML 训练 | [`ml/`](../ml/) | YOLO / Prophet / 数据集 |
| 数据集 | [`datasets/`](../datasets/) | 600 张合成占位图（非真实照片，详见 `datasets/products/README.md`） |
| 迁移脚本 | [`backend/migrations/`](../backend/migrations/) | Alembic 数据库迁移（22 个版本） |
| 后端测试 | [`backend/tests/`](../backend/tests/) | 44 个测试文件，549 个用例全绿 |

---

## 🎯 按角色推荐阅读路径

### 评委 / 指导老师（10 分钟）
1. [系统架构总览](architecture.md) → 2. [大创立项申请书](大创立项申请书.md) → 3. [演示脚本](demo-script.md)

### 新加入的开发者（30 分钟）
1. [系统架构总览](architecture.md) → 2. [API 规范](api-spec.md) → 3. [数据库设计](db-schema.md) → 4. [部署指南](deployment-guide.md)

### 答辩准备（参考）
1. [演示脚本](demo-script.md) → 2. [系统架构总览](architecture.md) → 3. [实验报告](experiment-report.md) → 4. [测试报告](test-report.md)

---

## 📝 文档维护约定

- 新增文档请同步登记到本索引对应分类下
- 文档命名使用中文短句或 kebab-case 英文
- 技术文档建议附代码位置引用（`文件:行号`），便于交叉验证
- 过时文档及时标注或归档，避免误导
