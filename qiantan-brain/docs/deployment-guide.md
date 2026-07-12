# 部署手册 (Deployment Guide)

千摊智脑后端为 FastAPI 异步服务，数据库支持 **SQLite（本地快速开发）** 与 **PostgreSQL 16（Docker / 正式部署）** 两种后端，通过环境变量切换。

## 1. 本地开发（SQLite）

```bash
cd backend
pip install -r requirements.txt   # 或已安装的等效依赖
uvicorn app.main:app --reload --port 8000
```

默认 `DB_BACKEND=sqlite`，`DATABASE_URL` 指向本地 `qiantan_dev.db`，启动时会自动 `init_db()` 建表（开发模式）。

## 2. 切换到 PostgreSQL（Docker）

`docker-compose.yml` 已配置 PostgreSQL 16 + FastAPI。

```bash
# 1) 设置环境变量（写入 .env 或 export）
export DB_BACKEND=postgresql
export DATABASE_URL="postgresql+asyncpg://qiantan:qiantan@localhost:5432/qiantan"

# 2) 启动数据库与服务
docker compose up -d db
pip install asyncpg        # PostgreSQL 异步驱动
uvicorn app.main:app --port 8000
```

## 3. 环境变量清单（.env）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DB_BACKEND` | `sqlite` / `postgresql` | `sqlite` |
| `DATABASE_URL` | 完整数据库连接串 | 本地 sqlite 路径 |
| `ASR_APP_ID` / `ASR_API_KEY` / `ASR_API_SECRET` / `ASR_API_URL` | 讯飞语音鉴权 | 空（走演示模式） |
| `WEATHER_API_KEY` / `WEATHER_API_URL` / `WEATHER_CITY_ID` | 和风天气 | 空（走 Mock） |
| `PRIVACY_EPSILON` | 经验云差分隐私预算，越小越私密 | `1.0` |
| `PRIVACY_QUERY_BUDGET` | 单 key 查询预算上限 | `100` |

## 4. 全新数据库验收流程（必做）

```text
空数据库
  → alembic upgrade head        # 应用全量迁移（当前 head: 80bd7e0fc1ac，32 张业务表）
  → 初始化种子数据（商品品类等）
  → 启动后端 uvicorn
  → 运行全部测试 / 接口冒烟测试
```

目的：避免迁移文件只覆盖新增表而遗漏初始结构。请在一个全新 PostgreSQL 实例上完整跑通后再对外演示。

## 5. 边缘端（Raspberry Pi 5）

见 `docs/hardware-guide.md`。边缘端通过 `edge/main.py` 采集并离线缓存，恢复联网后 POST 到后端 `/api/v1/edge/ingest`。
