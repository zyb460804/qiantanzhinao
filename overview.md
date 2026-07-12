# FastAPI 0.139.0 include_router 静默失效 — 排查修复报告

**日期**：2026-07-12  
**排查人**：Senior Developer（高级开发工程师）  
**问题等级**：P0 — 阻塞所有前端功能

---

## 问题

微信小程序启动后所有页面报 `POST /api/v1/auth/wechat-login 404 (Not Found)`，导致登录初始化失败，后继所有 API 调用全部失败。

## 诊断过程

1. 确认前端调 `/api/v1/auth/wechat-login`，后端 `auth.py` 路由定义正确（prefix + POST /wechat-login）。
2. 检查 OpenAPI JSON → 没有任何 `/api/v1/auth/*` 路径。
3. 独立 import auth router → 4 条路由正常存在。
4. `from app.main import app` → 仅 6 条路由（openapi/docs/redoc + 2 条 @app.get），23 个 `include_router` 全部失效。
5. 隔离测试 → Python 3.13 + FastAPI 0.139.0 的 `include_router` 静默失效；Python 3.11 + FastAPI 0.115.0 正常。

## 根因

**FastAPI 0.139.0 在 Python 3.13.14 上存在 `include_router()` 静默失效的严重 bug**。
项目 venv 中 FastAPI 被意外升级到了 0.139.0（`requirements.txt` 原本锁了 `==0.115.0`），导致所有通过 `include_router` 注册的路由不生效。

## 修复

| 步骤 | 操作 |
|------|------|
| 1 | 手动清理 `site-packages/fastapi` 旧版本残留 |
| 2 | `pip install fastapi==0.115.6` |
| 3 | `requirements.txt` 锁定 `fastapi==0.115.6` + 严禁升级注释 |

## 验证

- `import app.main` → **168 routes** ✅（含 4 条 auth 路由）
- `curl /api/v1/auth/wechat-login` → 返回业务错误（非 404），路由已通 ✅
- OpenAPI JSON 证实 4 条 auth 路径可见 ✅
- `uvicorn` 启动正常，健康检查 200 ✅

## 团队行动项

- **禁止**在 Python 3.13 上升级 FastAPI 到 >=0.139.0，直至社区修复确认。
- 如需验证新版本：在目标 Python 版本下跑 `include_router` 冒烟测试后再升级。
- `pip install` 务必从 `requirements.txt` 安装，避免手动 `pip install fastapi` 不带版本号。

---

## 技术细节备忘

```python
# 冒烟验证脚本
from fastapi import FastAPI, APIRouter
test_router = APIRouter()
@test_router.get('/test')
def test(): return {'ok': True}
app = FastAPI()
app.include_router(test_router)
assert any('test' in r.path for r in app.routes if hasattr(r, 'path')), \
    "include_router NOT WORKING — DO NOT USE THIS FASTAPI VERSION"
```

---

# 续：wechat-login dev mock 模式（2026-07-12 14:44）

## 问题
404 修好后，前端报 503 "服务端未配置微信 AppID/Secret"——本地开发没有真实微信凭证。

## 根因
`wechat_code2session()` 在生产设计下要求 `WECHAT_APPID` + `WECHAT_SECRET`，本地 dev 未配置即 503。

## 修复
`app/core/security.py` 的 `wechat_code2session` 新增 dev mock 分支：
- `settings.debug=True` 且无微信凭证 → 用 `sha256("qiantan-dev:{code}")` 生成确定性 mock openid
- `settings.debug=False` → 保持 fail-closed，绝无降级
- 同一个 code = 同一个 openid = 同一商户，幂等

## 验证
```
# 首次登录 → 创建新商户
POST /api/v1/auth/wechat-login {"code":"wx_test_code_001"}
→ 200, is_new:true, token:eyJ...

# 重复登录 → 返回同一商户
POST /api/v1/auth/wechat-login {"code":"wx_test_code_001"}  
→ 200, is_new:false, same merchant_id
```
