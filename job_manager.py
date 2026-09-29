from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, dict[str, Any]], None]
JobTarget = Callable[[ProgressCallback], dict[str, Any] | None]


STATUS_LABELS = {
    "queued": "Ожидание",
    "searching": "Поиск контрактов",
    "parsing": "Парсинг",
    "paused": "Пауза из-за ограничения ЕИС",
    "excel": "Формирование Excel",
    "completed": "Завершено",
    "error": "Ошибка",
}

ACTIVE_STATUSES = {"queued", "searching", "parsing", "paused", "excel"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class Job:
    job_id: str
    search_params: dict[str, Any]
    status: str = "queued"
    created_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    total_contracts: int = 0
    processed_contracts: int = 0
    current_contract_index: int = 0
    current_registry_number: str | None = None
    current_stage: str | None = None
    error_count: int = 0
    warning_count: int = 0
    result_filename: str | None = None
    fatal_error: str | None = None
    pause_reason: str | None = None
    retry_after_seconds: float | None = None
    retry_at: datetime | None = None
    medicines_count: int = 0
    upd_count: int = 0
    lp_count: int = 0
    warnings: list[dict[str, str]] = field(default_factory=list)
    _resume_status: str = "parsing"


class JobManager:
    """Thread-safe in-memory job registry with one active bulk job."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._active_job_id: str | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="eis-job",
        )

    def start_job(
        self,
        search_params: dict[str, Any],
        target: JobTarget,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically returns the active job or starts one new job."""
        with self._lock:
            active = self._active_job_locked()
            if active is not None:
                return self._snapshot_locked(active), False

            job = Job(
                job_id=uuid.uuid4().hex,
                search_params=dict(search_params),
            )
            self._jobs[job.job_id] = job
            self._active_job_id = job.job_id
            self._executor.submit(self._run_job, job.job_id, target)
            logger.info("job_id=%s queued", job.job_id)
            return self._snapshot_locked(job), True

    def _run_job(self, job_id: str, target: JobTarget) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.started_at = _now()

        try:
            completion = target(
                lambda event, data=None: self.report(
                    job_id,
                    event,
                    **(data or {}),
                )
            ) or {}

            with self._lock:
                job = self._jobs[job_id]
                if job.status != "error":
                    job.status = "completed"
                    job.finished_at = _now()
                    job.current_stage = "Excel"
                    job.pause_reason = None
                    job.retry_after_seconds = None
                    job.retry_at = None
                    for key in (
                        "result_filename",
                        "medicines_count",
                        "upd_count",
                        "lp_count",
                    ):
                        if key in completion:
                            setattr(job, key, completion[key])
                logger.info("job_id=%s completed", job_id)
        except Exception as exc:
            logger.exception("job_id=%s failed", job_id)
            with self._lock:
                job = self._jobs[job_id]
                job.status = "error"
                job.finished_at = _now()
                job.fatal_error = f"{type(exc).__name__}: {exc}"
                job.error_count += 1
                job.pause_reason = None
                job.retry_after_seconds = None
                job.retry_at = None
        finally:
            with self._lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None

    def report(self, job_id: str, event: str, **data: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return

            if event == "search_started":
                job.status = "searching"
                job.current_stage = "Поиск контрактов"
            elif event == "search_finished":
                job.total_contracts = int(data.get("total", 0))
            elif event == "contract_started":
                job.status = "parsing"
                job.current_contract_index = int(data.get("index", 0))
                job.current_registry_number = data.get("registry_number")
                job.current_stage = data.get("stage", "Карточка контракта")
            elif event == "stage_changed":
                job.current_stage = data.get("stage")
                if job.status != "paused":
                    job.status = "parsing"
            elif event == "warning":
                job.warning_count += int(data.get("count", 1))
                warning = data.get("warning")
                if warning and len(job.warnings) < 100:
                    job.warnings.append(dict(warning))
            elif event == "contract_finished":
                index = int(data.get("index", job.current_contract_index))
                job.processed_contracts = max(job.processed_contracts, index)
            elif event == "excel_started":
                job.status = "excel"
                job.current_stage = "Excel"
            elif event == "pause_started":
                if job.status != "paused":
                    job._resume_status = job.status
                job.status = "paused"
                job.pause_reason = data.get("reason", "ЕИС временно ограничила запросы")
                job.retry_after_seconds = float(data.get("retry_after", 0))
                job.retry_at = data.get("retry_at")
            elif event == "pause_finished":
                job.status = job._resume_status
                job.pause_reason = None
                job.retry_after_seconds = None
                job.retry_at = None

            logger.info(
                "job_id=%s event=%s contract=%s stage=%s",
                job_id,
                event,
                job.current_registry_number or "-",
                job.current_stage or "-",
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return self._snapshot_locked(job) if job else None

    def get_active_job(self) -> dict[str, Any] | None:
        with self._lock:
            job = self._active_job_locked()
            return self._snapshot_locked(job) if job else None

    def _active_job_locked(self) -> Job | None:
        if self._active_job_id is None:
            return None
        job = self._jobs.get(self._active_job_id)
        if job is None or job.status not in ACTIVE_STATUSES:
            self._active_job_id = None
            return None
        return job

    def _snapshot_locked(self, job: Job) -> dict[str, Any]:
        data = asdict(job)
        data.pop("_resume_status", None)
        for name in ("created_at", "started_at", "finished_at", "retry_at"):
            value = data[name]
            data[name] = value.isoformat() if value else None
        data["status_label"] = STATUS_LABELS[job.status]
        data["remaining_contracts"] = max(
            job.total_contracts - job.processed_contracts,
            0,
        )
        data["progress_percent"] = (
            round(job.processed_contracts * 100 / job.total_contracts)
            if job.total_contracts
            else 0
        )
        data["is_active"] = job.status in ACTIVE_STATUSES
        data["download_url"] = (
            f"/download/{job.result_filename}"
            if job.result_filename
            else None
        )
        return data

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)
