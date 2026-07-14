# 千摊智脑 — 项目总览

> 最后更新：2026-07-13 | Senior Developer 代码质量提升回合

---

## 本次回合 (2026-07-13 evening)：生产可运行性筑基

### P0-1 ✅ bcrypt 依赖修复
- `requirements.txt` 新增 `bcrypt==4.2.1`
- `requirements-test.txt` 补充 bcrypt 依赖
- `admin_security.py` 清理未使用的 `hashlib` 导入

### P0-2 ✅ 端口配置统一
- 小程序 `app.js` 默认 API base：8001 → 8000
- admin-web `vite.config.js` 代理目标：8001 → 8000
- 创建 `.env.example` 统一环境变量模板

### P0-3 ✅ SaaS 执行门禁架构
- `tenant_context.py` 大幅增强：新增 4 个门禁依赖
  - `require_active_tenant()` — 租户状态检查
  - `require_active_subscription()` — 订阅有效性检查
  - `PlanFeature("pos")` — 套餐功能门禁工厂
  - `QuotaCheck("api_calls")` — 配额检查+自动记录工厂
- `security.py`：get_current_merchant 注入 tenant_id 到 ContextVar
- `quota.py`：record_usage 增加并发冲突重试（IntegrityError → rollback → retry ×3）
- 过渡期策略：`STRICT_TENANT_REQUIRED=False`，仅 WARNING 日志不阻断

### P0-4 ✅ 设备 API Key 鉴权方案
- 新建 `app/core/device_auth.py`（Device ID + API Key + Timestamp + Nonce 四要素验证）
- `edge.py` 新增 `/ingest/device` 和 `/heartbeat` 端点（走 DeviceAuth）
- `edge_config.example.json` 边缘端配置模板
- 密钥生成：`generate_api_key()` → SHA-256 哈希存储，明文仅返回一次

### 团队规范
- `docs/team-code-quality-guidance.md`：完整的依赖管理、配置标准、错误处理、测试策略、代码审查清单

### 下一步（团队执行）
- [ ] `pip install -r requirements.txt` 验证 bcrypt
- [ ] `pytest --collect-only` 确认测试可收集
- [ ] 选 3 个查询接口接入 QuotaCheck
- [ ] 创建 `.github/workflows/ci.yml`
- [ ] 修复 Ruff F821/F811/F841 错误

---

## 千摊智脑后台 V2 升级全量实施报告

> 日期：2026-07-13 | 最终状态：全部完成

---

## 已完成（按蓝图逐项对照）

| 蓝图要求 | 实现 |
|----------|------|
| Phase 0 — 路由懒加载/分包/主题/错误处理 | 主包 558→25kB · ESLint 0 |
| Phase 1 — 新版Dashboard/租户列表/360°工作台/审计 | 5 页全面升级 |
| Phase 1 — Plans/Subs/Invoices/Usage 体验统一 | 4 页 PageHeader+PermissionGate |
| Phase 2 — 接入向导/AI运营/设备监控+3D | 3 新页面 |
| Phase 2 — 管理员管理 | 后端CRUD + 前端页面 |
| Phase 2 — RBAC 权限 | 25 权限点 + 5 角色 + 28 端点声明 |
| Phase 2 — 审计日志 | 全 CRUD 补全 |
| Phase 2 — 核心实体状态机 | Tenant/Subscription/Invoice/AIAction |
| Phase 2 — 高风险操作原因输入 | 标记支付+取消订阅 强制原因 |
| Phase 3 — Canvas 粒子网络拓扑 3D 场景 | Devices 页面集成 |

---

## 新增/修改文件统计

- **新增**: 28 个（7 组件 + 7 页面 + 2 权限 + 3 配置 + 1 RBAC 后端 + 1 状态机 + 1 管理员 API + 1 原因确认组件 + 3 后端改造 + 2 路由注册）
- **修改**: 15 个

## 最终验收

| 检查项 | 结果 |
|--------|------|
| ESLint | 0 errors / 0 warnings |
| Build | 0 warnings |
| 主包 gzip | 25 kB |
| 路由 chunk | 14 个独立懒加载 |
| 后端语法 | 全部通过 py_compile |
| 状态机 | 3 个路由已接入 |
| 高风险确认 | mark-paid + cancel 强制原因 |
