# 千摊智脑 QianTan Brain

面向小微商户的环境感知型 AI 经营智能体与数字孪生决策系统。

[详细文档 →](./qiantan-brain/README.md)

## 快速链接

| 服务 | 地址 |
|------|------|
| 后端 API | `http://127.0.0.1:8000` |
| API 文档 | `http://127.0.0.1:8000/docs` |
| Web 管理后台 | `http://localhost:5174` |
| 管理后台登录 | `admin@qiantan.com` / `Admin123!` |

## 技术栈

- **小程序**：微信小程序原生 · 23 个页面
- **管理后台**：React 18 + Vite + Ant Design 5
- **后端**：FastAPI + Python 3.11 + SQLAlchemy 2.0
- **数据库**：PostgreSQL 16（生产）/ SQLite（本地开发）
- **测试**：404 个测试通过

## 快速启动

```bash
# 后端
cd qiantan-brain/backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload

# 初始化 SaaS 数据
python -m scripts.seed_saas

# Web 后台
cd admin-web
npm install && npx vite

# 测试
python -m pytest tests/ -v
```

## 项目结构

```
qiantan-brain/
├── miniprogram/      # 微信小程序
├── backend/          # FastAPI + Web 管理后台
├── edge/             # 树莓派边缘端
├── ml/               # YOLO / Prophet 训练
└── docs/             # 文档
```
