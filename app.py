from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates

from contract_search import ContractSearchParams
from pipeline_service import FullPipelineService
from zakupki_client import ZakupkiClient


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates")
)

app = FastAPI(
    title="Парсер государственных закупок лекарственных препаратов",
    version="0.1.0",
)


def _verify_ssl_setting() -> bool:
    """
    На текущем локальном компьютере ЕИС требует verify_ssl=False.
    Для сервера позже лучше подключить корректный CA bundle.
    """
    value = os.getenv(
        "ZAKUPKI_VERIFY_SSL",
        "false",
    ).strip().lower()

    return value not in {
        "0",
        "false",
        "no",
        "off",
    }


def _html_date_to_eis(value: str) -> str:
    parsed = datetime.strptime(
        value,
        "%Y-%m-%d",
    )
    return parsed.strftime("%d.%m.%Y")


def _optional_positive_int(
    value: str | None,
    *,
    field_name: str,
) -> int | None:
    if value is None:
        return None

    value = value.strip()

    if not value:
        return None

    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name}: требуется целое число."
        ) from exc

    if number <= 0:
        raise ValueError(
            f"{field_name}: число должно быть больше нуля."
        )

    return number


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "defaults": {
                "search_string": "Вориконазол",
                "date_from": "2025-06-01",
                "date_to": "2026-06-01",
                "records_per_page": "50",
                "drug_znvlp": True,
                # Пусто = обработать все найденные контракты.
                "max_contracts": "",
            }
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
    max_contracts: str = Form(""),
):
    try:
        search_string = search_string.strip()

        if not search_string:
            raise ValueError(
                "Введите наименование препарата или поисковую строку."
            )

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
        )

    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "message": str(exc),
            },
            status_code=400,
        )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    filename = f"result_{timestamp}.xlsx"

    try:
        with ZakupkiClient(
            verify_ssl=_verify_ssl_setting()
        ) as client:
            pipeline = FullPipelineService(
                client,
                output_dir=OUTPUT_DIR,
            )

            result, excel_path = (
                pipeline.search_process_and_export(
                    params,
                    filename=filename,
                    # None = пройти всю поисковую выдачу ЕИС
                    # до последней страницы.
                    max_pages=None,
                    max_contracts=resolved_max_contracts,
                    delay_between_contracts=1.0,
                )
            )

    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "message": (
                    f"{type(exc).__name__}: {exc}"
                ),
            },
            status_code=500,
        )

    return templates.TemplateResponse(
        request=request,
        name="result.html",
        context={
            "search_string": search_string,
            "contracts_count": len(result.contracts),
            "medicines_count": len(result.medicines),
            "upd_count": len(result.upd_rows),
            "lp_count": len(result.lp_rows),
            "errors": result.errors,
            "errors_count": len(result.errors),
            "filename": excel_path.name,
        },
    )


@app.get("/download/{filename}")
def download_result(filename: str):
    # Защита от ../ и попыток выйти из output.
    safe_name = Path(filename).name
    file_path = OUTPUT_DIR / safe_name

    if (
        safe_name != filename
        or not file_path.is_file()
        or file_path.suffix.lower() != ".xlsx"
    ):
        raise HTTPException(
            status_code=404,
            detail="Файл не найден.",
        )

    return FileResponse(
        path=file_path,
        filename=safe_name,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
    }
