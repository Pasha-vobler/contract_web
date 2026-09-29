from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from zakupki_client import BASE_URL, ZakupkiClient


ROOT_PATH = "/epz/nsi/kladr/openModal.html"
CHILDREN_PATH = "/epz/api/nsi/kladr/loadChildren.html"


class KladrServiceError(RuntimeError):
    """Ошибка получения справочника местоположений ЕИС."""


class KladrService:
    """
    Работа со справочником "Местоположение заказчика" ЕИС.

    Корневой список федеральных округов извлекается из того же
    HTML-модального окна, которое использует zakupki.gov.ru.

    Регионы выбранного округа загружаются через официальный
    loadChildren.html endpoint ЕИС.
    """

    ROOT_PARAMS = {
        "insertCallback": "epzCommonChooseOkpd.kladrCustomerModal.add",
        "selectedCallback": (
            "epzCommonChooseOkpd.kladrCustomerModal.getSelectedIds"
        ),
        "hideCheckbox": "false",
        "selectedCodes": "",
        "levelsNumber": "2",
    }

    CODE_PATTERN = re.compile(
        r"^(?:OKER\d{2}|\d{11})$",
        flags=re.I,
    )

    def __init__(self, client: ZakupkiClient) -> None:
        self.client = client

    @classmethod
    def validate_code(cls, code: str) -> str:
        normalized = code.strip()

        if not cls.CODE_PATTERN.fullmatch(normalized):
            raise ValueError(
                f"Некорректный код местоположения: {code}"
            )

        return normalized

    @staticmethod
    def _normalize_items(
        raw_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        for item in raw_items:
            code = str(
                item.get("code")
                or item.get("key")
                or ""
            ).strip()

            name = str(
                item.get("name")
                or item.get("title")
                or ""
            ).strip()

            if not code or not name:
                continue

            result.append(
                {
                    "code": code,
                    "name": name,
                    "isFolder": bool(
                        item.get("isFolder")
                        or item.get("isLazy")
                    ),
                }
            )

        return result

    def get_root(self) -> list[dict[str, Any]]:
        response = self.client._get(
            urljoin(BASE_URL, ROOT_PATH),
            params=self.ROOT_PARAMS,
        )

        html = response.text

        match = re.search(
            r"function\s+getNewChildren\(\)\s*\{"
            r".*?return\s+(\[\{.*?\}\]);",
            html,
            flags=re.I | re.S,
        )

        if not match:
            raise KladrServiceError(
                "Не удалось извлечь список федеральных округов "
                "из ответа ЕИС."
            )

        try:
            raw_items = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise KladrServiceError(
                "ЕИС вернула некорректный список федеральных округов."
            ) from exc

        return self._normalize_items(raw_items)

    def get_children(
        self,
        code: str,
    ) -> list[dict[str, Any]]:
        normalized_code = self.validate_code(code)

        response = self.client._get(
            urljoin(BASE_URL, CHILDREN_PATH),
            params={
                "levelsNumber": "2",
                "code": normalized_code,
            },
        )

        try:
            raw_items = response.json()
        except Exception:
            try:
                raw_items = json.loads(response.text)
            except json.JSONDecodeError as exc:
                raise KladrServiceError(
                    "ЕИС вернула некорректный JSON "
                    f"для кода {normalized_code}."
                ) from exc

        if not isinstance(raw_items, list):
            raise KladrServiceError(
                "Неожиданный формат ответа ЕИС "
                f"для кода {normalized_code}."
            )

        return self._normalize_items(raw_items)
