from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from contract_search import ContractSearch, ContractSearchParams
from zakupki_client import ZakupkiClient, ZakupkiClientError


@dataclass(slots=True)
class ContractParseError:
    registry_number: str
    error: str


@dataclass(slots=True)
class BatchParseResult:
    contracts: list[dict[str, Any]] = field(default_factory=list)
    medicines: list[dict[str, Any]] = field(default_factory=list)
    errors: list[ContractParseError] = field(default_factory=list)


class ParsingService:
    """Связывает поиск контрактов и парсинг их печатных форм."""

    def __init__(self, client: ZakupkiClient) -> None:
        self.client = client
        self.search_client = ContractSearch(client)

    def parse_registry_numbers(
        self,
        registry_numbers: list[str],
        *,
        delay_between_contracts: float = 1.0,
    ) -> BatchParseResult:
        result = BatchParseResult()

        total = len(registry_numbers)

        for index, registry_number in enumerate(registry_numbers, start=1):
            print(
                f"[{index}/{total}] Парсим контракт {registry_number}..."
            )

            try:
                parsed = self.client.parse_contract(registry_number)

                contract = dict(parsed["contract"])
                source = dict(parsed.get("source", {}))

                # Служебные ID оставляем у контракта: позже пригодятся
                # для диагностики и работы с документами/УПД.
                contract["contract_info_id"] = source.get("contract_info_id")
                contract["contract_info_pf_id"] = source.get(
                    "contract_info_pf_id"
                )
                contract["print_form_url"] = source.get("print_form_url")

                result.contracts.append(contract)
                result.medicines.extend(parsed["medicines"])

                print(
                    f"    OK: препаратов/позиций = "
                    f"{len(parsed['medicines'])}"
                )

            except (ZakupkiClientError, ValueError, KeyError) as exc:
                result.errors.append(
                    ContractParseError(
                        registry_number=registry_number,
                        error=str(exc),
                    )
                )
                print(f"    ОШИБКА: {exc}")

            except Exception as exc:
                # На тестовом этапе не даём одной неожиданной ошибке
                # остановить весь пакет. Ошибка сохраняется отдельно.
                result.errors.append(
                    ContractParseError(
                        registry_number=registry_number,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                print(
                    f"    НЕОЖИДАННАЯ ОШИБКА: "
                    f"{type(exc).__name__}: {exc}"
                )

            if (
                delay_between_contracts > 0
                and index < total
            ):
                time.sleep(delay_between_contracts)

        return result

    def search_and_parse(
        self,
        search_params: ContractSearchParams,
        *,
        max_pages: int | None = None,
        max_contracts: int | None = None,
        delay_between_contracts: float = 1.0,
    ) -> BatchParseResult:
        print("Получаем список контрактов из поисковой выдачи...")

        search_results = self.search_client.search(
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
            f"Найдено контрактов для парсинга: "
            f"{len(registry_numbers)}"
        )
        print()

        return self.parse_registry_numbers(
            registry_numbers,
            delay_between_contracts=delay_between_contracts,
        )