# 千摊智脑 — 团队代码质量规范与提升方案

> **Senior Developer 制定，基于 2026-07-13 项目审查结论。**
> 目标：把"功能开发型团队"升级为"可交付 SaaS 产品团队"。

---

## 一、依赖管理铁律（这次 bcrypt 问题的教训）

### 问题本质

`admin_security.py` 导入了 `bcrypt`，但 `requirements.txt` 没有声明。这不是"忘了加"——而是**缺少依赖审计流程**。

### 铁律

```
1. 任何新 import 必须在同 PR 内更新 requirements.txt
2. 任何 PR 必须在 CI 中执行 `python -c "import app.main"` 快速检查
3. 定期执行 `pip check` 检查依赖一致性
4. 版本必须固定（==），不用 >=（防止 CI 和生产的依赖版本漂移）
```

### 团队操作清单

```bash
# 每次新增依赖后执行：
pip freeze > requirements-freeze.txt  # 锁定全量依赖树（提交到仓库）

# CI 流水线中必须有的步骤：
python -c "import app.main"           # 导入检查，5 秒内发现 bcrypt 类问题
pip check                             # 依赖一致性检查
pytest --collect-only                 # 测试收集检查（不需要跑完）
```

### 反模式警示

```python
# ❌ 反模式：开发时 pip install 了 bcrypt 但忘记写入 requirements.txt
# 结果：本地能跑，CI/同事/Docker 全部炸裂
import bcrypt  # 这个依赖在 requirements.txt 里吗？

# ✅ 正确流程：
# 1. pip install bcrypt==4.2.1
# 2. 立即编辑 requirements.txt 加上 bcrypt==4.2.1
# 3. git diff 确认 requirements.txt 有变更
# 4. 提交代码 + requirements.txt 一起
```

---

## 二、配置管理标准（端口不一致的教训）

### 问题本质

小程序用 8001，Docker 用 8000，文档用 8000。三个地方三个值——谁改谁炸。

### 铁律

```
1. 配置只在一处定义，其他地方引用
2. 每个环境（dev/test/staging/prod）有明确配置文件
3. 不允许在代码中硬编码端口、URL、密钥
4. 生产配置在启动时自检（fail-fast），不合格直接拒绝启动
```

### 你们已有的好习惯（保持）

`config.py` 里的 `validate_security()` 就是一个很好的 fail-fast 模式：

```python
# ✅ 已有的好模式：生产环境启动自检
class Settings(BaseSettings):
    def validate_security(self) -> None:
        if self.debug:
            return
        if self.jwt_secret == "dev-secret-please-override-with-env-in-prod":
            raise RuntimeError("生产环境禁止使用默认 JWT_SECRET")
```

### 需要补充的

```python
# ⚠ 建议在 config.py 中增加端口一致性检查
class Settings(BaseSettings):
    api_port: int = 8000  # ← 单一事实来源

# miniprogram 端：从 app.js 的配置改为从后端 /api/v1/config 拉取
# admin-web 端：vite.config.js 的代理目标从 .env 文件读取
```

---

## 三、SaaS 多租户执行闭环（最关键的架构升级）

### 现状 vs 目标

```
现状：
  Request → JWT Auth → Merchant 查询 → 业务逻辑
  问题：租户被停用后，业务接口依然可用

目标：
  Request → JWT Auth → Merchant → Tenant 注入
    → 租户状态检查（suspended? deleted?）
    → 订阅有效性检查（active? trialing?）
    → 套餐功能检查（plan.features.pos?）
    → 配额检查（api_calls 超限?）
    → 业务逻辑
    → 用量记录
```

### 门禁链使用示例

```python
# ✅ 完整门禁链：一行依赖串联四个检查
from app.core.tenant_context import PlanFeature, QuotaCheck

@router.post("/api/v1/pos/checkout")
async def pos_checkout(
    merchant_id: uuid.UUID = Depends(get_merchant_id),      # JWT 鉴权
    _tenant = Depends(require_active_tenant),                # 门禁1：租户状态
    _sub = Depends(require_active_subscription),             # 门禁2：订阅有效
    plan = Depends(PlanFeature("pos")),                      # 门禁3：套餐含POS功能
    _quota = Depends(QuotaCheck("api_calls")),               # 门禁4：配额未超 + 自动记录
    body: CheckoutRequest = Body(...),
):
    # 到这里，所有 SaaS 门禁已通过，可以放心执行
    return await pos_service.checkout(merchant_id, body)


# 轻量级接口：只需要配额检查
@router.get("/api/v1/inventory")
async def list_inventory(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    _quota = Depends(QuotaCheck("api_calls")),
):
    return await inventory_service.list(merchant_id)


# 免费功能：不需要任何门禁
@router.get("/api/v1/health")
async def health():
    return {"status": "ok"}
```

### 迁移策略

```
Phase 1（当前）：所有门禁默认放行（STRICT_TENANT_REQUIRED=False）
  → 只记录 WARNING 日志，不阻断请求
  → 两周观察期，确认无误报

Phase 2（观察后）：对"经营核心接口"启用完整门禁
  → POS、库存、采购、AI 建议必须经过所有门禁
  → 健康检查、配置查询等不强制

Phase 3（正式上线）：STRICT_TENANT_REQUIRED=True
  → 所有业务接口强制要求 tenant_id
  → 同时完成旧商户 tenant_id 回填
```

### 改造业务接口的顺序（按影响面从小到大）

```
第一批（零风险）：管理员接口 — 本来就不走商户通道
  改造成本：0

第二批（低风险）：查询类接口
  GET /api/v1/inventory, /api/v1/products, /api/v1/suppliers
  加 Depends(QuotaCheck("api_calls")) 即可
  改造成本：每接口 1 行

第三批（中风险）：写入类接口
  POST /api/v1/pos/checkout, /api/v1/purchase/orders
  加完整四层门禁
  改造成本：每接口 4 行

第四批（高风险）：计费相关
  POST /api/v1/subscriptions, /api/v1/invoices
  确保计费操作本身不被门禁阻断（鸡生蛋问题）
  改造成本：需要仔细设计白名单
```

---

## 四、错误处理规范

### 问题

项目中有些地方 catch 了异常但只 log 不处理，有些地方返回了不一致的错误格式。

### 规范

```python
# ✅ 业务层：抛出类型化异常
from app.core.errors import QuotaExceededError, SubscriptionExpiredError

async def check_quota(tenant_id, metric):
    if current >= limit:
        raise QuotaExceededError(
            metric=metric,
            current=current,
            limit=limit,
        )

# ✅ 路由层：FastAPI 异常处理器统一转换
@app.exception_handler(QuotaExceededError)
async def quota_exceeded_handler(request, exc):
    return JSONResponse(
        status_code=429,
        content={
            "code": 42901,                # 统一错误码
            "message": f"配额超限：{exc.metric}",
            "data": {
                "metric": exc.metric,
                "current": exc.current,
                "limit": exc.limit,
            },
        },
    )

# ❌ 反模式：catch 了啥也不干
try:
    result = await some_operation()
except Exception:
    pass  # 出错了也不知道
```

### 错误码规范

```
格式：HTTP_STATUS + 2位模块码 + 2位具体错误

40101 — 未登录
40102 — Token 过期
40103 — 设备 API Key 无效
40201 — 订阅过期
40202 — 配额超限
40301 — 租户已停用
40302 — 套餐不支持此功能
40303 — 无权限
42901 — 频率限制
```

---

## 五、测试策略

### 分层原则

```
层级         | 占比  | 目标              | 示例
E2E          | 10%   | 核心流程不崩       | POS 下单 → 扣库存 → 退款回库存
集成测试      | 30%   | API + DB 正确交互  | POST /checkout → 检查库存流水
单元测试      | 60%   | 逻辑正确性         | calculate_total() 各种边界
```

### 你们当前的问题

```python
# ❌ 问题：测试文件里直接 import 了会加载全部依赖的模块
# tests/test_something.py
from app.main import app  # 这行导致 bcrypt 缺失时整个测试套件崩溃

# ✅ 改进：测试只导入需要的
# tests/test_quota.py
from app.core.quota import check_quota, record_usage
# 不需要 app.main，不需要 bcrypt
```

### 每个 PR 必须通过的检查

```bash
# 1. 导入检查（5 秒）
python -c "import app.main"

# 2. Lint（10 秒）
ruff check app/ tests/

# 3. 单元测试（< 30 秒）
pytest tests/ -x -m "not slow"

# 4. 集成测试（< 2 分钟）
pytest tests/ -x -m "integration" --db=test
```

---

## 六、代码审查清单

每个 PR 合并前，审查者必须确认以下项目：

### 安全性
- [ ] 新接口有鉴权依赖（Depends(get_merchant_id) 或 DeviceAuth）
- [ ] 没有在请求体中信任 client 传来的 merchant_id / tenant_id
- [ ] 新依赖已加入 requirements.txt
- [ ] 没有硬编码密钥、密码、Token

### SaaS 合规
- [ ] 新业务接口是否绕过租户/订阅/配额检查？
- [ ] 如果是新增的计费相关接口，是否在门禁白名单中？
- [ ] 新功能是否对应 plan.features 中的某个开关？

### 代码质量
- [ ] 没有 `except Exception: pass` 或裸 `except:`
- [ ] 没有未使用的 import
- [ ] 错误返回使用了统一格式 `{code, message, data}`
- [ ] 日志包含关键上下文（merchant_id, tenant_id, request_id）

### 测试
- [ ] 新增/修改的逻辑有对应测试
- [ ] 异常路径有测试覆盖（不只是 happy path）
- [ ] 没有因为新增 import 导致测试套件无法启动

---

## 七、团队技能提升路线

### 每个开发者必须掌握

| 技能 | 重要程度 | 学习方法 |
|------|---------|---------|
| FastAPI 依赖注入 | ⭐⭐⭐⭐⭐ | 本项目 tenant_context.py 就是最好的教材 |
| SQLAlchemy async | ⭐⭐⭐⭐⭐ | 阅读 quota.py 的用法，理解 session 生命周期 |
| ContextVar 请求隔离 | ⭐⭐⭐⭐ | 阅读 tenant_context.py 的实现 |
| Pydantic Settings | ⭐⭐⭐⭐ | 阅读 config.py 的 validate_security() |
| pytest + pytest-asyncio | ⭐⭐⭐⭐ | 给现有接口补测试就是最好的练习 |

### 推荐的代码阅读顺序（新人入职）

```
1. backend/app/config.py           — 了解配置体系和安全检查
2. backend/app/core/security.py    — 理解 JWT 鉴权流程
3. backend/app/core/tenant_context.py — 理解多租户门禁链（最重要）
4. backend/app/core/quota.py       — 理解用量计量
5. backend/app/routers/edge.py     — 理解设备接入
6. backend/app/models/saas.py      — 理解数据模型
```

---

## 八、工具链配置

### pre-commit hook（推荐安装）

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: ruff-check
        name: Ruff Lint
        entry: ruff check app/ tests/
        language: system
        pass_filenames: false
      - id: import-check
        name: Import Check
        entry: python -c "import app.main"
        language: system
        pass_filenames: false
      - id: requirements-check
        name: Requirements Check
        entry: pip check
        language: system
        pass_filenames: false
```

### VS Code 推荐配置

```json
{
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.organizeImports": "explicit"
    }
  }
}
```

---

## 九、本周立即执行清单

按优先级排列，每天完成一组：

### Day 1：恢复可运行状态

- [ ] `pip install -r requirements.txt` 确认 bcrypt 已安装
- [ ] `pytest --collect-only` 确认测试能收集
- [ ] `ruff check app/` 修复 F821/F811/F841 错误（逻辑问题优先）
- [ ] 多端口统一为 8000（已完成）

### Day 2-3：SaaS 门禁接入

- [ ] 选择 3 个查询类接口加上 `QuotaCheck("api_calls")`
- [ ] 观察日志，确认 WARNING 级别行为正常
- [ ] 写 5 个 tenant_context 的单元测试

### Day 4-5：CI 搭建

- [ ] 创建 `.github/workflows/ci.yml`
- [ ] 包含：import check → ruff → pytest → pip check
- [ ] 确保 CI 绿灯

### Day 6-7：代码清理

- [ ] 修复所有 Ruff 问题
- [ ] 统一 mypy 配置
- [ ] 清理未使用的 import

---

> **最后提醒**：你们审查报告里列的 333 个测试函数、354 个 Ruff 问题、21 个页面——这些数字本身不丢人，说明项目规模已经到了。现在最需要的不是"写出更多代码"，而是"每写一行代码都保证不破坏已有的 333 个测试"。
>
> 从今天开始，把 CI 流水线搭起来，让每一次提交都自动跑检查——这才是从"学生项目"到"商业产品"最关键的一步。
