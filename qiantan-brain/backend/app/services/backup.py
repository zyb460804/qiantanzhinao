"""Database backup service — creates and rotates database backups.

Supports SQLite (file copy) and PostgreSQL (pg_dump) backends.
Rotates old backups based on retention configuration.
"""

import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def _extract_sqlite_path(database_url: str) -> str | None:
    """Extract the file path from a SQLite database URL.

    Handles both absolute and relative paths:
      - sqlite+aiosqlite:///./qiantan_dev.db
      - sqlite+aiosqlite:///E:/absolute/path/qiantan_dev.db
    """
    # Strip prefix: "sqlite+aiosqlite:///" or "sqlite:///"
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if database_url.startswith(prefix):
            return database_url[len(prefix):]
    return None


def _pg_dump_available() -> bool:
    """Check whether pg_dump executable is on PATH."""
    return shutil.which("pg_dump") is not None


async def run_database_backup() -> dict:
    """Run a database backup, returning a status dict.

    For SQLite: copies the database file with a timestamp suffix.
    For PostgreSQL: runs pg_dump (skips gracefully if pg_dump not found).
    Rotates backups older than retention period.
    """
    backup_dir = Path(settings.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    if settings.db_backend == "sqlite":
        return await _backup_sqlite(backup_dir, timestamp)
    elif settings.db_backend == "postgresql":
        return await _backup_postgres(backup_dir, timestamp)
    else:
        return {"ok": False, "error": f"Unsupported db_backend: {settings.db_backend}"}


async def _backup_sqlite(backup_dir: Path, timestamp: str) -> dict:
    """Copy SQLite database file and rotate old backups."""
    db_path_str = _extract_sqlite_path(settings.database_url)
    if not db_path_str:
        return {"ok": False, "error": f"Cannot parse SQLite path from: {settings.database_url}"}

    source = Path(db_path_str)

    # Resolve relative paths relative to the backend directory
    if not source.is_absolute():
        backend_dir = Path(__file__).resolve().parent.parent.parent
        source = backend_dir / db_path_str.lstrip("./")

    if not source.exists():
        return {"ok": False, "error": f"Database file not found: {source}"}

    dest = backup_dir / f"qiantan-sqlite-{timestamp}.db"
    shutil.copy2(source, dest)
    file_size = dest.stat().st_size

    # Rotate old backups
    _rotate_backups(backup_dir, "qiantan-sqlite-", ".db")

    logger.info("SQLite backup created: %s (%d bytes)", dest, file_size)
    return {"ok": True, "backend": "sqlite", "file": str(dest), "size_bytes": file_size}


async def _backup_postgres(backup_dir: Path, timestamp: str) -> dict:
    """Run pg_dump to create a SQL dump, rotating old backups."""
    if not _pg_dump_available():
        msg = "pg_dump not found on PATH — skipping PostgreSQL backup"
        logger.warning(msg)
        return {"ok": False, "error": msg}

    dest = backup_dir / f"qiantan-postgres-{timestamp}.sql.gz"

    try:
        result = subprocess.run(
            [
                "pg_dump",
                settings.database_url,
                "--no-owner",
                "--no-acl",
                "--compress=9",
                f"--file={dest}",
            ],
            capture_output=True,
            text=True,
            timeout=300,  # 5-minute timeout for large databases
        )

        if result.returncode != 0:
            logger.error("pg_dump failed: %s", result.stderr)
            return {"ok": False, "error": result.stderr.strip()}

        file_size = dest.stat().st_size if dest.exists() else 0

        # Rotate old backups
        _rotate_backups(backup_dir, "qiantan-postgres-", ".sql.gz")

        logger.info("PostgreSQL backup created: %s (%d bytes)", dest, file_size)
        return {"ok": True, "backend": "postgresql", "file": str(dest), "size_bytes": file_size}

    except FileNotFoundError:
        msg = (
            "pg_dump executable not found — install PostgreSQL client tools "
            "or set DB_BACKEND=sqlite for file-based backup"
        )
        logger.warning(msg)
        return {"ok": False, "error": msg}
    except subprocess.TimeoutExpired:
        msg = "pg_dump timed out after 300 seconds"
        logger.error(msg)
        return {"ok": False, "error": msg}


def _rotate_backups(base_dir: Path, prefix: str, suffix: str) -> None:
    """Delete backup files older than the configured retention period."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.backup_retention_daily)
    deleted = 0

    for f in base_dir.glob(f"{prefix}*{suffix}"):
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            try:
                f.unlink()
                deleted += 1
            except OSError as exc:
                logger.warning("Failed to delete old backup %s: %s", f, exc)

    if deleted > 0:
        logger.info("Rotated %d old backup(s) older than %d days", deleted, settings.backup_retention_daily)