"""
test_offline_sync.py — 离线幂等落库 参考实现测试
────────────────────────────────────────────────────────────────────────
运行：从 backend/ 目录执行  pytest reference/offline-sync/backend
  （或在该目录直接 pytest，前提是 backend 的 app 包可导入）

说明：
  - idempotency 的纯函数测试「零依赖」，任何环境都能跑；
  - upsert_offline_record 的集成测试用 FakeSession 模拟数据库，验证「先查后插 + 唯一约束兜底」
    的并发安全幂等写法；若 app 依赖不可导入则自动跳过（不阻塞 CI）。
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from idempotency import decide_upsert, is_valid_uuid_v4, SyncResult  # noqa: E402


# ── 纯函数：幂等决策 ───────────────────────────────────────────────────────
def test_decide_upsert_duplicate_when_existing():
    assert decide_upsert({"id": 1}, {"amount": 1}) == "duplicate"


def test_decide_upsert_create_when_absent():
    assert decide_upsert(None, {"amount": 1}) == "create"


def test_decide_upsert_conflict_when_flagged():
    assert decide_upsert(None, {"_conflict": True}) == "conflict"


def test_is_valid_uuid_v4():
    assert is_valid_uuid_v4(str(uuid.uuid4()))
    assert not is_valid_uuid_v4("not-a-uuid")
    assert not is_valid_uuid_v4("123e4567-e89b-12d3-a456-426614174000")  # v1，不是 v4


# ── 集成：用 FakeSession 验证 upsert 的并发安全幂等 ─────────────────────────
class FakeRecord:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.id = uuid.uuid4()


class FakeSession:
    """最小化的异步 Session 替身，足以驱动 upsert_offline_record 的主流程。"""

    def __init__(self, *, raise_on_add: bool = False):
        self.store: dict[str, FakeRecord] = {}
        self.raise_on_add = raise_on_add

    async def execute(self, stmt):
        class _R:
            def scalar_one_or_none(self):  # noqa: D401 - 极简替身
                return None

        return _R()

    def add(self, rec: FakeRecord) -> None:
        if self.raise_on_add:
            from sqlalchemy.exc import IntegrityError

            raise IntegrityError("unique violation", None, None)
        self.store[rec.client_id] = rec

    async def flush(self) -> None:  # pragma: no cover - 替身无操作
        pass

    async def commit(self) -> None:  # pragma: no cover
        pass

    async def rollback(self) -> None:  # pragma: no cover
        pass


try:
    import offline_sync_service as svc  # 需要 backend 的 app 包可导入

    HAVE_APP = True
except Exception:  # pragma: no cover - 仅在缺依赖时跳过
    HAVE_APP = False


@pytest.fixture
def patched(monkeypatch):
    async def fake_find(db, merchant_id, client_id):
        return db.store.get(client_id)

    async def fake_conflict(db, payload, kind):
        return None

    # 用轻量替身替换「真正构造 ORM 对象」的步骤：参考实现的测试目标是验证
    # 「幂等控制流」（先查→建/重复→唯一约束兜底），而非 ORM 映射——后者在
    # PRD D3 的 client_id 迁移落地后由真实模型保证。
    def fake_build(*, merchant_id, client_id, kind, payload, source):
        return FakeRecord(client_id)

    def fake_audit(*, merchant_id, client_id, record_id, kind, result, device_fp="x"):
        return type("Audit", (), {"client_id": client_id, "id": record_id})()

    if HAVE_APP:
        monkeypatch.setattr(svc, "_find_by_client_id", fake_find)
        monkeypatch.setattr(svc, "_check_business_conflict", fake_conflict)
        monkeypatch.setattr(svc, "_build_record", fake_build)
        monkeypatch.setattr(svc, "_build_audit", fake_audit)
    yield


@pytest.mark.skipif(not HAVE_APP, reason="app 依赖不可导入，跳过集成测试")
@pytest.mark.asyncio
async def test_upsert_creates_then_duplicate(patched):
    db = FakeSession()
    mid = uuid.uuid4()
    r1 = await svc.upsert_offline_record(
        db, merchant_id=mid, client_id="c1", kind="cashier",
        payload={"total_amount": 15, "event_type": "sale", "quantity": 1},
    )
    assert r1.status == "created"
    assert r1.record_id is not None

    r2 = await svc.upsert_offline_record(
        db, merchant_id=mid, client_id="c1", kind="cashier",
        payload={"total_amount": 15, "event_type": "sale", "quantity": 1},
    )
    assert r2.status == "duplicate"  # 同 client_id 第二次 → 不重复写


@pytest.mark.skipif(not HAVE_APP, reason="app 依赖不可导入，跳过集成测试")
@pytest.mark.asyncio
async def test_upsert_concurrent_integrity_fallback(patched, monkeypatch):
    # 模拟「先查为空 → 插入时唯一约束被并发方触发 → 再查到并发方记录 → 视为 duplicate」
    db = FakeSession(raise_on_add=True)
    concurrent_record = FakeRecord("c1")

    calls = {"n": 0}

    async def fake_find_race(db, merchant_id, client_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # 第一次查：还没人写
        return concurrent_record  # 第二次查（except 里）：并发方已写入

    monkeypatch.setattr(svc, "_find_by_client_id", fake_find_race)  # 覆盖为竞态版本

    r = await svc.upsert_offline_record(
        db, merchant_id=uuid.uuid4(), client_id="c1", kind="cashier",
        payload={"total_amount": 15, "event_type": "sale", "quantity": 1},
    )
    assert r.status == "duplicate"
    assert str(r.record_id) == str(concurrent_record.id)
