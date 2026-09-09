from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup


class UpdParserError(RuntimeError):
    """Ошибка разбора HTML печатной формы УПД."""


class UpdParser:
    """Парсер основной товарной таблицы из UPD_*.html."""

    COLUMNS = [
        ("product_code", "Код товара/ работ, услуг"),  # А
        ("line_number", "№ п/п"),  # 1
        (
            "product_name",
            "Наименование товара (описание выполненных работ, оказанных услуг), имущественного права",
        ),  # 1а
        ("product_type_code", "Код вида товара"),  # 1б
        ("unit_code", "Единица измерения: код"),  # 2
        (
            "unit_name",
            "Единица измерения: условное обозначение (национальное)",
        ),  # 2а
        ("quantity", "Количество (объем)"),  # 3
        ("unit_price", "Цена (тариф) за единицу измерения"),  # 4
        (
            "amount_without_tax",
            "Стоимость товаров (работ, услуг), имущественных прав без налога – всего",
        ),  # 5
        ("excise_amount", "В том числе сумма акциза"),  # 6
        ("tax_rate", "Налоговая ставка"),  # 7
        (
            "tax_amount",
            "Сумма налога, предъявляемая покупателю",
        ),  # 8
        (
            "amount_with_tax",
            "Стоимость товаров (работ, услуг), имущественных прав с налогом – всего",
        ),  # 9
        ("origin_country_code", "Страна происхождения товара: цифровой код"),  # 10
        (
            "origin_country_name",
            "Страна происхождения товара: краткое наименование",
        ),  # 10а
        (
            "customs_declaration_number",
            "Регистрационный номер декларации на товары или регистрационный номер партии товара, подлежащего прослеживаемости",
        ),  # 11
        (
            "manufacturer_registration_country",
            "Страна регистрации производителя товара",
        ),  # 14
    ]

    @staticmethod
    def _text(cell: Any) -> str:
        if cell is None:
            return ""

        # separator=" " сохраняет "Серия: ... / Срок годности: ..."
        # как читаемый текст вместо склеивания.
        return " ".join(
            cell.get_text(" ", strip=True)
            .replace("\xa0", " ")
            .split()
        )

    @staticmethod
    def _is_numbering_row(cells: list[Any]) -> bool:
        if len(cells) < 18:
            return False

        values = [UpdParser._text(cell).lower() for cell in cells]

        # В типовой форме строка выглядит:
        # [пусто], А, 1, 1а, 1б, 2, 2а, 3 ... 11, 14, [пусто]
        return (
            values[1] in {"а", "a"}
            and values[2] == "1"
            and values[3] in {"1а", "1a"}
            and values[4] in {"1б", "1b"}
            and "14" in values
        )

    @staticmethod
    def _looks_like_total_row(cells: list[Any]) -> bool:
        text = " ".join(
            UpdParser._text(cell).lower()
            for cell in cells
        )
        return "всего к оплате" in text

    def parse_html(
        self,
        html: str | bytes,
        *,
        registry_number: str,
        upd_uid: str | None = None,
        upd_archive_name: str | None = None,
        upd_archive_url: str | None = None,
        upd_html_name: str | None = None,
    ) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")

        main_rows = soup.find_all("tr", class_="main-table")
        if not main_rows:
            raise UpdParserError(
                "В UPD HTML не найдены строки таблицы class='main-table'."
            )

        numbering_index: int | None = None

        for index, row in enumerate(main_rows):
            cells = row.find_all("td", recursive=False)
            if self._is_numbering_row(cells):
                numbering_index = index
                break

        if numbering_index is None:
            raise UpdParserError(
                "Не найдена строка нумерации столбцов А, 1, 1а ... 14."
            )

        result: list[dict[str, Any]] = []

        for row in main_rows[numbering_index + 1:]:
            cells = row.find_all("td", recursive=False)

            if not cells:
                continue

            if self._looks_like_total_row(cells):
                break

            # В товарной строке формы:
            # 0 — техническая пустая ячейка,
            # 1..17 — наши значения А..14,
            # 18 — техническая правая пустая ячейка.
            if len(cells) < 18:
                continue

            logical_cells = cells[1:18]

            if len(logical_cells) != len(self.COLUMNS):
                continue

            row_data: dict[str, Any] = {
                "registry_number": registry_number,

                # Служебные поля. В Excel на этом этапе не выводим.
                "_upd_uid": upd_uid,
                "_upd_archive_name": upd_archive_name,
                "_upd_archive_url": upd_archive_url,
                "_upd_html_name": upd_html_name,
            }

            for (field_name, _), cell in zip(
                self.COLUMNS,
                logical_cells,
            ):
                row_data[field_name] = self._text(cell)

            # Пустую строку не считаем товарной позицией.
            if not any(
                row_data.get(field_name)
                for field_name, _ in self.COLUMNS
            ):
                continue

            result.append(row_data)

        return result


def parse_upd_html(
    html: str | bytes,
    registry_number: str,
) -> list[dict[str, Any]]:
    """Упрощённый вызов для тестов/отдельного HTML."""
    return UpdParser().parse_html(
        html,
        registry_number=registry_number,
    )
