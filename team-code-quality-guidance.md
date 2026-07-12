# 千摊智脑 · 代码质量与团队能力提升指南

## 一、本次问题复盘

### 现象
微信开发者工具控制台先报错：
```
[ WXSS 文件编译错误 ] ./app.wxss(374:3): unexpected token "{"
```
修复后紧接着出现：
```
[ WXSS 文件编译错误 ] ./app.wxss(374:16): unexpected token "*"
```
右侧模拟器白屏，所有样式未生效。

### 根因（连环坑）
`app.wxss` 第 373 行写入了 CSS 标准的媒体查询：
```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { ... }
}
```
这里同时踩中两条 WXSS 红线：

1. **WXSS 不支持 `@media` 媒体查询语法**。解析器不认识 `@media`，因此把紧随其后的 `{` 判定为非法 token。
2. **WXSS 不支持 `*` 通配选择器**。即使去掉 `@media`，`*` 本身也会被解析器拒绝。

两个特性都是 CSS 标准，但在 WXSS 里都被裁剪了。

### 已修复内容
1. 删除不兼容的 `@media (prefers-reduced-motion: reduce)` 块。
2. 将 `*, *::before, *::after` 替换为 WXSS 支持的常见标签枚举，保留 `.reduce-motion` 类名降级方案：在需要关闭动效的页面 `page` 节点上添加 `class="reduce-motion"` 即可。
3. 顺手清理了两处 WXSS 不支持的视口单位：
   - `.page-shell { min-height: 100vh; }` → `min-height: 100%`
   - `.toast-box { max-width: 70vw; }` → `max-width: 70%`
4. **`components/icon/icon.wxss` 本地 SVG 图标报错**：WXSS 不允许用 `url()` 引用本地图片（mask-image/background-image 都不行），导致每个图标都报「渲染层网络层错误」。改用 **base64 内联** 到 mask（`url("data:image/svg+xml;base64,...")`），既消除报错、又保留 CSS 颜色主题化（mask + background-color 上色）。配套生成脚本可随时由 `images/icons/*.svg` 重新生成。

---

## 二、WXSS 常见兼容性红线（团队自查表）

| 特性 | WXSS 支持情况 | 替代方案 |
|------|--------------|----------|
| `@media` | ❌ 不支持 | 用类名控制（如 `.reduce-motion`）或 JS 判断 |
| `:root` | ❌ 不支持 | 用 `page { ... }` 定义全局 CSS 变量 |
| `*` 通配选择器 | ❌ 不支持 | 明确标签选择器或类名枚举 |
| WXSS 中 `url()` 引用本地图片 | ❌ 不支持 | 网络图片 / base64 内联 / `<image>` 组件 |
| `vh` / `vw` / `rem` | ❌ 不支持 | 用 `rpx` / `px` / `%` |
| `vh` / `vw` / `rem` | ❌ 不支持 | 用 `rpx` / `px` / `%` |
| `:hover` / `:focus` / `:active` | ⚠️ 部分可用但无实际意义 | 小程序用 `hover-class` / `catchtap` |
| `*` 通配选择器 | ❌ 不支持 | 明确标签选择器或类名 |
| `calc()` | ✅ 基本支持（注意旧机型兼容） | 尽量用固定 `rpx` 或 JS 计算 |
| CSS 变量 `--x` | ✅ 支持 | 推荐在设计系统中使用 |
| `env(safe-area-inset-*)` | ✅ 支持 | 用于刘海屏安全区适配 |

**重点建议**：遇到 WXSS 编译报错，先把错误定位到具体行，优先检查是否用了 CSS 标准但 WXSS 未实现的特性。

---

## 三、团队代码质量提升建议

### 1. 建立 WXSS / WXML 代码规范
- 单位统一：视觉稿以 `rpx` 为主，边框/细线可用 `px`（避免奇数 rpx 渲染模糊）。
- 避免行内样式：除动态计算的值外，样式应收敛到 `.wxss`。
- 类名语义化：使用 BEM-like 命名（如 `.toast-box__title--active`），避免 `.a` `.b` 这种无意义类名。
- 组件化：公共样式抽到 `app.wxss`，页面私用样式保留在页面 `.wxss`。

### 2. 引入自动化检查（低成本高收益）
- **WXSS 语法扫描**：在 CI 或本地加入规则，扫描 `@media`、`:root`、`vh`/`vw`/`rem`、裸 `*` 选择器等。
- **ESLint + Prettier（小程序 JS）**：统一代码风格，避免 `var`、未使用变量、隐式全局变量。
- **Git 提交前检查**： Husky / pre-commit 钩子执行 lint，防止明显错误进入仓库。

### 3. 代码审查 Checklist（Review 时逐条核对）
- [ ] 新增样式是否在微信开发者工具中无编译警告/错误？
- [ ] 是否用了 `wx.request` 等异步 API 的异常处理（`fail`、`complete`）？
- [ ] 网络请求是否统一走 `app.js` 封装的 `request()` 以复用错误处理？
- [ ] 新增的接口调用是否包含 `merchant_id` 等数据隔离参数？
- [ ] 是否所有页面都有对应的 `.json` 配置（避免导航/下拉配置缺失）？
- [ ] 是否对空数据、加载失败、网络异常做了 UI 兜底？
- [ ] 是否新增了可复用组件，而非复制粘贴同类代码？
- [ ] **（后端账本）** 新增/修改的写接口是否带 `idempotency_key` 幂等键？
- [ ] **（后端账本）** 库存/金额计算是否走 `SUM/GROUP BY` 全量聚合 + 排除 `is_voided`，且无 `.limit()` 截断？
- [ ] **（后端账本）** 纠错是否用「作废 + 冲正记录 + AuditLog」，而非 `DELETE`/`UPDATE` 历史？
- [ ] **（后端账本）** 单位换算是否走 `unit_service` + `unit_conversions`，无硬编码因子？
- [ ] **（后端账本）** 查询是否都带 `merchant_id` 隔离，无跨商户泄漏风险？
- [ ] **（后端账本）** 模型变更是否同步生成了 Alembic 迁移文件？

### 4. 后端 Python 侧同步提质
- **类型注解**：关键函数参数、返回值使用 `typing` 注解，配合 `mypy` 检查。
- **Pydantic 模型校验**：所有接口入参优先用模型，不要裸 `dict.get()`。
- **测试先行**：新增接口必须同步补充 `pytest` 用例，覆盖正常路径、异常路径、多商户隔离。
- **Alembic 迁移**：任何模型字段变更必须生成迁移脚本，禁止直接改表。
  ⚠️ `migrations/env.py` 已改为 `from app.models import *`（通配导入），与
  `app/models/__init__.py` 的 `__all__` 自动同步——**新增模型会被 autogenerate 自动纳入**，
  不会再出现 stocktake/purchase/audit 曾被遗漏的坑。但每次改完模型仍须跑
  `alembic revision --autogenerate` 并提交迁移文件（dev 库用 create_all，生产库用 `alembic upgrade head`）。

### 5. 后端账本专项红线（真实经营闭环）

> 以下规则对应战略文档「建立真实业务账本」，是摊主敢把生意交给系统的底线。
> 任何一笔账错了，信任就崩了。Review 时逐条核对。

1. **流水账为真相，快照不记账**：库存「当前量」只能由 `inventory_records` 聚合或触发器计算，**禁止**直接 `UPDATE` 某个 `current_qty` 字段当记账。聚合查询（如 `GET /inventory/current`）必须 `SUM/GROUP BY` 全量、且 `WHERE is_voided = false`，**绝不可用 `.limit(N)` 截断**（旧实现曾因 `.limit(200)` 漏算、未排除作废、Python 循环累加三重缺陷，已修复于 `app/routers/inventory.py:get_current_inventory`）。
2. **不可删改，只能作废/冲正**：任何记账记录不允许物理删除或修改历史。纠错路径 = `is_voided` 标记作废 + 生成冲正/修正记录（`is_correction` + `original_record_id`），并写 `AuditLog`。见 `app/models/inventory.py`、`app/routers/inventory.py:void_inventory_record`。
3. **单位换算只在边界、因子走 DB**：入库/出库记录一律以 SKU 的 `canonical_unit`（标准单位，如斤）存储；包装单位（筐/袋/件）只在语音解析/导入时换算，换算因子必须查 `unit_conversions`（随商家/商品不同，一筐西红柿≈45斤、一筐土豆≈60斤），**禁止硬编码**在业务代码里。换算逻辑集中在 `app/services/unit_service.py`。
4. **幂等键防重复入账**：所有「写」接口（记账 / 采购确认 / POS 收款 / 日结）必须携带 `idempotency_key`（客户端生成，或按归一化内容派生）。`inventory_records.idempotency_key` 已加唯一索引，NULL 不参与冲突。重试同一动作只入账一次。
5. **多租户隔离**：每条查询都要带 `merchant_id` 过滤，联表也是如此；**禁止**出现「查全表再在内存里按商户分」或漏写过滤导致跨商户数据泄漏。
6. **商品主数据用 SKU**：库存/批次/账本的主键是 `product_skus.id`，别名走 `product_aliases`（番茄/西红柿/洋柿子→同一 SKU）。不再把扁平的 `product_categories` 当主键。详见 `app/models/catalog.py`。
7. **金额用 Decimal/NUMERIC**：成本、售价、金额一律 `NUMERIC`/`Decimal`，**禁止**用 `float` 累加金额（二进制浮点精度丢失会导致对账差几分钱）。

### 6. 知识沉淀
- 把本指南、常见 WXSS 错误及修复方式沉淀到项目 `docs/` 或 `README.md`。
- 每次线上故障或编译阻塞问题，按「现象 → 根因 → 修复 → 预防措施」四段式记录。

---

## 四、推荐团队学习路径

1. **微信小程序官方文档精读**：WXSS、WXML、自定义组件、生命周期、性能优化。
2. **CSS 与 WXSS 差异**：明确哪些 CSS 特性可用、哪些不可用，避免直接 copy Web 端样式。
3. **代码审查实践**：每周固定 30 分钟代码 review，重点看边界处理和可维护性。
4. **单元测试**：后端优先覆盖核心服务（库存、批次 FIFO、报表计算），前端补充组件快照测试。

---

## 五、后续可跟进事项

- [ ] 在项目中加入 WXSS 语法扫描脚本（正则即可，无需复杂工具）。
- [ ] 统一整理并落地一份《千摊智脑 小程序/后端编码规范》。
- [ ] 为 `app.wxss` 等核心文件补充样式说明注释，避免后人误写不兼容语法。
- [ ] 建立 `docs/bug-archive.md`，持续记录类似 WXSS 编译、API 异常等典型问题。

---

## 六、离线记账 / 断网同步 专项评审清单（PRD 重点）

> 离线优先功能一旦写错，后果是**重复记账 / 丢账 / 跨商户串数据**，比普通 bug 更严重。
> 凡涉及 POS 记账、离线队列、同步接口、幂等落库的 MR，review 时逐条勾选。
> 配套可运行样板见 `qiantan-brain/reference/offline-sync/**`（含 Node 11/11 + pytest 6/6 测试）。

### 6.1 幂等（最高优先级，防重复记账）

- [ ] 每一笔离线记录都带 `client_id`（`crypto.randomUUID()` / `uuidv4`，**前端生成，不能后端发**）。
- [ ] 后端 `InventoryRecord`（及同类账本表）有 `client_id` 列 + `(merchant_id, client_id)` **唯一约束**。
- [ ] 落库逻辑是「先按 `(merchant_id, client_id)` 查，存在则跳过/更新，不存在则插」；唯一约束作为 race 兜底，捕获 `IntegrityError` 后视为已处理（**不是报错**）。
- [ ] 同步接口返回 `status: "created" | "duplicate"`，前端据此把本地队列标记 `synced` 而非 `failed`。

### 6.2 持久化队列（断网不丢）

- [ ] 本地用 `wx.setStorageSync` 维护一个**数组队列**（键名如 `qt_offline_queue`），不是零散的 `posToday`/`posRecords` 变量。
- [ ] 入队结构包含：`client_id`、`payload`、`status`(pending/syncing/synced/conflict/failed)、`attempts`、`created_at`。
- [ ] 入队即返回"已记账"给 UI（乐观更新）；只有 `synced` 才算真正确认。
- [ ] 队列有上限（如 200 条）与 `prune`（清理已 synced 的老记录），避免 storage 膨胀。

### 6.3 同步引擎（只发一次、可重试）

- [ ] 单飞锁：同一时刻只有一个同步在跑（`syncing` 状态），避免并发双发。
- [ ] FIFO 顺序发送；失败项指数退避（2/4/8/16/32s，最多 5 次）后转入 `failed` 人工区。
- [ ] 网络层**只走一处**封装（`app.js` 的规范 `request()`，带 `error-type` 契约）；**禁止**再出现 `utils/api.js` 那种第二份弱封装。
- [ ] 弱网/断网自感知：用 `wx.getNetworkType` / `onNetworkStatusChange` 触发重试，而非盲目轮询。

### 6.4 冲突与一致性（服务端为准）

- [ ] 冲突定义明确：同一 `client_id` 在服务端已存在但内容不一致 → 进冲突中心，**不静默覆盖**。
- [ ] 金额/数量以"服务端为准"或走 LWW（按 `client_id` 生成时间戳），前端展示"同步报告"告知用户哪几笔有分歧。
- [ ] 多租户：`merchant_id` 一律从 token 取（`Depends(get_merchant_id)`），**绝不信任 body 里的 merchant_id**。
- [ ] 所有写入走 Pydantic `Body` 模型（**禁止 `dict = Body(...)`**），字段缺失返回 422 而非 500。

### 6.5 离线 UI 的 WXSS 红线（复用第三节）

- [ ] 离线状态条、冲突提示、同步报告等新增样式，依旧遵守 `@media`/`:root`/`*`/`vh`/`vw`/`rem`/本地 `url()` 红线。
- [ ] 图标用 `data:` base64 内联（见 `components/icon/icon.wxss`），字体走 CDN `https`。
- [ ] 提交前过 `bash scripts/wxss-lint.sh`，确认无兼容性红线（v2 已修误报，可信任）。

### 6.6 自测要求

- [ ] 后端：`pytest` 覆盖 `decide_upsert` 的"首次创建 / 重复幂等 / 并发兜底"三态。
- [ ] 前端：`node --test` 覆盖队列 enqueue→syncing→synced→conflict→failed 的状态机与退避。
- [ ] 真机/模拟器走查：开飞行模式记 3 笔 → 关飞行模式 → 确认 3 笔全部到后端且无重复。
