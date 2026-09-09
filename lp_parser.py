from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup


class LpParserError(RuntimeError):
    """Ошибка разбора файла LP_*.html."""


class LpParser:
    """
    Парсер приложения
    "Сведения о поставленных лекарственных препаратах".

    Поддерживает как минимум две известные структуры ЕИС:

    Старый формат:
      первая таблица: 1–11
      вторая таблица: 1, 12–16

    Новый формат:
      первая таблица: 1–12
      (добавлена "Комплектность потребительской упаковки")
      вторая таблица: 1, 13–17

    Поля определяются по текстам заголовков, а не по номерам,
    поэтому сдвиг нумерации не ломает парсер.
    """

    FIRST_HEADER_RULES = [
        ("line_number", ("№ п/п",)),
        (
            "inn",
            (
                "международное непатентованное",
                "химическое",
                "группировочное наименование",
            ),
        ),
        (
            "trade_form_dosage",
            ("торговое наименование", "лекарственная форма", "дозировка"),
        ),
        (
            "consumer_quantity",
            ("кол-во в потреб. единицах измерения",),
        ),
        (
            "package_completeness",
            ("комплектность потребительской упаковки",),
        ),
        (
            "primary_package_type",
            ("вид первичной упаковки",),
        ),
        (
            "forms_per_primary_package",
            ("кол-во лек. форм в первичной упаковке",),
        ),
        (
            "primary_packages_per_consumer_package",
            (
                "кол-во лек. первичных упаковок в потреб. упаковке",
            ),
        ),
        (
            "consumer_units_per_package",
            ("кол-во потреб. единиц в потреб. упаковке",),
        ),
        (
            "expiration_date",
            ("срок годности",),
        ),
        (
            "batch_number",
            ("серия лекарственного препарата",),
        ),
        (
            "supplied_consumer_packages",
            ("кол-во поставленных потреб. упаковок",),
        ),
    ]

    SECOND_HEADER_RULES = [
        ("line_number", ("№ п/п",)),
        (
            "znvlp_flag",
            ("признак включения в перечень жнвлп",),
        ),
        (
            "registered_max_price",
            (
                "зарегистрированная предельная отпускная цена",
                "установленная производителем",
            ),
        ),
        (
            "actual_manufacturer_price",
            (
                "фактическая отпускная цена",
                "установленная производителем",
            ),
        ),
        (
            "wholesale_markup_rub",
            (
                "суммарный размер фактических оптовых надбавок",
                "руб",
            ),
        ),
        (
            "wholesale_markup_percent",
            (
                "суммарный размер фактических оптовых надбавок",
                "%",
            ),
        ),
    ]

    ALL_FIELDS = [
        "line_number",
        "inn",
        "trade_form_dosage",
        "consumer_quantity",
        "package_completeness",
        "primary_package_type",
        "forms_per_primary_package",
        "primary_packages_per_consumer_package",
        "consumer_units_per_package",
        "expiration_date",
        "batch_number",
        "supplied_consumer_packages",
        "znvlp_flag",
        "registered_max_price",
        "actual_manufacturer_price",
        "wholesale_markup_rub",
        "wholesale_markup_percent",
    ]

    @staticmethod
    def _text(cell: Any) -> str:
        if cell is None:
            return ""

        return " ".join(
            cell.get_text(" ", strip=True)
            .replace("\xa0", " ")
            .split()
        )

    @staticmethod
    def _normalize(text: str) -> str:
        return (
            " ".join(
                text.replace("\xa0", " ")
                .replace("ё", "е")
                .lower()
                .split()
            )
        )

    @classmethod
    def _match_header(
        cls,
        header_text: str,
        rules: list[tuple[str, tuple[str, ...]]],
    ) -> str | None:
        normalized = cls._normalize(header_text)

        for field_name, required_fragments in rules:
            if all(
                cls._normalize(fragment) in normalized
                for fragment in required_fragments
            ):
                return field_name

        return None

    @classmethod
    def _extract_table_definition(
        cls,
        table: Any,
        rules: list[tuple[str, tuple[str, ...]]],
        *,
        required_fields: set[str],
    ) -> tuple[list[str | None], list[list[str]]] | None:
        """
        Возвращает:
          - список field_name для каждого HTML-столбца;
          - все строки таблицы в текстовом виде.

        Таблица определяется по первой строке-заголовку.
        """
        tr_rows = table.find_all("tr", class_="main-table")
        if not tr_rows:
            return None

        rows: list[list[str]] = []

        for tr in tr_rows:
            cells = tr.find_all(["td", "th"], recursive=False)
            if not cells:
                continue

            rows.append([cls._text(cell) for cell in cells])

        if not rows:
            return None

        headers = rows[0]
        mapping = [
            cls._match_header(header, rules)
            for header in headers
        ]

        found_fields = {
            field_name
            for field_name in mapping
            if field_name is not None
        }

        if not required_fields.issubset(found_fields):
            return None

        return mapping, rows

    @staticmethod
    def _looks_like_numbering_row(values: list[str]) -> bool:
        if not values:
            return False

        normalized = [value.strip() for value in values]

        # Нумерация в ЕИС состоит из целых номеров столбцов:
        # старый формат 1..11 / 1,12..16,
        # новый формат 1..12 / 1,13..17.
        return all(value.isdigit() for value in normalized)

    @classmethod
    def _extract_data_rows(
        cls,
        mapping: list[str | None],
        rows: list[list[str]],
    ) -> list[dict[str, str]]:
        numbering_index: int | None = None

        for index, values in enumerate(rows[1:], start=1):
            if (
                len(values) == len(mapping)
                and cls._looks_like_numbering_row(values)
            ):
                numbering_index = index
                break

        if numbering_index is None:
            raise LpParserError(
                "В таблице LP не найдена строка нумерации столбцов."
            )

        result: list[dict[str, str]] = []

        for values in rows[numbering_index + 1:]:
            if len(values) != len(mapping):
                continue

            row_data: dict[str, str] = {}

            for field_name, value in zip(mapping, values):
                if field_name is not None:
                    row_data[field_name] = value

            line_number = row_data.get(
                "line_number",
                "",
            ).strip()

            if not line_number:
                continue

            result.append(row_data)

        return result

    @staticmethod
    def _rows_to_map(
        rows: list[dict[str, str]],
        *,
        table_name: str,
    ) -> tuple[dict[str, dict[str, str]], list[str]]:
        mapped: dict[str, dict[str, str]] = {}
        order: list[str] = []

        for row_data in rows:
            line_number = row_data.get(
                "line_number",
                "",
            ).strip()

            if not line_number:
                continue

            if line_number in mapped:
                raise LpParserError(
                    f"В {table_name} найден повтор № п/п "
                    f"{line_number}; однозначное объединение невозможно."
                )

            mapped[line_number] = row_data
            order.append(line_number)

        return mapped, order

    def parse_html(
        self,
        html: str | bytes,
        *,
        registry_number: str,
        upd_uid: str | None = None,
        upd_archive_name: str | None = None,
        lp_html_name: str | None = None,
    ) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")

        full_text = soup.get_text(" ", strip=True)
        if (
            "Сведения о поставленных лекарственных препаратах"
            not in full_text
        ):
            raise LpParserError(
                "HTML не похож на форму "
                "'Сведения о поставленных лекарственных препаратах'."
            )

        tables = soup.find_all("table")
        if len(tables) < 2:
            raise LpParserError(
                "В LP HTML не найдены две части таблицы."
            )

        first_definition = None
        second_definition = None

        for table in tables:
            if first_definition is None:
                first_definition = self._extract_table_definition(
                    table,
                    self.FIRST_HEADER_RULES,
                    required_fields={
                        "line_number",
                        "inn",
                        "trade_form_dosage",
                        "consumer_quantity",
                        "primary_package_type",
                        "batch_number",
                        "supplied_consumer_packages",
                    },
                )

            if second_definition is None:
                second_definition = self._extract_table_definition(
                    table,
                    self.SECOND_HEADER_RULES,
                    required_fields={
                        "line_number",
                        "znvlp_flag",
                        "registered_max_price",
                        "actual_manufacturer_price",
                        "wholesale_markup_rub",
                        "wholesale_markup_percent",
                    },
                )

        if first_definition is None:
            raise LpParserError(
                "Не найдена первая часть таблицы LP "
                "по названиям столбцов."
            )

        if second_definition is None:
            raise LpParserError(
                "Не найдена вторая часть таблицы LP "
                "по названиям столбцов."
            )

        first_mapping, first_rows_raw = first_definition
        second_mapping, second_rows_raw = second_definition

        first_rows = self._extract_data_rows(
            first_mapping,
            first_rows_raw,
        )
        second_rows = self._extract_data_rows(
            second_mapping,
            second_rows_raw,
        )

        first_map, first_order = self._rows_to_map(
            first_rows,
            table_name="первой части LP",
        )
        second_map, second_order = self._rows_to_map(
            second_rows,
            table_name="второй части LP",
        )

        merged_order = list(first_order)

        for line_number in second_order:
            if line_number not in first_map:
                merged_order.append(line_number)

        result: list[dict[str, Any]] = []

        has_completeness = any(
            mapping_value == "package_completeness"
            for mapping_value in first_mapping
        )

        schema_name = (
            "current_with_package_completeness"
            if has_completeness
            else "legacy_without_package_completeness"
        )

        for line_number in merged_order:
            row_data: dict[str, Any] = {
                "registry_number": registry_number,

                # Служебные поля, в Excel не выводятся.
                "_upd_uid": upd_uid,
                "_upd_archive_name": upd_archive_name,
                "_lp_html_name": lp_html_name,
                "_lp_schema": schema_name,
            }

            if line_number in first_map:
                row_data.update(first_map[line_number])

            if line_number in second_map:
                for field_name, value in second_map[
                    line_number
                ].items():
                    if field_name != "line_number":
                        row_data[field_name] = value

            row_data["line_number"] = line_number

            for field_name in self.ALL_FIELDS:
                row_data.setdefault(field_name, "")

            result.append(row_data)

        return result


def parse_lp_html(
    html: str | bytes,
    registry_number: str,
) -> list[dict[str, Any]]:
    return LpParser().parse_html(
        html,
        registry_number=registry_number,
    )