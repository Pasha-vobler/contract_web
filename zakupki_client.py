from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import parse_qs, urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from contract_parser_stage1 import parse_contract_html


BASE_URL = "https://zakupki.gov.ru"
COMMON_INFO_PATH = "/epz/contract/contractCard/common-info.html"
DOCUMENT_INFO_PATH = "/epz/contract/contractCard/document-info.html"
PRINT_FORM_PATH = "/epz/contract/printForm/view.html"


class ZakupkiClientError(RuntimeError):
    """Ошибка получения данных с ЕИС."""


class EISTemporaryBlockError(ZakupkiClientError):
    """
    ЕИС продолжает возвращать 403/429 после длительных пауз.

    Такое исключение означает, что продолжать перебирать следующие
    контракты нельзя: это только усилит временную блокировку.
    """


@dataclass(slots=True)
class DownloadedContract:
    registry_number: str
    contract_info_id: str | None
    contract_info_pf_id: str
    print_form_url: str
    html: str


class ZakupkiClient:
    """HTTP-клиент для карточек контрактов ЕИС."""

    def __init__(
        self,
        timeout: tuple[float, float] = (10.0, 40.0),
        retries: int = 4,
        verify_ssl: bool | str = True,
        block_retries: int = 3,
        min_request_interval: float = 1.2,
        proactive_pause_every: int = 40,
        proactive_pause_seconds: float = 20.0,
        pause_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.timeout = timeout
        self.verify_ssl = verify_ssl

        # Сколько раз после 403/429 делать длинную паузу
        # и повторять ТОТ ЖЕ запрос.
        self.block_retries = block_retries

        # Минимальный интервал между любыми запросами.
        self.min_request_interval = min_request_interval

        # Профилактическая пауза после N запросов в рамках
        # одной массовой выгрузки.
        self.proactive_pause_every = proactive_pause_every
        self.proactive_pause_seconds = proactive_pause_seconds
        self.pause_callback = pause_callback

        self._last_request_started_at = 0.0
        self._request_count = 0

        if verify_ssl is False:
            urllib3.disable_warnings(
                urllib3.exceptions.InsecureRequestWarning
            )

        self.session = requests.Session()

        # 429 обрабатываем вручную ниже.
        # urllib3 оставляем только для временных 5xx/сетевых ошибок.
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            status=retries,
            backoff_factor=1.0,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )

        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=10,
            pool_maxsize=10,
        )

        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/152.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
        )

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "ZakupkiClient":
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        self.close()

    def _wait_before_request(self) -> None:
        """
        Глобальный троттлинг для текущего клиента.

        Пока этот метод спит, весь pipeline стоит на месте:
        следующий контракт не начинается.
        """
        if (
            self.proactive_pause_every > 0
            and self._request_count > 0
            and self._request_count % self.proactive_pause_every == 0
        ):
            print(
                "        Профилактическая пауза: "
                f"{self.proactive_pause_seconds:.0f} сек "
                f"после {self._request_count} запросов к ЕИС..."
            )
            time.sleep(self.proactive_pause_seconds)

        if self.min_request_interval > 0:
            elapsed = (
                time.monotonic()
                - self._last_request_started_at
            )
            wait_for = (
                self.min_request_interval
                - elapsed
            )

            if wait_for > 0:
                time.sleep(wait_for)

        self._request_count += 1

    @staticmethod
    def _retry_after_seconds(
        response: requests.Response,
    ) -> float | None:
        value = response.headers.get("Retry-After")
        if not value:
            return None

        value = value.strip()

        # Retry-After может быть количеством секунд.
        try:
            return max(0.0, float(value))
        except ValueError:
            pass

        # Или HTTP-date.
        try:
            retry_dt = parsedate_to_datetime(value)
            now_dt = parsedate_to_datetime(
                response.headers.get("Date", "")
            )
            if now_dt is not None:
                return max(
                    0.0,
                    (retry_dt - now_dt).total_seconds(),
                )
        except Exception:
            pass

        return None

    def _block_delay(
        self,
        status_code: int,
        attempt: int,
        response: requests.Response,
    ) -> float:
        """
        Длинная пауза при временной блокировке.

        429:
            60 -> 120 -> 180 секунд.
        403:
            120 -> 180 -> 300 секунд.

        Для 429 Retry-After имеет приоритет, если ЕИС его прислала.
        """
        if status_code == 429:
            retry_after = self._retry_after_seconds(
                response
            )

            if retry_after is not None:
                return (
                    max(5.0, retry_after)
                    + random.uniform(1.0, 3.0)
                )

            delays = (60.0, 120.0, 180.0)

        else:
            # Массовый 403 после серии успешных запросов у ЕИС
            # на практике означает более жёсткую временную блокировку.
            delays = (120.0, 180.0, 300.0)

        index = min(
            attempt - 1,
            len(delays) - 1,
        )

        return (
            delays[index]
            + random.uniform(1.0, 4.0)
        )

    def _request_get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        stream: bool = False,
    ) -> requests.Response:
        """
        Единая точка GET-запросов.

        ВАЖНО:
        при 403/429 не переходим к следующему контракту.
        Весь pipeline ждёт, затем повторяется тот же URL.
        """
        total_attempts = max(
            1,
            self.block_retries + 1,
        )

        last_status: int | None = None
        last_url = url

        for attempt in range(
            1,
            total_attempts + 1,
        ):
            self._wait_before_request()
            self._last_request_started_at = (
                time.monotonic()
            )

            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                    allow_redirects=True,
                    verify=self.verify_ssl,
                    stream=stream,
                )
            except requests.RequestException as exc:
                raise ZakupkiClientError(
                    "Не удалось выполнить запрос "
                    f"к ЕИС: {exc}"
                ) from exc

            last_status = response.status_code
            last_url = response.url

            if response.status_code not in {
                403,
                429,
            }:
                return response

            if attempt >= total_attempts:
                response.close()
                break

            delay = self._block_delay(
                response.status_code,
                attempt,
                response,
            )

            print()
            print(
                "!" * 88
            )
            print(
                f"ЕИС временно ограничила доступ "
                f"(HTTP {response.status_code})."
            )
            print(
                "ВЕСЬ ПАРСЕР ПРИОСТАНОВЛЕН. "
                "К следующим контрактам не переходим."
            )
            print(
                f"Ждём {delay:.0f} сек, затем повторяем "
                "тот же запрос."
            )
            print(
                f"Попытка после паузы: "
                f"{attempt + 1}/{total_attempts}"
            )
            print(
                "!" * 88
            )
            print()

            response.close()
            retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            if self.pause_callback is not None:
                self.pause_callback(
                    "pause_started",
                    {
                        "reason": (
                            "ЕИС временно ограничила запросы "
                            f"(HTTP {response.status_code})"
                        ),
                        "status_code": response.status_code,
                        "retry_after": delay,
                        "retry_at": retry_at,
                    },
                )
            try:
                time.sleep(delay)
            finally:
                if self.pause_callback is not None:
                    self.pause_callback("pause_finished", {})

        raise EISTemporaryBlockError(
            "ЕИС продолжает блокировать запросы "
            f"(последний HTTP {last_status}) после "
            f"{total_attempts} попыток: {last_url}. "
            "Массовая обработка остановлена, чтобы "
            "не усиливать блокировку."
        )

    def _get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> requests.Response:
        response = self._request_get(
            url,
            params=params,
            stream=False,
        )

        if response.status_code != 200:
            raise ZakupkiClientError(
                f"ЕИС вернула HTTP {response.status_code}: "
                f"{response.url}"
            )

        if not response.encoding:
            response.encoding = "utf-8"

        return response

    @staticmethod
    def _extract_query_value(
        url: str,
        name: str,
    ) -> str | None:
        values = parse_qs(urlparse(url).query).get(name)
        return values[0] if values else None

    def discover_contract_info_id(
        self,
        registry_number: str,
    ) -> str | None:
        response = self._get(
            urljoin(BASE_URL, COMMON_INFO_PATH),
            params={
                "reestrNumber": registry_number,
            },
        )

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        for link in soup.find_all("a", href=True):
            href = link.get("href", "")

            if "document-info.html" not in href:
                continue

            contract_info_id = self._extract_query_value(
                href,
                "contractInfoId",
            )

            if contract_info_id:
                return contract_info_id

        match = re.search(
            r"contractInfoId(?:=|%3D)(\d+)",
            response.text,
            flags=re.I,
        )

        return match.group(1) if match else None

    def get_document_info_html(
        self,
        registry_number: str,
        contract_info_id: str | None = None,
    ) -> tuple[str, str | None]:
        if contract_info_id is None:
            contract_info_id = (
                self.discover_contract_info_id(
                    registry_number
                )
            )

        params = {
            "reestrNumber": registry_number,
        }

        if contract_info_id:
            params["contractInfoId"] = (
                contract_info_id
            )

        response = self._get(
            urljoin(BASE_URL, DOCUMENT_INFO_PATH),
            params=params,
        )

        return response.text, contract_info_id

    @staticmethod
    def _find_print_form_candidates(
        html: str,
    ) -> list[tuple[str, str]]:
        soup = BeautifulSoup(
            html,
            "html.parser",
        )

        candidates: list[tuple[str, str]] = []
        seen_ids: set[str] = set()

        for link in soup.find_all("a", href=True):
            href = link.get("href", "")

            if (
                "/epz/contract/printForm/view.html"
                not in href
            ):
                continue

            pf_id = ZakupkiClient._extract_query_value(
                href,
                "contractInfoPfId",
            )

            if not pf_id or pf_id in seen_ids:
                continue

            seen_ids.add(pf_id)
            candidates.append(
                (
                    pf_id,
                    urljoin(BASE_URL, href),
                )
            )

        return candidates

    @staticmethod
    def _looks_like_contract_print_form(
        html: str,
    ) -> bool:
        required_markers = (
            "Информация о заключенном контракте",
            "Наименование заказчика",
            "Раздел II. Объект закупки",
            "Дополнительная информация о лекарственном препарате",
        )

        return all(
            marker in html
            for marker in required_markers
        )

    def find_actual_print_form(
        self,
        registry_number: str,
        contract_info_id: str | None = None,
    ) -> tuple[str, str | None, str, str]:
        (
            document_html,
            resolved_contract_info_id,
        ) = self.get_document_info_html(
            registry_number=registry_number,
            contract_info_id=contract_info_id,
        )

        candidates = (
            self._find_print_form_candidates(
                document_html
            )
        )

        if not candidates:
            raise ZakupkiClientError(
                "На странице 'Вложения' не найдены "
                "ссылки на печатную форму."
            )

        checked: list[str] = []

        for pf_id, print_form_url in reversed(
            candidates
        ):
            checked.append(pf_id)

            response = self._get(
                print_form_url
            )

            if self._looks_like_contract_print_form(
                response.text
            ):
                return (
                    response.url,
                    resolved_contract_info_id,
                    pf_id,
                    response.text,
                )

        raise ZakupkiClientError(
            "Не удалось найти подходящую "
            f"печатную форму для {registry_number}. "
            "Проверены contractInfoPfId: "
            + ", ".join(checked)
        )

    def download_print_form(
        self,
        registry_number: str,
        contract_info_id: str | None = None,
        contract_info_pf_id: str | None = None,
    ) -> DownloadedContract:
        if contract_info_pf_id is not None:
            print_form_url = urljoin(
                BASE_URL,
                (
                    f"{PRINT_FORM_PATH}"
                    f"?contractInfoPfId="
                    f"{contract_info_pf_id}"
                ),
            )

            response = self._get(
                print_form_url
            )

            if not self._looks_like_contract_print_form(
                response.text
            ):
                raise ZakupkiClientError(
                    "Указанный contractInfoPfId "
                    "не похож на печатную форму "
                    "контракта с лекарственными препаратами."
                )

            return DownloadedContract(
                registry_number=registry_number,
                contract_info_id=contract_info_id,
                contract_info_pf_id=(
                    contract_info_pf_id
                ),
                print_form_url=response.url,
                html=response.text,
            )

        (
            print_form_url,
            resolved_contract_info_id,
            resolved_pf_id,
            html,
        ) = self.find_actual_print_form(
            registry_number=registry_number,
            contract_info_id=contract_info_id,
        )

        return DownloadedContract(
            registry_number=registry_number,
            contract_info_id=(
                resolved_contract_info_id
            ),
            contract_info_pf_id=resolved_pf_id,
            print_form_url=print_form_url,
            html=html,
        )

    def parse_contract(
        self,
        registry_number: str,
        contract_info_id: str | None = None,
        contract_info_pf_id: str | None = None,
    ) -> dict[str, Any]:
        downloaded = self.download_print_form(
            registry_number=registry_number,
            contract_info_id=contract_info_id,
            contract_info_pf_id=contract_info_pf_id,
        )

        result = parse_contract_html(
            downloaded.html
        )

        parsed_registry_number = (
            result.get(
                "contract",
                {},
            )
            .get(
                "registry_number",
                "",
            )
            .strip()
        )

        if (
            parsed_registry_number
            and parsed_registry_number
            != registry_number
        ):
            raise ZakupkiClientError(
                "Печатная форма содержит другой "
                "номер реестровой записи: "
                f"{parsed_registry_number}; "
                f"ожидался {registry_number}."
            )

        result["contract"][
            "registry_number"
        ] = registry_number

        for medicine in result.get(
            "medicines",
            [],
        ):
            medicine[
                "registry_number"
            ] = registry_number

        result["source"] = {
            "registry_number_requested": (
                registry_number
            ),
            "contract_info_id": (
                downloaded.contract_info_id
            ),
            "contract_info_pf_id": (
                downloaded.contract_info_pf_id
            ),
            "print_form_url": (
                downloaded.print_form_url
            ),
            "registry_number_from_print_form": (
                parsed_registry_number or None
            ),
        }

        return result
