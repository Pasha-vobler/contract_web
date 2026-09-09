from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from lp_parser import LpParser, LpParserError
from upd_parser import UpdParser, UpdParserError
from zakupki_client import BASE_URL, ZakupkiClient, ZakupkiClientError


@dataclass(slots=True)
class UpdArchiveLink:
    uid: str | None
    filename: str
    url: str


@dataclass(slots=True)
class UpdArchiveError:
    archive_name: str
    url: str
    error: str


@dataclass(slots=True)
class UpdContractResult:
    registry_number: str
    contract_info_id: str | None
    archives_found: int = 0

    # Основная форма UPD_*.html.
    html_files_processed: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)

    # Приложение ON_NSCHFDOPPR/LP_*.html.
    lp_html_files_processed: int = 0
    lp_rows: list[dict[str, Any]] = field(default_factory=list)

    errors: list[UpdArchiveError] = field(default_factory=list)


@dataclass(slots=True)
class ArchiveParseResult:
    upd_rows: list[dict[str, Any]] = field(default_factory=list)
    lp_rows: list[dict[str, Any]] = field(default_factory=list)
    upd_html_count: int = 0
    lp_html_count: int = 0
    errors: list[str] = field(default_factory=list)


class UpdService:
    """Поиск и обработка UPD ZIP-файлов без сохранения на диск."""

    DEFAULT_MAX_ARCHIVE_SIZE = 50 * 1024 * 1024  # 50 МБ

    def __init__(
        self,
        client: ZakupkiClient,
        *,
        max_archive_size: int = DEFAULT_MAX_ARCHIVE_SIZE,
    ) -> None:
        self.client = client
        self.max_archive_size = max_archive_size
        self.parser = UpdParser()
        self.lp_parser = LpParser()

    @staticmethod
    def _extract_uid(url: str) -> str | None:
        values = parse_qs(urlparse(url).query).get("uid")
        return values[0] if values else None

    @staticmethod
    def _filename_from_title(title: str) -> str | None:
        match = re.match(
            r"^\s*(UPD_.+?\.zip)(?:\s*\(|\s*$)",
            title,
            flags=re.I,
        )
        return match.group(1).strip() if match else None

    def find_upd_archives(
        self,
        document_info_html: str,
    ) -> list[UpdArchiveLink]:
        soup = BeautifulSoup(document_info_html, "html.parser")

        result: list[UpdArchiveLink] = []
        seen_urls: set[str] = set()

        for link in soup.find_all("a", href=True):
            href = link.get("href", "").strip()
            title = link.get("title", "").strip()

            if not href or not title:
                continue

            filename = self._filename_from_title(title)
            if not filename:
                continue

            absolute_url = urljoin(BASE_URL, href)

            if "/44fz/filestore/public/" not in absolute_url:
                continue
            if "file.html" not in absolute_url:
                continue

            if absolute_url in seen_urls:
                continue

            seen_urls.add(absolute_url)

            result.append(
                UpdArchiveLink(
                    uid=self._extract_uid(absolute_url),
                    filename=filename,
                    url=absolute_url,
                )
            )

        return result

    def _download_archive_to_memory(
        self,
        archive: UpdArchiveLink,
    ) -> io.BytesIO:
        try:
            response = self.client.session.get(
                archive.url,
                timeout=self.client.timeout,
                allow_redirects=True,
                verify=self.client.verify_ssl,
                stream=True,
            )
        except Exception as exc:
            raise ZakupkiClientError(
                f"Не удалось скачать UPD ZIP {archive.filename}: {exc}"
            ) from exc

        try:
            if response.status_code != 200:
                raise ZakupkiClientError(
                    f"UPD ZIP {archive.filename}: "
                    f"HTTP {response.status_code}"
                )

            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    declared_size = 0

                if declared_size > self.max_archive_size:
                    raise ZakupkiClientError(
                        f"UPD ZIP {archive.filename} слишком большой: "
                        f"{declared_size} байт."
                    )

            buffer = io.BytesIO()
            downloaded = 0

            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue

                downloaded += len(chunk)

                if downloaded > self.max_archive_size:
                    buffer.close()
                    raise ZakupkiClientError(
                        f"UPD ZIP {archive.filename} превысил "
                        f"лимит {self.max_archive_size} байт."
                    )

                buffer.write(chunk)

            buffer.seek(0)
            return buffer

        finally:
            response.close()

    @staticmethod
    def _normalized_member_name(member: zipfile.ZipInfo) -> str:
        # Обычно ZIP использует "/", но учитываем и "\".
        return member.filename.replace("\\", "/")

    @classmethod
    def _find_upd_html_members(
        cls,
        archive: zipfile.ZipFile,
    ) -> list[zipfile.ZipInfo]:
        result: list[zipfile.ZipInfo] = []

        for member in archive.infolist():
            if member.is_dir():
                continue

            normalized = cls._normalized_member_name(member)
            basename = PurePosixPath(normalized).name

            if (
                basename.upper().startswith("UPD_")
                and basename.lower().endswith(".html")
            ):
                result.append(member)

        return result

    @classmethod
    def _find_lp_html_members(
        cls,
        archive: zipfile.ZipFile,
    ) -> list[zipfile.ZipInfo]:
        """
        Ищем только:
        ON_NSCHFDOPPR/.../LP_*.html
        """
        result: list[zipfile.ZipInfo] = []

        for member in archive.infolist():
            if member.is_dir():
                continue

            normalized = cls._normalized_member_name(member)
            path = PurePosixPath(normalized)
            basename = path.name
            parts_upper = [part.upper() for part in path.parts]

            if "ON_NSCHFDOPPR" not in parts_upper:
                continue

            if (
                basename.upper().startswith("LP_")
                and basename.lower().endswith(".html")
            ):
                result.append(member)

        return result

    def _parse_archive(
        self,
        archive_link: UpdArchiveLink,
        *,
        registry_number: str,
    ) -> ArchiveParseResult:
        memory_file = self._download_archive_to_memory(
            archive_link
        )

        result = ArchiveParseResult()

        try:
            try:
                with zipfile.ZipFile(memory_file, "r") as archive:
                    upd_members = self._find_upd_html_members(archive)
                    lp_members = self._find_lp_html_members(archive)

                    if not upd_members:
                        result.errors.append(
                            "В ZIP не найден файл UPD_*.html."
                        )

                    if not lp_members:
                        result.errors.append(
                            "В ZIP не найден "
                            "ON_NSCHFDOPPR/LP_*.html."
                        )

                    for member in upd_members:
                        try:
                            raw_html = archive.read(member)

                            parsed_rows = self.parser.parse_html(
                                raw_html,
                                registry_number=registry_number,
                                upd_uid=archive_link.uid,
                                upd_archive_name=archive_link.filename,
                                upd_archive_url=archive_link.url,
                                upd_html_name=PurePosixPath(
                                    self._normalized_member_name(member)
                                ).name,
                            )

                            result.upd_rows.extend(parsed_rows)
                            result.upd_html_count += 1

                        except Exception as exc:
                            result.errors.append(
                                "Ошибка UPD HTML "
                                f"{member.filename}: "
                                f"{type(exc).__name__}: {exc}"
                            )

                    for member in lp_members:
                        try:
                            raw_html = archive.read(member)

                            parsed_rows = self.lp_parser.parse_html(
                                raw_html,
                                registry_number=registry_number,
                                upd_uid=archive_link.uid,
                                upd_archive_name=archive_link.filename,
                                lp_html_name=PurePosixPath(
                                    self._normalized_member_name(member)
                                ).name,
                            )

                            result.lp_rows.extend(parsed_rows)
                            result.lp_html_count += 1

                        except Exception as exc:
                            result.errors.append(
                                "Ошибка LP HTML "
                                f"{member.filename}: "
                                f"{type(exc).__name__}: {exc}"
                            )

                    return result

            except zipfile.BadZipFile as exc:
                raise UpdParserError(
                    f"Файл {archive_link.filename} не является "
                    "корректным ZIP-архивом."
                ) from exc

        finally:
            memory_file.close()

    def parse_contract_upd(
        self,
        registry_number: str,
        *,
        contract_info_id: str | None = None,
    ) -> UpdContractResult:
        document_html, resolved_contract_info_id = (
            self.client.get_document_info_html(
                registry_number=registry_number,
                contract_info_id=contract_info_id,
            )
        )

        archive_links = self.find_upd_archives(
            document_html
        )

        result = UpdContractResult(
            registry_number=registry_number,
            contract_info_id=resolved_contract_info_id,
            archives_found=len(archive_links),
        )

        for index, archive_link in enumerate(
            archive_links,
            start=1,
        ):
            print(
                f"    UPD ZIP [{index}/{len(archive_links)}]: "
                f"{archive_link.filename}"
            )

            try:
                archive_result = self._parse_archive(
                    archive_link,
                    registry_number=registry_number,
                )

                result.rows.extend(archive_result.upd_rows)
                result.lp_rows.extend(archive_result.lp_rows)

                result.html_files_processed += (
                    archive_result.upd_html_count
                )
                result.lp_html_files_processed += (
                    archive_result.lp_html_count
                )

                print(
                    "        UPD: "
                    f"HTML = {archive_result.upd_html_count}, "
                    f"строк = {len(archive_result.upd_rows)}"
                )
                print(
                    "        LP:  "
                    f"HTML = {archive_result.lp_html_count}, "
                    f"строк = {len(archive_result.lp_rows)}"
                )

                for error_text in archive_result.errors:
                    result.errors.append(
                        UpdArchiveError(
                            archive_name=archive_link.filename,
                            url=archive_link.url,
                            error=error_text,
                        )
                    )
                    print(f"        ПРЕДУПРЕЖДЕНИЕ: {error_text}")

                if not archive_result.errors:
                    print("        OK")

            except Exception as exc:
                result.errors.append(
                    UpdArchiveError(
                        archive_name=archive_link.filename,
                        url=archive_link.url,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

                print(
                    f"        ОШИБКА: "
                    f"{type(exc).__name__}: {exc}"
                )

        return result
