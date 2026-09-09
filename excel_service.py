from __future__ import annotations

from pathlib import Path
from typing import Any

import xlsxwriter


class ExcelService:
    """Формирование итогового Excel-файла по результатам парсинга."""

    CONTRACT_COLUMNS = [
        ("registry_number", "№ реестровой записи"),
        ("customer", "Заказчик"),
        ("contract_url", "Ссылка на контракт"),
    ]

    CONTRACT_COMMON_INFO_URL = (
        "https://zakupki.gov.ru/epz/contract/contractCard/"
        "common-info.html?reestrNumber={registry_number}"
    )

    MEDICINE_COLUMNS = [
        ("registry_number", "№ реестровой записи"),
        ("purchase_object_name", "Наименование объекта закупки"),
        ("purchase_object_type", "Тип объекта закупки"),
        ("ktru_okpd2", "Позиции по КТРУ / ОКПД2 / информация о ТРУ"),
        ("quantity_unit", "Количество (объем) и единица измерения по ОКЕИ"),
        ("characteristics", "Характеристики объекта закупки"),
        ("unit_price", "Цена за единицу"),
        ("vat_rate", "Ставка НДС"),
        ("origin_country", "Страна происхождения товара"),
        ("amount", "Сумма"),
        ("inn", "МНН / группировочное / химическое наименование"),
        ("trade_name", "Торговое наименование"),
        ("dosage_form", "Лекарственная форма"),
        ("dosage", "Дозировка"),
        ("grls_inn_release_form", "МНН и форма выпуска в соответствии с ГРЛС"),
        ("consumer_quantity", "Количество в потребительских единицах измерения"),
        ("registration_certificate", "Номер РУ"),
        ("registration_holder", "Наименование держателя или владельца РУ"),
        ("manufacturer", "Наименование производителя"),
        ("manufacturer_country", "Страна производителя, код по ОКСМ"),
        ("primary_package_type", "Вид первичной упаковки"),
        ("forms_per_primary_package", "Количество лекарственных форм в первичной упаковке"),
        (
            "primary_packages_per_consumer_package",
            "Количество первичных упаковок в потребительской упаковке",
        ),
        (
            "consumer_units_per_package",
            "Количество потребительских единиц в потребительской упаковке",
        ),
        ("package_completeness", "Комплектность потребительской упаковки"),
    ]

    # Таблица из UPD_*.html: от колонки А до колонки 14.
    # Техническая строка "А / 1 / 1а / ... / 14" в Excel не выгружается.
    UPD_COLUMNS = [
        ("registry_number", "№ реестровой записи"),
        ("_upd_archive_url", "Ссылка на UPD ZIP"),
        ("product_code", "Код товара/ работ, услуг"),
        ("line_number", "№ п/п"),
        (
            "product_name",
            "Наименование товара (описание выполненных работ, оказанных услуг), имущественного права",
        ),
        ("product_type_code", "Код вида товара"),
        ("unit_code", "Единица измерения: код"),
        (
            "unit_name",
            "Единица измерения: условное обозначение (национальное)",
        ),
        ("quantity", "Количество (объем)"),
        ("unit_price", "Цена (тариф) за единицу измерения"),
        (
            "amount_without_tax",
            "Стоимость товаров (работ, услуг), имущественных прав без налога – всего",
        ),
        ("excise_amount", "В том числе сумма акциза"),
        ("tax_rate", "Налоговая ставка"),
        ("tax_amount", "Сумма налога, предъявляемая покупателю"),
        (
            "amount_with_tax",
            "Стоимость товаров (работ, услуг), имущественных прав с налогом – всего",
        ),
        (
            "origin_country_code",
            "Страна происхождения товара: цифровой код",
        ),
        (
            "origin_country_name",
            "Страна происхождения товара: краткое наименование",
        ),
        (
            "customs_declaration_number",
            "Регистрационный номер декларации на товары или регистрационный номер партии товара, подлежащего прослеживаемости",
        ),
        (
            "manufacturer_registration_country",
            "Страна регистрации производителя товара",
        ),
    ]

    NUMERIC_FIELDS = {
        "unit_price",
        "amount",
        "consumer_quantity",
        "forms_per_primary_package",
        "primary_packages_per_consumer_package",
        "consumer_units_per_package",
    }

    MONEY_FIELDS = {"unit_price", "amount"}

    UPD_NUMERIC_FIELDS = {
        "quantity",
        "unit_price",
        "amount_without_tax",
        "tax_amount",
        "amount_with_tax",
    }

    UPD_MONEY_FIELDS = {
        "unit_price",
        "amount_without_tax",
        "tax_amount",
        "amount_with_tax",
    }

    CONTRACT_WIDTHS = [24, 90, 72]

    MEDICINE_WIDTHS = [
        24, 65, 18, 45, 38, 45, 16, 11, 30, 18,
        28, 28, 42, 18, 55, 20, 24, 42, 42, 30,
        24, 24, 25, 25, 60,
    ]

    UPD_WIDTHS = [
        24,  # registry_number
        72,  # _upd_archive_url
        20,  # product_code
        10,  # line_number
        60,  # product_name
        16,  # product_type_code
        14,  # unit_code
        24,  # unit_name
        16,  # quantity
        20,  # unit_price
        22,  # amount_without_tax
        18,  # excise_amount
        14,  # tax_rate
        20,  # tax_amount
        22,  # amount_with_tax
        16,  # origin_country_code
        24,  # origin_country_name
        48,  # customs_declaration_number
        36,  # manufacturer_registration_country
    ]


    LP_COLUMNS = [
        ("registry_number", "№ реестровой записи"),
        ("line_number", "№ п/п"),
        (
            "inn",
            "Международное непатентованное или химическое или группировочное наименование",
        ),
        (
            "trade_form_dosage",
            "Торговое наименование, лекарственная форма, дозировка",
        ),
        (
            "consumer_quantity",
            "Кол-во в потреб. единицах измерения",
        ),
        (
            "package_completeness",
            "Комплектность потребительской упаковки",
        ),
        ("primary_package_type", "Вид первичной упаковки"),
        (
            "forms_per_primary_package",
            "Кол-во лек. форм в первичной упаковке",
        ),
        (
            "primary_packages_per_consumer_package",
            "Кол-во лек. первичных упаковок в потреб. упаковке",
        ),
        (
            "consumer_units_per_package",
            "Кол-во потреб. единиц в потреб. упаковке",
        ),
        ("expiration_date", "Срок годности"),
        ("batch_number", "Серия лекарственного препарата"),
        (
            "supplied_consumer_packages",
            "Кол-во поставленных потреб. упаковок*",
        ),
        (
            "znvlp_flag",
            "Признак включения в перечень ЖНВЛП",
        ),
        (
            "registered_max_price",
            "Зарегистрированная предельная отпускная цена, установленная производителем лекарственного препарата, руб",
        ),
        (
            "actual_manufacturer_price",
            "Фактическая отпускная цена, установленная производителем лекарственного препарата, руб",
        ),
        (
            "wholesale_markup_rub",
            "Суммарный размер фактических оптовых надбавок, установленных организациям оптовой торговли, руб",
        ),
        (
            "wholesale_markup_percent",
            "Суммарный размер фактических оптовых надбавок, установленных организациям оптовой торговли, %",
        ),
    ]

    LP_NUMERIC_FIELDS = {
        "forms_per_primary_package",
        "primary_packages_per_consumer_package",
        "consumer_units_per_package",
        "supplied_consumer_packages",
        "registered_max_price",
        "actual_manufacturer_price",
        "wholesale_markup_rub",
        "wholesale_markup_percent",
    }

    LP_MONEY_FIELDS = {
        "registered_max_price",
        "actual_manufacturer_price",
        "wholesale_markup_rub",
    }

    LP_WIDTHS = [
        24,  # registry_number
        10,  # line_number
        34,  # inn
        55,  # trade_form_dosage
        34,  # consumer_quantity
        28,  # package_completeness
        24,  # primary_package_type
        24,  # forms_per_primary_package
        26,  # primary_packages_per_consumer_package
        26,  # consumer_units_per_package
        18,  # expiration_date
        22,  # batch_number
        24,  # supplied_consumer_packages
        22,  # znvlp_flag
        28,  # registered_max_price
        28,  # actual_manufacturer_price
        30,  # wholesale_markup_rub
        30,  # wholesale_markup_percent
    ]

    def __init__(self, output_dir: str | Path = "output") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _clean_value(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _to_number(value: Any) -> int | float | None:
        if value is None:
            return None

        text = str(value).strip()
        if not text:
            return None

        normalized = (
            text.replace("\xa0", "")
            .replace(" ", "")
            .replace(",", ".")
        )

        try:
            number = float(normalized)
        except ValueError:
            return None

        if number.is_integer():
            return int(number)
        return number

    @staticmethod
    def _make_formats(workbook: xlsxwriter.Workbook) -> dict[str, Any]:
        return {
            "header": workbook.add_format(
                {
                    "bold": True,
                    "font_color": "white",
                    "bg_color": "#1F4E78",
                    "border": 1,
                    "align": "center",
                    "valign": "vcenter",
                    "text_wrap": True,
                }
            ),
            "text": workbook.add_format(
                {
                    "border": 1,
                    "valign": "top",
                    "text_wrap": True,
                }
            ),
            "text_center": workbook.add_format(
                {
                    "border": 1,
                    "align": "center",
                    "valign": "top",
                    "text_wrap": True,
                }
            ),
            "number": workbook.add_format(
                {
                    "border": 1,
                    "valign": "top",
                    "num_format": "#,##0.###########",
                }
            ),
            "money": workbook.add_format(
                {
                    "border": 1,
                    "valign": "top",
                    "num_format": "#,##0.00###########",
                }
            ),
            "link": workbook.add_format(
                {
                    "border": 1,
                    "valign": "top",
                    "text_wrap": True,
                    "font_color": "blue",
                    "underline": 1,
                }
            ),
        }

    def _write_contracts_sheet(
        self,
        workbook: xlsxwriter.Workbook,
        formats: dict[str, Any],
        contracts: list[dict[str, Any]],
    ) -> None:
        sheet = workbook.add_worksheet("Контракты")
        sheet.freeze_panes(1, 0)
        sheet.set_row(0, 36)

        headers = [title for _, title in self.CONTRACT_COLUMNS]

        for col, header in enumerate(headers):
            sheet.write(0, col, header, formats["header"])

        for row, contract in enumerate(contracts, start=1):
            registry_number = self._clean_value(
                contract.get("registry_number")
            )

            sheet.write_string(
                row,
                0,
                registry_number,
                formats["text_center"],
            )

            sheet.write_string(
                row,
                1,
                self._clean_value(contract.get("customer")),
                formats["text"],
            )

            contract_url = self.CONTRACT_COMMON_INFO_URL.format(
                registry_number=registry_number
            )

            # Полный URL показывается в ячейке и остаётся кликабельным.
            sheet.write_url(
                row,
                2,
                contract_url,
                formats["link"],
                contract_url,
            )

        for col, width in enumerate(self.CONTRACT_WIDTHS):
            sheet.set_column(col, col, width)

        last_row = max(1, len(contracts))
        sheet.autofilter(0, 0, last_row, len(headers) - 1)

    def _write_medicines_sheet(
        self,
        workbook: xlsxwriter.Workbook,
        formats: dict[str, Any],
        medicines: list[dict[str, Any]],
    ) -> None:
        sheet = workbook.add_worksheet("Препараты")
        sheet.freeze_panes(1, 1)
        sheet.set_row(0, 60)

        headers = [title for _, title in self.MEDICINE_COLUMNS]

        for col, header in enumerate(headers):
            sheet.write(0, col, header, formats["header"])

        for row, medicine in enumerate(medicines, start=1):
            for col, (field_name, _) in enumerate(self.MEDICINE_COLUMNS):
                raw_value = medicine.get(field_name)

                if field_name == "registry_number":
                    sheet.write_string(
                        row,
                        col,
                        self._clean_value(raw_value),
                        formats["text_center"],
                    )
                    continue

                if field_name in self.NUMERIC_FIELDS:
                    number = self._to_number(raw_value)
                    if number is not None:
                        number_format = (
                            formats["money"]
                            if field_name in self.MONEY_FIELDS
                            else formats["number"]
                        )
                        sheet.write_number(row, col, number, number_format)
                        continue

                sheet.write_string(
                    row,
                    col,
                    self._clean_value(raw_value),
                    formats["text"],
                )

        for col, width in enumerate(self.MEDICINE_WIDTHS):
            sheet.set_column(col, col, width)

        last_row = max(1, len(medicines))
        sheet.autofilter(0, 0, last_row, len(headers) - 1)

    def _write_upd_sheet(
        self,
        workbook: xlsxwriter.Workbook,
        formats: dict[str, Any],
        upd_rows: list[dict[str, Any]],
    ) -> None:
        sheet = workbook.add_worksheet("Сведения из UPD")
        sheet.freeze_panes(1, 1)
        sheet.set_row(0, 72)

        headers = [title for _, title in self.UPD_COLUMNS]

        for col, header in enumerate(headers):
            sheet.write(0, col, header, formats["header"])

        for row, upd_item in enumerate(upd_rows, start=1):
            for col, (field_name, _) in enumerate(self.UPD_COLUMNS):
                raw_value = upd_item.get(field_name)

                # Реестровый номер всегда текстом, иначе Excel испортит
                # длинный номер (>15 цифр).
                if field_name == "registry_number":
                    sheet.write_string(
                        row,
                        col,
                        self._clean_value(raw_value),
                        formats["text_center"],
                    )
                    continue

                if field_name == "_upd_archive_url":
                    archive_url = self._clean_value(raw_value)

                    if archive_url:
                        sheet.write_url(
                            row,
                            col,
                            archive_url,
                            formats["link"],
                            archive_url,
                        )
                    else:
                        sheet.write_string(
                            row,
                            col,
                            "",
                            formats["text"],
                        )

                    continue

                if field_name in self.UPD_NUMERIC_FIELDS:
                    number = self._to_number(raw_value)
                    if number is not None:
                        number_format = (
                            formats["money"]
                            if field_name in self.UPD_MONEY_FIELDS
                            else formats["number"]
                        )
                        sheet.write_number(
                            row,
                            col,
                            number,
                            number_format,
                        )
                        continue

                sheet.write_string(
                    row,
                    col,
                    self._clean_value(raw_value),
                    formats["text"],
                )

        for col, width in enumerate(self.UPD_WIDTHS):
            sheet.set_column(col, col, width)

        last_row = max(1, len(upd_rows))
        sheet.autofilter(0, 0, last_row, len(headers) - 1)


    def _write_lp_sheet(
        self,
        workbook: xlsxwriter.Workbook,
        formats: dict[str, Any],
        lp_rows: list[dict[str, Any]],
    ) -> None:
        # Excel ограничивает имя листа 31 символом.
        sheet = workbook.add_worksheet("Сведения о поставленных ЛП")
        sheet.freeze_panes(1, 2)
        sheet.set_row(0, 84)

        headers = [title for _, title in self.LP_COLUMNS]

        for col, header in enumerate(headers):
            sheet.write(0, col, header, formats["header"])

        for row, lp_item in enumerate(lp_rows, start=1):
            for col, (field_name, _) in enumerate(self.LP_COLUMNS):
                raw_value = lp_item.get(field_name)

                if field_name == "registry_number":
                    sheet.write_string(
                        row,
                        col,
                        self._clean_value(raw_value),
                        formats["text_center"],
                    )
                    continue

                if field_name in self.LP_NUMERIC_FIELDS:
                    number = self._to_number(raw_value)
                    if number is not None:
                        number_format = (
                            formats["money"]
                            if field_name in self.LP_MONEY_FIELDS
                            else formats["number"]
                        )
                        sheet.write_number(
                            row,
                            col,
                            number,
                            number_format,
                        )
                        continue

                sheet.write_string(
                    row,
                    col,
                    self._clean_value(raw_value),
                    formats["text"],
                )

        for col, width in enumerate(self.LP_WIDTHS):
            sheet.set_column(col, col, width)

        last_row = max(1, len(lp_rows))
        sheet.autofilter(0, 0, last_row, len(headers) - 1)

    def export(
        self,
        batch_result: Any,
        filename: str = "result.xlsx",
        *,
        upd_rows: list[dict[str, Any]] | None = None,
        lp_rows: list[dict[str, Any]] | None = None,
    ) -> Path:
        if not filename.lower().endswith(".xlsx"):
            filename += ".xlsx"

        output_path = self.output_dir / filename

        workbook = xlsxwriter.Workbook(output_path)

        try:
            formats = self._make_formats(workbook)

            contracts = list(batch_result.contracts)
            medicines = list(batch_result.medicines)

            self._write_contracts_sheet(
                workbook,
                formats,
                contracts,
            )
            self._write_medicines_sheet(
                workbook,
                formats,
                medicines,
            )

            # Если UPD переданы, лист создаётся даже при 0 строк:
            # это удобно для итогового WEB-приложения.
            if upd_rows is not None:
                self._write_upd_sheet(
                    workbook,
                    formats,
                    list(upd_rows),
                )

            if lp_rows is not None:
                self._write_lp_sheet(
                    workbook,
                    formats,
                    list(lp_rows),
                )

        finally:
            workbook.close()

        return output_path.resolve()
