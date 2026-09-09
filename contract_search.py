from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from zakupki_client import BASE_URL, ZakupkiClient, ZakupkiClientError


SEARCH_PATH = "/epz/contract/search/results.html"


@dataclass(slots=True)
class ContractSearchParams:
    search_string: str
    date_from: str
    date_to: str
    records_per_page: int = 10
    drug_znvlp: bool = True

    def validate(self) -> None:
        if not self.search_string.strip():
            raise ValueError("Поисковое слово не должно быть пустым.")

        try:
            date_from = datetime.strptime(self.date_from, "%d.%m.%Y")
            date_to = datetime.strptime(self.date_to, "%d.%m.%Y")
        except ValueError as exc:
            raise ValueError(
                "Дата должна быть в формате ДД.ММ.ГГГГ, например 01.06.2025."
            ) from exc

        if date_from > date_to:
            raise ValueError("Дата начала не может быть позже даты окончания.")

        if self.records_per_page not in (10, 20, 50):
            raise ValueError(
                "Для тестовой версии records_per_page должен быть 10, 20 или 50."
            )

    def to_query_params(self, page_number: int) -> dict[str, str]:
        self.validate()

        params = {
            "searchString": self.search_string.strip(),
            "morphology": "on",
            "search-filter": "Дате размещения",
            "fz44": "on",
            "contractStageList_1": "on",
            "contractStageList": "1",
            "budgetLevelsIdNameHidden": "{}",
            "publishDateFrom": self.date_from,
            "publishDateTo": self.date_to,
            "subjectDrug": "on",
            "sortBy": "UPDATE_DATE",
            "pageNumber": str(page_number),
            "sortDirection": "false",
            "recordsPerPage": f"_{self.records_per_page}",
            "showLotsInfoHidden": "false",
        }

        # Фильтр:
        # "Лекарственный препарат включен в перечень ЖНВЛП".
        # Если галочка снята, параметр drugZnvlp вообще не
        # отправляется в ЕИС.
        if self.drug_znvlp:
            params["drugZnvlp"] = "on"

        return params


@dataclass(slots=True)
class ContractSearchResult:
    registry_number: str
    common_info_url: str
    page_number: int


class ContractSearch:
    """Поиск контрактов ЕИС с нашими фиксированными фильтрами."""

    def __init__(self, client: ZakupkiClient) -> None:
        self.client = client

    @staticmethod
    def _registry_number_from_url(url: str) -> str | None:
        values = parse_qs(urlparse(url).query).get("reestrNumber")
        if not values:
            return None

        registry_number = values[0].strip()
        return registry_number if registry_number.isdigit() else None

    def parse_search_page(
        self,
        html: str,
        page_number: int,
    ) -> list[ContractSearchResult]:
        soup = BeautifulSoup(html, "html.parser")

        results: list[ContractSearchResult] = []
        seen: set[str] = set()

        # Основной способ: ссылки на common-info карточки контрактов.
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")

            if "reestrNumber=" not in href:
                continue

            if "/epz/contract/contractCard/" not in href:
                continue

            registry_number = self._registry_number_from_url(href)
            if not registry_number or registry_number in seen:
                continue

            seen.add(registry_number)

            results.append(
                ContractSearchResult(
                    registry_number=registry_number,
                    common_info_url=urljoin(BASE_URL, href),
                    page_number=page_number,
                )
            )

        # Fallback на случай изменения структуры ссылок.
        if not results:
            for registry_number in re.findall(
                r"reestrNumber=(\d+)",
                html,
                flags=re.I,
            ):
                if registry_number in seen:
                    continue

                seen.add(registry_number)
                results.append(
                    ContractSearchResult(
                        registry_number=registry_number,
                        common_info_url=(
                            f"{BASE_URL}/epz/contract/contractCard/"
                            f"common-info.html?reestrNumber={registry_number}"
                        ),
                        page_number=page_number,
                    )
                )

        return results

    def fetch_page(
        self,
        search_params: ContractSearchParams,
        page_number: int = 1,
    ) -> tuple[list[ContractSearchResult], str]:
        response = self.client._get(
            urljoin(BASE_URL, SEARCH_PATH),
            params=search_params.to_query_params(page_number),
        )

        results = self.parse_search_page(
            response.text,
            page_number=page_number,
        )

        return results, response.url

    def search(
        self,
        search_params: ContractSearchParams,
        *,
        max_pages: int | None = None,
    ) -> list[ContractSearchResult]:
        """Получает все уникальные reestrNumber из поисковой выдачи.

        Остановка:
        - страница пустая;
        - страница не дала новых контрактов;
        - найдено меньше записей, чем records_per_page;
        - достигнут max_pages, если он задан.
        """
        search_params.validate()

        all_results: list[ContractSearchResult] = []
        seen_registry_numbers: set[str] = set()

        page_number = 1

        while True:
            if max_pages is not None and page_number > max_pages:
                break

            page_results, _ = self.fetch_page(
                search_params,
                page_number=page_number,
            )

            if not page_results:
                break

            new_results = [
                item
                for item in page_results
                if item.registry_number not in seen_registry_numbers
            ]

            if not new_results:
                break

            for item in new_results:
                seen_registry_numbers.add(item.registry_number)
                all_results.append(item)

            if len(page_results) < search_params.records_per_page:
                break

            page_number += 1

        return all_results
