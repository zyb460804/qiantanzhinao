# 离线记账与断网同步 · 参考实现（教学样例）

> 本目录是**资深开发工程师给出的「正确范式」参考**，目的不是直接上线，而是让团队
> 在动手实现 PRD（千摊智脑-离线记账与断网同步-PRD.md）前，先建立对「离线队列 / 同步引擎 /
> 幂等落库 / 端上解析」的统一认知，照着学、照着改。
>
> 现有 `miniprogram/pages/pos.js` 等仍是**纯本地原型**（乐观更新但没有队列、没有 `client_id`、
> 不同步到后端）。本参考展示它们「应该长什么样」。

## 目录结构

```
reference/offline-sync/
├── README.md                  # 你正在看
├── miniprogram/
│   ├── offline-queue.js       # 持久化队列：落盘即存、崩溃恢复、容量上限、静态加密、client_id
│   ├── sync-engine.js         # 同步引擎：syncing 锁、FIFO、指数退避、冲突分流、网络恢复触发
│   ├── voice-parser.js        # 端上规则解析器（纯函数，镜像服务端逻辑，对应 PRD D2）
│   ├── offline-queue.test.js  # Node 单测（队列核心逻辑，零 wx 依赖）
│   ├── voice-parser.test.js   # Node 单测（解析器）
│   └── package.json           # type:module，便于 node --test
└── backend/
    ├── idempotency.py         # 幂等决策纯函数（零依赖、可单测、前后端共用）
    ├── offline_sync_service.py# 幂等落库服务：先查后插 + 唯一约束兜底 + 审计留痕(R3)
    ├── models_offline.py      # 模型增量参考：client_id 字段 + 唯一约束
    ├── migration_offline.py   # Alembic 迁移骨架（对应 PRD §7.2 D3）
    ├── routers_offline.py     # 接收接口（Pydantic 模型入参，非裸 dict）
    └── test_offline_sync.py   # pytest：纯函数 + FakeSession 集成测试
```

## 资深评审要点（为什么这样写）

1. **幂等是第一公民**。`client_id` 由客户端生成 UUID v4，后端用 `(merchant_id, client_id)`
   唯一约束兜底——重复上送返回既存记录，绝不产生第二条。这是「不记两次」唯一可信保证
   （PRD §4.1.2 / P0-4）。
2. **先查后插仍要防 race**。并发下两次插入可能都「查不到」，所以插入时 `catch IntegrityError`
   再读一次，保证唯一约束这道最后防线真正生效（`offline_sync_service.upsert_offline_record`）。
3. **多租户隔离**。所有查询都带 `merchant_id`；跨商户的 `client_id` 在本商户查不到，自然 404，
   绝不返回他人数据。生产环境 `merchant_id` 应来自鉴权 token，而非客户端自报。
4. **脱 wx 可测**。队列核心、同步引擎、解析器都把「存储 / 网络 / 随机源」通过依赖注入传入，
   因此能在 Node / pytest 里原地单测，无需微信开发者工具。
5. **加密在存储边界**。整条队列落盘前加密（`at-rest`），敏感金额不以明文驻留（PRD §6.2）。
   生产把占位 XOR 换成真 AES-GCM，密钥经微信隐私接口 / 用户手势派生，绝不硬编码。
6. **崩溃可恢复**。重启时停留在 `syncing` 的条目重置为 `pending` 重新尝试，不丢账。
7. **业务错 vs 网络错分流**。业务错（商品已删）标记 `conflict` 交冲突中心(R6)，不无限重试；
   网络错指数退避（2/4/8/16/32s，最多 5 次）。

## 如何运行测试（验证范式正确）

```bash
# 前端（Node 22+）
cd reference/offline-sync/miniprogram
node --test
# 期望：offline-queue / voice-parser 全部通过

# 后端（在 backend/ 目录下，使用项目 .venv）
cd ../../..            # 回到 qiantan-brain/backend
pytest reference/offline-sync/backend
```

## 接入生产时请团队注意

- 把 `offline-queue.js` / `sync-engine.js` 迁进 `miniprogram/utils/`，并接好
  `app.js` 的 `wx.onNetworkStatusChange` 与 `request()`。
- 把 `idempotency.py` / `offline_sync_service.py` 的逻辑合并进 `app/services/` 与对应 router，
  并先跑 `migration_offline.py` 的 Alembic 版本（**不要**直接 ALTER 表）。
- 解析器 `voice-parser.js` 与服务端 `app/services/voice_parser.py` 必须共用一份规则契约，
  并在 CI 两端各跑一份共享 fixture，防止语义漂移（PRD D2）。
- 全量接好前，先用仓库根的 `scripts/check.sh` 跑一遍质量门禁（ruff + WXSS 扫描 + pytest）。
