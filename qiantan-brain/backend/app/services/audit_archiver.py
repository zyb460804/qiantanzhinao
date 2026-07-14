"""Audit log archiving service — periodically archives old audit logs to compressed files."""

import gzip
import json
import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.timezone import utc_now
from app.models.admin_audit import AdminAuditLog

logger = logging.getLogger(__name__)


async def archive_old_audit_logs(db: AsyncSession) -> dict:
    """Archive audit logs older than audit_archive_days to compressed JSONL files.

    Deletes archived records after successful write.
    Returns a status dict with count and file path.
    """
    cutoff = utc_now() - timedelta(days=settings.audit_archive_days)

    # Count records to archive
    count_result = await db.execute(
        select(func.count(AdminAuditLog.id)).where(AdminAuditLog.created_at < cutoff)
    )
    count = count_result.scalar_one()

    if count == 0:
        return {"archived": 0, "message": "No logs to archive"}

    # Fetch records to archive (limit batch size to avoid memory pressure)
    result = await db.execute(
        select(AdminAuditLog)
        .where(AdminAuditLog.created_at < cutoff)
        .limit(10000)
    )
    records = result.scalars().all()

    if not records:
        return {"archived": 0, "message": "No logs to archive"}

    # Write to archive file
    archive_dir = Path("./archives/audit")
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_file = archive_dir / f"audit-{utc_now().strftime('%Y%m%d-%H%M%S')}.jsonl.gz"

    with gzip.open(archive_file, "wt", encoding="utf-8") as f:
        for r in records:
            f.write(
                json.dumps(
                    {
                        "id": str(r.id),
                        "admin_id": r.admin_id,
                        "admin_email": r.admin_email,
                        "action": r.action,
                        "resource_type": r.resource_type,
                        "resource_id": r.resource_id,
                        "detail": r.detail,
                        "ip_address": r.ip_address,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    # Delete archived records
    archived_ids = [r.id for r in records]
    await db.execute(delete(AdminAuditLog).where(AdminAuditLog.id.in_(archived_ids)))
    await db.commit()

    logger.info("Archived %d audit logs to %s", len(records), archive_file)
    return {"archived": len(records), "file": str(archive_file)}
