from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contract_search import ContractSearch, ContractSearchParams
from excel_service import ExcelService
from upd_service import UpdService
from zakupki_client import ZakupkiClient


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

    @property
    def processed_contracts(self) -> int:
        return len(self.contracts)


class FullPipelineService:
    """
    Единый конвейер:
    поиск контрактов -> карточка/препараты -> UPD -> LP -> Excel.
    """

    def __init__(
        self,
        client: ZakupkiClient,
        *,
        output_dir: str | Path = "output",
    ) -> None:
        self.client = client
        self.search = ContractSearch(client)
        self.upd_service = UpdService(client)
        self.excel_service = ExcelService(output_dir=output_dir)

    def process_registry_numbers(
        self,
        registry_numbers: list[str],
        *,
        delay_between_contracts: float = 1.0,
    ) -> FullPipelineResult:
        result = FullPipelineResult()
        total = len(registry_numbers)

        for index, registry_number in enumerate(
            registry_numbers,
            start=1,
        ):
            print()
            print("=" * 100)
            print(
                f"[{index}/{total}] КОНТРАКТ "
                f"{registry_number}"
            )
            print("=" * 100)

            contract_info_id: str | None = None

            # ---------------------------------------------------------
            # 1. Карточка контракта + сведения о препаратах
            # ---------------------------------------------------------
            try:
                parsed = self.client.parse_contract(
                    registry_number
                )

                contract = dict(parsed["contract"])
                source = parsed.get("source", {})

                # Эти поля нужны внутри программы для диагностики.
                # ExcelService их на лист "Контракты" не выводит.
                contract["contract_info_id"] = source.get(
                    "contract_info_id"
                )
                contract["contract_info_pf_id"] = source.get(
                    "contract_info_pf_id"
                )
                contract["print_form_url"] = source.get(
                    "print_form_url"
                )

                contract_info_id = source.get(
                    "contract_info_id"
                )

                result.contracts.append(contract)
                result.medicines.extend(
                    parsed.get("medicines", [])
                )

                print(
                    "Карточка: OK | "
                    f"препаратов/позиций = "
                    f"{len(parsed.get('medicines', []))}"
                )

            except Exception as exc:
                result.errors.append(
                    PipelineError(
                        registry_number=registry_number,
                        stage="contract",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                print(
                    "Карточка: ОШИБКА | "
                    f"{type(exc).__name__}: {exc}"
                )

            # ---------------------------------------------------------
            # 2. UPD ZIP + основная UPD-таблица + LP-таблица
            #
            # Даже если печатная форма карточки дала ошибку,
            # пробуем обработать UPD независимо.
            # ---------------------------------------------------------
            try:
                upd_result = self.upd_service.parse_contract_upd(
                    registry_number,
                    contract_info_id=contract_info_id,
                )

                result.upd_rows.extend(upd_result.rows)
                result.lp_rows.extend(upd_result.lp_rows)

                for error in upd_result.errors:
                    result.errors.append(
                        PipelineError(
                            registry_number=registry_number,
                            stage="UPD/LP",
                            error=(
                                f"{error.archive_name}: "
                                f"{error.error}"
                            ),
                        )
                    )

                print(
                    "UPD/LP: "
                    f"ZIP = {upd_result.archives_found} | "
                    f"UPD строк = {len(upd_result.rows)} | "
                    f"LP строк = {len(upd_result.lp_rows)} | "
                    f"ошибок = {len(upd_result.errors)}"
                )

            except Exception as exc:
                result.errors.append(
                    PipelineError(
                        registry_number=registry_number,
                        stage="UPD/LP",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                print(
                    "UPD/LP: ОШИБКА | "
                    f"{type(exc).__name__}: {exc}"
                )

            if (
                delay_between_contracts > 0
                and index < total
            ):
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

        search_results = self.search.search(
            search_params,
            max_pages=max_pages,
        )

        registry_numbers = [
            item.registry_number
            for item in search_results
        ]

        if max_contracts is not None:
            registry_numbers = registry_numbers[:max_contracts]

        print(
            "Найдено контрактов для полного парсинга:",
            len(registry_numbers),
        )

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
        return self.excel_service.export(
            result,
            filename=filename,
            upd_rows=result.upd_rows,
            lp_rows=result.lp_rows,
        )

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

        excel_path = self.export_excel(
            result,
            filename=filename,
        )

        return result, excel_path