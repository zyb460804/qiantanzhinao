"""
idempotency.py — 离线记账幂等决策（纯函数，零依赖，可单测）
────────────────────────────────────────────────────────────────────────
对应 PRD §4.1.2。把「这条离线记账要不要写库 / 是否重复 / 是否冲突」的决策，
从数据库耦合的代码里抽成纯函数：

  - 零依赖 → 不连库也能单测（见 test_offline_sync.py）；
  - 可复用 → 小程序端也能 import 同一份判定，避免「服务端一套、客户端一套」漂移（D2）；
  - 易评审 → 幂等核心逻辑一眼可见，代码评审聚焦真正需要人脑判断的部分。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass
class SyncResult:
    client_id: str
    status: str  # 'created' | 'duplicate' | 'conflict'
    record_id: uuid.UUID | None = None
    message: str = ""


def is_valid_uuid_v4(value: str) -> bool:
    """校验 client_id 是否为合法 UUID v4（客户端生成幂等键的契约）。"""
    return bool(_UUID_V4_RE.match(value or ""))


def decide_upsert(existing: object | None, incoming: dict) -> str:
    """给定「已存在的记录」与「本次入队」，返回处置决策。

    - existing 存在            → 'duplicate'（返回既存，不重复处理）
    - existing 不存在 & 无冲突 → 'create'
    - incoming 触发业务冲突     → 'conflict'（不静默写入，交冲突中心 R6）
    """
    if existing is not None:
        return "duplicate"
    if incoming.get("_conflict"):
        return "conflict"
    return "create"
