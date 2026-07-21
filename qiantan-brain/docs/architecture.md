# 系统架构总览

> 千摊智脑——面向菜市场小微商户的 AI 多模态智能经营辅助系统。
>
> 本文档提供系统整体架构图与分层说明，供答辩演示、README、大创立项书共同引用。

---

## 一、总体架构图

```mermaid
graph TB
    subgraph U["用户层"]
        M["菜市场摊主<br/>(微信小程序)"]
        A["市场管理方<br/>(管理后台 Web)"]
    end

    subgraph F["前端层"]
        MP["微信小程序<br/>24 页面 · ES5 原生<br/>设计系统 v2.2 · 离线队列"]
        AW["管理后台 Web<br/>React + Ant Design"]
    end

    subgraph E["边缘层 (Edge)"]
        EV["视觉推理模块<br/>(YOLO ONNX)"]
        EW["称重数据采集"]
        EO["OTA · 心跳 · 日志"]
    end

    subgraph B["后端层 (FastAPI · Python 3.13)"]
        MW["中间件层<br/>JWT · 幂等 · 租户上下文<br/>请求 ID · 限流"]
        R["路由层 (34 模块 · 244 接口)<br/>voice · vision · pos · purchase<br/>inventory · twin · food_safety · ..."]
        S["服务层<br/>批次 FIFO · 往来账<br/>数字孪生 · 经验云"]
        DAO["数据访问层<br/>SQLAlchemy async"]
    end

    subgraph ML["AI / 模型层"]
        ASR["讯飞 ASR<br/>(WebSocket · 多方言)"]
        YOLO["YOLOv8 商品识别<br/>(ONNX 端侧)"]
        PRO["Prophet 销量预测"]
        NLP["自研语义解析器<br/>(语音/文字 → 结构化事件)"]
    end

    subgraph DATA["数据层"]
        PG[("PostgreSQL<br/>(生产)")]
        SL[("SQLite<br/>(开发)")]
        FS["文件存储<br/>音频 · 图片 · 导出"]
    end

    subgraph EXT["外部服务"]
        WX["微信开放平台<br/>登录 · 支付"]
        QW["和风天气<br/>天气 · 节气"]
    end

    subgraph OPS["工程基建"]
        CI["GitHub Actions CI"]
        DK["Docker Compose"]
        PM["Prometheus 监控"]
        ST["Sentry 错误追踪"]
    end

    M --> MP
    A --> AW
    MP -->|HTTPS / api/v1| MW
    AW -->|HTTPS / api/admin| MW
    EV --> EO
    EW --> EO
    EO -->|上报| MW

    MW --> R --> S --> DAO
    R -.调用.-> ASR
    R -.调用.-> NLP
    R -.调用.-> PRO
    EV -.推理.-> YOLO

    DAO --> PG
    DAO --> SL
    S --> FS

    R -.调用.-> WX
    R -.调用.-> QW

    CI -.构建.-> DK
    DK -.部署.-> B
    B -.指标.-> PM
    B -.异常.-> ST

    classDef user fill:#E8F5E9,stroke:#2E7D32
    classDef front fill:#FFF3E0,stroke:#E65100
    classDef edge fill:#F3E5F5,stroke:#6A1B9A
    classDef back fill:#E3F2FD,stroke:#1565C0
    classDef ml fill:#FCE4EC,stroke:#AD1457
    classDef data fill:#ECEFF1,stroke:#455A64
    classDef ext fill:#FFF9C4,stroke:#F57F17
    classDef ops fill:#F5F5F5,stroke:#616161

    class U user
    class F front
    class E edge
    class B back
    class ML ml
    class DATA data
    class EXT ext
    class OPS ops
```

---

## 二、核心数据流

### 2.1 语音记账全链路（核心卖点）

```mermaid
sequenceDiagram
    participant U as 摊主
    participant MP as 小程序
    participant API as FastAPI
    participant ASR as 讯飞 ASR
    participant NLP as 语义解析器
    participant DB as 数据库
    participant BATCH as 批次服务

    U->>MP: 按住话筒说话<br/>"进了白菜50斤三毛一斤"
    MP->>MP: 录音 + 本地草稿保护
    MP->>API: POST /voice/upload (音频)
    API->>ASR: WebSocket 流式识别
    ASR-->>API: 返回文本
    API->>NLP: parse_voice_text()
    NLP-->>API: {商品,数量,单价,事件类型}
    API->>DB: 存 VoiceLog(parsed)
    API-->>MP: 返回识别结果 + 置信度
    MP->>U: 展示并可修正
    U->>MP: 确认
    MP->>API: POST /voice/confirm
    API->>DB: 写 InventoryRecord
    API->>BATCH: create_batch (FIFO)
    BATCH->>DB: 创建可追溯批次
    API-->>MP: 记账成功
    MP->>U: 今日毛利实时更新
```

### 2.2 弱网容错流程（创新点 5）

```mermaid
flowchart LR
    A[用户提交订单] --> B{网络可用?}
    B -- 是 --> C[POST + Idempotency-Key]
    C --> D{响应?}
    D -- 成功 --> E[✅ 入账]
    D -- 5xx/超时 --> F[指数退避重试]
    F --> D
    B -- 否 --> G[存入本地离线队列]
    G --> H[提示: 离线已保存]
    I[网络恢复事件] --> J[自动同步队列]
    J --> C
```

---

## 三、分层职责说明

| 层 | 技术栈 | 核心职责 | 关键文件 |
|---|---|---|---|
| **前端·小程序** | 微信原生 ES5、自研设计系统 | 摊主交互、离线队列、Canvas 图表 | `miniprogram/` (24 页面) |
| **前端·管理后台** | React + Ant Design | 市场管理、SaaS 运营 | `backend/admin-web/` |
| **边缘层** | Python + ONNX Runtime | 端侧视觉推理、称重采集、OTA | `edge/` |
| **后端·中间件** | FastAPI Middleware | 鉴权、幂等、租户隔离、限流、审计 | `app/core/` |
| **后端·路由** | FastAPI APIRouter (34 模块) | 244 个 REST 接口，薄层 | `app/routers/` |
| **后端·服务** | Python | 批次 FIFO、往来账、数字孪生、经验云 | `app/services/` |
| **AI/ML** | 讯飞 ASR、YOLOv8、Prophet | 语音识别、视觉识别、销量预测 | `app/services/` + `ml/` |
| **数据层** | PostgreSQL / SQLite + Alembic | 持久化、迁移管理 | `app/database.py` + `migrations/` |
| **工程基建** | GitHub Actions、Docker、Prometheus、Sentry | CI/CD、容器化、监控、错误追踪 | `.github/` + `docker-compose*.yml` |

---

## 四、技术栈一览

| 分类 | 选型 | 选型理由 |
|---|---|---|
| 前端 | 微信小程序原生 | 摊主无需下载，微信内即开即用 |
| 后端框架 | FastAPI (async) | 高性能、自动文档、原生异步 |
| ORM | SQLAlchemy 2.0 (async) | 成稳可靠、支持异步 |
| 数据库 | PostgreSQL（生产）/ SQLite（开发） | 双后端兼顾生产严谨与开发便利 |
| 迁移 | Alembic | 唯一建表权威，版本可追溯 |
| 语音识别 | 讯飞 ASR v2 (WebSocket) | 支持方言、实时流式 |
| 视觉识别 | YOLOv8 (ONNX) | 端侧可部署、推理快 |
| 时序预测 | Prophet | 适合日级销量数据、可解释 |
| 监控 | Prometheus | 指标采集标准方案 |
| 错误追踪 | Sentry | 生产异常实时告警 |
| 容器化 | Docker Compose | 一键部署依赖完整 |

---

## 五、安全架构（重点）

```mermaid
flowchart LR
    REQ[请求] --> AUTH{JWT 校验}
    AUTH -- 失败 --> R401[401 拒绝]
    AUTH -- 通过 --> TENANT[租户上下文注入<br/>TenantContext ContextVar]
    TENANT --> IDEM{写请求?<br/>查 Idempotency-Key}
    IDEM -- 命中缓存 --> CACHED[返回缓存结果]
    IDEM -- 未命中 --> BIZ[业务处理]
    BIZ --> ISOLATION[数据查询自动带<br/>merchant_id 过滤]
    ISOLATION --> AUDIT[审计日志记录]

    subgraph 启动自检
        S1[JWT_SECRET 强度] 
        S2[auth_allow_fallback<br/>生产必须 False]
        S3[CORS 白名单]
        S1 -.失败.- ABORT[拒绝启动]
        S2 -.失败.- ABORT
    end
```

**安全设计要点**：
1. 身份只来自 JWT token，绝不信任请求体中的 merchant_id
2. 生产环境启动自检（`validate_security`）fail-closed，致命误配直接拒绝启动
3. 多租户数据隔离通过 ContextVar + ORM 查询过滤双重保障
4. 写请求支持幂等键，客户端可安全重试
5. 关键操作（撤销、修改、退款）全链路审计日志

---

## 六、可扩展性设计

- **水平扩展**：后端无状态，可通过 Docker 副本横向扩展
- **租户扩展**：多租户架构天然支持商户增长
- **功能扩展**：模块化路由设计，新增业务域只需加 router + service
- **模型扩展**：AI 能力通过 service 层解耦，可替换 ASR/视觉/预测模型

---

> 📌 **使用指引**：
> - **答辩 PPT**：直接截图"总体架构图"+"语音记账全链路"
> - **README**：嵌入"总体架构图"作为核心视觉
> - **立项书**：引用"分层职责说明"表，呼应技术方案章节
