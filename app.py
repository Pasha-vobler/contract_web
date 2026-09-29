from __future__ import annotations

import logging
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from contract_search import ContractSearchParams
from job_manager import JobManager, ProgressCallback
from kladr_service import KladrService
from pipeline_service import FullPipelineService
from zakupki_client import EISTemporaryBlockError, ZakupkiClient


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
job_manager = JobManager()

app = FastAPI(
    title="Парсер государственных закупок лекарственных препаратов",
    version="0.2.0",
)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value and value.strip() else default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value and value.strip() else default


def _verify_ssl_setting() -> bool | str:
    if not _env_bool("ZAKUPKI_VERIFY_SSL", False):
        return False
    ca_bundle = os.getenv("REQUESTS_CA_BUNDLE", "").strip()
    return ca_bundle or True


def _make_client(
    progress_callback: ProgressCallback | None = None,
) -> ZakupkiClient:
    return ZakupkiClient(
        verify_ssl=_verify_ssl_setting(),
        block_retries=_env_int("ZAKUPKI_BLOCK_RETRIES", 3),
        min_request_interval=_env_float("ZAKUPKI_MIN_REQUEST_INTERVAL", 1.2),
        proactive_pause_every=_env_int("ZAKUPKI_PROACTIVE_PAUSE_EVERY", 40),
        proactive_pause_seconds=_env_float(
            "ZAKUPKI_PROACTIVE_PAUSE_SECONDS", 20.0
        ),
        pause_callback=progress_callback,
    )


def _html_date_to_eis(value: str) -> str:
    return datetime.strptime(value, "%Y-%m-%d").strftime("%d.%m.%Y")


def _optional_positive_int(
    value: str | None,
    *,
    field_name: str,
) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"{field_name}: требуется целое число.") from exc
    if number <= 0:
        raise ValueError(f"{field_name}: число должно быть больше нуля.")
    return number


def _execute_job(
    params: ContractSearchParams,
    max_contracts: int | None,
    filename: str,
    progress_callback: ProgressCallback,
) -> dict[str, Any]:
    """Background target. It owns one client and the existing pipeline."""
    with _make_client(progress_callback) as client:
        pipeline = FullPipelineService(
            client,
            output_dir=OUTPUT_DIR,
            progress_callback=progress_callback,
        )
        result, excel_path = pipeline.search_process_and_export(
            params,
            filename=filename,
            max_pages=None,
            max_contracts=max_contracts,
            delay_between_contracts=3.0,
        )

    if result.aborted_due_to_eis_block:
        raise EISTemporaryBlockError(result.abort_reason or "ЕИС ограничила запросы")

    return {
        "result_filename": excel_path.name,
        "medicines_count": len(result.medicines),
        "upd_count": len(result.upd_rows),
        "lp_count": len(result.lp_rows),
    }


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "active_job": job_manager.get_active_job(),
            "defaults": {
                "search_string": "Вориконазол",
                "date_from": "2025-06-01",
                "date_to": "2026-06-01",
                "records_per_page": "50",
                "drug_znvlp": True,
                "max_contracts": "",
            },
        },
    )


@app.post("/run")
def run_parser(
    request: Request,
    search_string: str = Form(...),
    date_from: str = Form(...),
    date_to: str = Form(...),
    records_per_page: int = Form(10),
    drug_znvlp: bool = Form(False),
    customer_places: list[str] | None = Form(None),
    max_contracts: str = Form(""),
):
    active_job = job_manager.get_active_job()
    if active_job is not None:
        return RedirectResponse(
            url=f"/jobs/{active_job['job_id']}",
            status_code=303,
        )

    try:
        search_string = search_string.strip()
        if not search_string:
            raise ValueError("Введите наименование препарата или поисковую строку.")
        resolved_max_contracts = _optional_positive_int(
            max_contracts,
            field_name="Максимум контрактов",
        )
        params = ContractSearchParams(
            search_string=search_string,
            date_from=_html_date_to_eis(date_from),
            date_to=_html_date_to_eis(date_to),
            records_per_page=records_per_page,
            drug_znvlp=drug_znvlp,
            customer_places=customer_places or [],
        )
        params.validate()
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={"message": str(exc)},
            status_code=400,
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"result_{timestamp}.xlsx"
    public_params = asdict(params)
    public_params["max_contracts"] = resolved_max_contracts

    job, created = job_manager.start_job(
        public_params,
        lambda callback: _execute_job(
            params,
            resolved_max_contracts,
            filename,
            callback,
        ),
    )
    logger.info(
        "job_id=%s POST /run created=%s",
        job["job_id"],
        created,
    )
    return RedirectResponse(url=f"/jobs/{job['job_id']}", status_code=303)


@app.get("/jobs/{job_id}")
def job_page(request: Request, job_id: str):
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена.")
    return templates.TemplateResponse(
        request=request,
        name="job.html",
        context={"job": job},
    )


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена.")
    return job


@app.get("/download/{filename}")
def download_result(filename: str):
    safe_name = Path(filename).name
    file_path = OUTPUT_DIR / safe_name
    if (
        safe_name != filename
        or not file_path.is_file()
        or file_path.suffix.lower() != ".xlsx"
    ):
        raise HTTPException(status_code=404, detail="Файл не найден.")
    return FileResponse(
        path=file_path,
        filename=safe_name,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )


@app.get("/api/customer-places/root")
def customer_places_root():
    try:
        with _make_client() as client:
            return KladrService(client).get_root()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Не удалось получить список местоположений ЕИС: "
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc


@app.get("/api/customer-places/children/{code}")
def customer_places_children(code: str):
    try:
        with _make_client() as client:
            return KladrService(client).get_children(code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Не удалось получить регионы ЕИС: "
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc


@app.get("/health")
def health():
    return {"status": "ok"}
