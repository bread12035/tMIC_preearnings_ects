"""GCS-backed registry of scheduled earnings tasks.

Stored as one JSON file per (fiscal_year, fiscal_quarter). All mutations are
read-modify-write on the full JSON file; the per-quarter event count is small
enough that this is acceptable. Concurrency safety relies on calendar_sync's
``concurrencyPolicy: Forbid`` and the dispatcher running every 10 minutes
(it is idempotent for already-published tasks).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from common.exceptions import GCSObjectNotFound
from event_calendar.models import ScheduledTask

log = logging.getLogger(__name__)


class TaskRegistry:
    def __init__(self, gcs, bucket: str, prefix: str):
        self._gcs = gcs
        self._bucket = bucket
        self._prefix = prefix.rstrip("/")

    def _blob_path(self, fiscal_year: str, fiscal_quarter: str) -> str:
        return f"{self._prefix}/tasks_{fiscal_year}_{fiscal_quarter}.json"

    async def load(
        self, fiscal_year: str, fiscal_quarter: str
    ) -> list[ScheduledTask]:
        path = self._blob_path(fiscal_year, fiscal_quarter)
        try:
            raw = await self._gcs.read_json(self._bucket, path)
        except GCSObjectNotFound:
            return []
        return [ScheduledTask(**t) for t in raw]

    async def save(
        self,
        fiscal_year: str,
        fiscal_quarter: str,
        tasks: list[ScheduledTask],
    ) -> None:
        path = self._blob_path(fiscal_year, fiscal_quarter)
        await self._gcs.write_json(
            self._bucket, path, [t.model_dump() for t in tasks]
        )

    async def upsert(self, new_tasks: Iterable[ScheduledTask]) -> int:
        """Merge new_tasks into existing registry. task_id is the dedup key.

        Existing tasks are NEVER overwritten — their status/exec time stays put
        once created. Returns the count of newly inserted tasks.
        """
        groups: dict[tuple[str, str], list[ScheduledTask]] = {}
        for t in new_tasks:
            groups.setdefault((t.fiscal_year, t.fiscal_quarter), []).append(t)

        inserted = 0
        for (fy, fq), tasks in groups.items():
            existing = await self.load(fy, fq)
            existing_by_id = {t.task_id: t for t in existing}
            for task in tasks:
                if task.task_id not in existing_by_id:
                    existing_by_id[task.task_id] = task
                    inserted += 1
            await self.save(fy, fq, list(existing_by_id.values()))
        return inserted

    async def mark_status(
        self, task: ScheduledTask, status: str
    ) -> None:
        existing = await self.load(task.fiscal_year, task.fiscal_quarter)
        changed = False
        for t in existing:
            if t.task_id == task.task_id and t.status != status:
                t.status = status  # type: ignore[assignment]
                changed = True
        if changed:
            await self.save(task.fiscal_year, task.fiscal_quarter, existing)

    async def mark_published(self, task: ScheduledTask) -> None:
        await self.mark_status(task, "published")

    async def mark_skipped(self, task: ScheduledTask) -> None:
        await self.mark_status(task, "skipped")

    async def list_quarter_files(self) -> list[tuple[str, str]]:
        """Return (fiscal_year, fiscal_quarter) tuples for every registry blob."""
        prefix = f"{self._prefix}/tasks_"
        names = await self._gcs.list_blobs(self._bucket, prefix)
        out: list[tuple[str, str]] = []
        for n in names:
            stem = n.rsplit("/", 1)[-1]  # tasks_{year}_{quarter}.json
            if not stem.startswith("tasks_") or not stem.endswith(".json"):
                continue
            body = stem[len("tasks_") : -len(".json")]
            parts = body.split("_")
            if len(parts) != 2:
                continue
            out.append((parts[0], parts[1]))
        return out


def utcnow() -> datetime:
    """Naive UTC ``datetime`` — matches how event/exec times are stored."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
