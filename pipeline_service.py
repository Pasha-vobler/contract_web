from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from contract_search import ContractSearch, ContractSearchParams
from excel_service import ExcelService
from upd_service import UpdService
from zakupki_client import EISTemporaryBlockError, ZakupkiClient


@dataclass(slots=True)
class PipelineError:
    registry_number: str
    stage: str
    error: str


@dataclass(slots=True)
class FullPipelineResult:
    contracts: list[dict[str, Any]] = field(default_factory=list)
    medicines: list[dict[str, Any]] = field(default_factory=list)
    upd_rows: list[dict[str, Any]] = field(default_factory=list)
    lp_rows: list[dict[str, Any]] = field(default_factory=list)
    errors: list[PipelineError] = field(default_factory=list)
    aborted_due_to_eis_block: bool = False
    abort_reason: str | None = None

    @property
    def processed_contracts(self) -> int:
        return len(self.contracts)


class FullPipelineService:
    """Search -> contract card -> UPD/LP -> Excel pipeline."""

    def __init__(
        self,
        client: ZakupkiClient,
        *,
        output_dir: str | Path = "output",
        progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.client = client
        self.search = ContractSearch(client)
        self.upd_service = UpdService(client)
        self.excel_service = ExcelService(output_dir=output_dir)
        self.progress_callback = progress_callback

    def _progress(self, event: str, **data: Any) -> None:
        if self.progress_callback is not None:
            self.progress_callback(event, data)

    def _record_warning(
        self,
        result: FullPipelineResult,
        registry_number: str,
        stage: str,
        error: str,
    ) -> None:
        result.errors.append(PipelineError(registry_number, stage, error))
        self._progress(
            "warning",
            warning={
                "registry_number": registry_number,
                "stage": stage,
                "error": error,
            },
        )

    @staticmethod
    def _record_eis_block(
        result: FullPipelineResult,
        registry_number: str,
        exc: EISTemporaryBlockError,
    ) -> None:
        result.aborted_due_to_eis_block = True
        result.abort_reason = str(exc)
        result.errors.append(PipelineError(registry_number, "EIS BLOCK", str(exc)))
        print()
        print("#" * 100)
        print("МАССОВАЯ ОБРАБОТКА ОСТАНОВЛЕНА: ЕИС не сняла временную блокировку.")
        print("Следующие ZIP и контракты запрашиваться НЕ будут.")
        print("#" * 100)

    def process_registry_numbers(
        self,
        registry_numbers: list[str],
        *,
        delay_between_contracts: float = 1.0,
    ) -> FullPipelineResult:
        result = FullPipelineResult()
        total = len(registry_numbers)

        for index, registry_number in enumerate(registry_numbers, start=1):
            self._progress(
                "contract_started",
                index=index,
                total=total,
                registry_number=registry_number,
                stage="Карточка контракта",
            )
            print()
            print("=" * 100)
            print(f"[{index}/{total}] КОНТРАКТ {registry_number}")
            print("=" * 100)
            contract_info_id: str | None = None

            try:
                self._progress("stage_changed", stage="Карточка контракта")
                parsed = self.client.parse_contract(registry_number)
                contract = dict(parsed["contract"])
                source = parsed.get("source", {})
                contract["contract_info_id"] = source.get("contract_info_id")
                contract["contract_info_pf_id"] = source.get("contract_info_pf_id")
                contract["print_form_url"] = source.get("print_form_url")
                contract_info_id = source.get("contract_info_id")
                result.contracts.append(contract)
                result.medicines.extend(parsed.get("medicines", []))
                print(
                    "Карточка: OK | препаратов/позиций = "
                    f"{len(parsed.get('medicines', []))}"
                )
            except EISTemporaryBlockError as exc:
                self._record_eis_block(result, registry_number, exc)
                break
            except Exception as exc:
                error_text = f"{type(exc).__name__}: {exc}"
                self._record_warning(result, registry_number, "contract", error_text)
                print(f"Карточка: ОШИБКА | {error_text}")

            # UPD/LP remains independent: it is attempted even if the card
            # failed, matching the original pipeline behavior.
            try:
                self._progress("stage_changed", stage="UPD/LP")
                upd_result = self.upd_service.parse_contract_upd(
                    registry_number,
                    contract_info_id=contract_info_id,
                )
                result.upd_rows.extend(upd_result.rows)
                result.lp_rows.extend(upd_result.lp_rows)

                for error in upd_result.errors:
                    self._record_warning(
                        result,
                        registry_number,
                        "UPD/LP",
                        f"{error.archive_name}: {error.error}",
                    )

                print(
                    "UPD/LP: "
                    f"ZIP = {upd_result.archives_found} | "
                    f"UPD строк = {len(upd_result.rows)} | "
                    f"LP строк = {len(upd_result.lp_rows)} | "
                    f"ошибок = {len(upd_result.errors)}"
                )
            except EISTemporaryBlockError as exc:
                self._record_eis_block(result, registry_number, exc)
                break
            except Exception as exc:
                error_text = f"{type(exc).__name__}: {exc}"
                self._record_warning(result, registry_number, "UPD/LP", error_text)
                print(f"UPD/LP: ОШИБКА | {error_text}")

            # Moving to the next contract means the current one is processed,
            # even when it produced a non-fatal warning.
            self._progress(
                "contract_finished",
                index=index,
                total=total,
                registry_number=registry_number,
            )
            if delay_between_contracts > 0 and index < total:
                time.sleep(delay_between_contracts)

        return result

    def search_and_process(
        self,
        search_params: ContractSearchParams,
        *,
        max_pages: int | None = None,
        max_contracts: int | None = None,
        delay_between_contracts: float = 1.0,
    ) -> FullPipelineResult:
        print("Получаем список контрактов из поисковой выдачи...")
        self._progress("search_started")
        search_results = self.search.search(search_params, max_pages=max_pages)
        registry_numbers = [item.registry_number for item in search_results]
        if max_contracts is not None:
            registry_numbers = registry_numbers[:max_contracts]
        print("Найдено контрактов для полного парсинга:", len(registry_numbers))
        self._progress("search_finished", total=len(registry_numbers))
        return self.process_registry_numbers(
            registry_numbers,
            delay_between_contracts=delay_between_contracts,
        )

    def export_excel(
        self,
        result: FullPipelineResult,
        *,
        filename: str = "result.xlsx",
    ) -> Path:
        self._progress("excel_started")
        path = self.excel_service.export(
            result,
            filename=filename,
            upd_rows=result.upd_rows,
            lp_rows=result.lp_rows,
        )
        self._progress("excel_finished", filename=path.name)
        return path

    def search_process_and_export(
        self,
        search_params: ContractSearchParams,
        *,
        filename: str = "result.xlsx",
        max_pages: int | None = None,
        max_contracts: int | None = None,
        delay_between_contracts: float = 1.0,
    ) -> tuple[FullPipelineResult, Path]:
        result = self.search_and_process(
            search_params,
            max_pages=max_pages,
            max_contracts=max_contracts,
            delay_between_contracts=delay_between_contracts,
        )
        excel_path = self.export_excel(result, filename=filename)
        return result, excel_path
