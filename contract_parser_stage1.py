from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag


class ContractParseError(RuntimeError):
    """Ошибка разбора печатной формы контракта ЕИС."""


def clean_text(value: str) -> str:
    """Нормализует пробелы, сохраняя фактическое содержимое ЕИС."""
    value = value.replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def cell_text(cell: Tag) -> str:
    return clean_text(cell.get_text(" ", strip=True))


def direct_cells(row: Tag) -> list[Tag]:
    return row.find_all(["td", "th"], recursive=False)


def is_numbering_row(values: list[str]) -> bool:
    """Строка вида 1 | 2 | 3 | ... — техническая нумерация колонок ЕИС."""
    if not values:
        return False
    return all(value.isdigit() for value in values)


def find_center(soup: BeautifulSoup, title: str) -> Tag:
    center = soup.find(
        "center",
        string=lambda value: value is not None and title in clean_text(value),
    )
    if center is None:
        # На случай вложенных тегов внутри <center>.
        center = soup.find(
            "center",
            lambda tag: title in clean_text(tag.get_text(" ", strip=True)),
        )
    if center is None:
        raise ContractParseError(f"Не найден раздел: {title}")
    return center


def find_value_after_label(soup: BeautifulSoup, label: str) -> str:
    """Ищет двухколоночное поле вида <td>Метка</td><td>Значение</td>."""
    label_cell = soup.find(
        "td",
        string=lambda value: value is not None and clean_text(value) == label,
    )
    if label_cell is None:
        # fallback для ячеек с вложенными элементами
        for td in soup.find_all("td"):
            if clean_text(td.get_text(" ", strip=True)) == label:
                label_cell = td
                break
    if label_cell is None:
        raise ContractParseError(f"Не найдено поле: {label}")

    value_cell = label_cell.find_next_sibling("td")
    if value_cell is None:
        raise ContractParseError(f"Не найдено значение поля: {label}")
    return cell_text(value_cell)


def parse_customer(soup: BeautifulSoup) -> str:
    """Извлекает полное наименование заказчика из печатной формы."""
    label_node = soup.find(
        string=lambda value: value is not None
        and "Наименование заказчика" in clean_text(value)
    )
    if label_node is None:
        raise ContractParseError("Не найдено наименование заказчика")

    parent = label_node.parent
    # У поля заказчика значение находится в span.data.
    container = parent.find_parent("p") if parent else None
    if container is None:
        raise ContractParseError("Не удалось определить контейнер заказчика")

    data = container.find("span", class_="data")
    if data is None:
        raise ContractParseError("Не найдено значение заказчика")
    return cell_text(data)


def parse_contract_info(soup: BeautifulSoup) -> dict[str, str]:
    return {
        "registry_number": find_value_after_label(soup, "Номер реестровой записи"),
        "customer": parse_customer(soup),
    }


def parse_purchase_objects(soup: BeautifulSoup) -> dict[str, dict[str, str]]:
    title = find_center(soup, "Раздел II. Объект закупки")
    table = title.find_next("table")
    if table is None:
        raise ContractParseError("Не найдена таблица 'Раздел II. Объект закупки'")

    result: dict[str, dict[str, str]] = {}

    for row in table.find_all("tr", recursive=False):
        cells = direct_cells(row)
        values = [cell_text(cell) for cell in cells]

        # Для основной таблицы ожидаем 10 колонок.
        if len(values) != 10:
            continue
        if values[0] in {"№ п/п", "Итого"}:
            continue
        if is_numbering_row(values):
            continue
        if not values[0].isdigit():
            continue

        object_no = values[0]
        result[object_no] = {
            "object_no": object_no,
            "purchase_object_name": values[1],
            "purchase_object_type": values[2],
            "ktru_okpd2": values[3],
            "quantity_unit": values[4],
            "characteristics": values[5],
            "unit_price": values[6],
            "vat_rate": values[7],
            "origin_country": values[8],
            "amount": values[9],
        }

    if not result:
        raise ContractParseError("В разделе II не найдено ни одной позиции закупки")
    return result


def _parse_drug_table(
    table: Tag,
    expected_columns: int,
    field_names: list[str],
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}

    for row in table.find_all("tr", recursive=False):
        cells = direct_cells(row)
        values = [cell_text(cell) for cell in cells]

        if len(values) != expected_columns:
            continue
        if not values:
            continue
        if values[0] == "№ п/п":
            continue
        if is_numbering_row(values):
            continue
        if not values[0].isdigit() or not values[1].isdigit():
            continue

        object_no = values[1]
        payload = {
            field_name: values[index + 2]
            for index, field_name in enumerate(field_names)
        }
        result[object_no] = payload

    return result


def parse_drug_details(soup: BeautifulSoup) -> dict[str, dict[str, str]]:
    """Объединяет три таблицы блока дополнительной информации по object_no."""
    title = find_center(soup, "Дополнительная информация о лекарственном препарате")
    table1 = title.find_next("table")
    if table1 is None:
        raise ContractParseError("Не найдена первая таблица лекарственных данных")

    table2 = table1.find_next("table")
    table3 = table2.find_next("table") if table2 is not None else None
    if table2 is None or table3 is None:
        raise ContractParseError("Не найдены все три таблицы лекарственных данных")

    part1 = _parse_drug_table(
        table1,
        expected_columns=8,
        field_names=[
            "inn",
            "trade_name",
            "dosage_form",
            "dosage",
            "grls_inn_release_form",
            "consumer_quantity",
        ],
    )

    part2 = _parse_drug_table(
        table2,
        expected_columns=6,
        field_names=[
            "registration_certificate",
            "registration_holder",
            "manufacturer",
            "manufacturer_country",
        ],
    )

    part3 = _parse_drug_table(
        table3,
        expected_columns=7,
        field_names=[
            "primary_package_type",
            "forms_per_primary_package",
            "primary_packages_per_consumer_package",
            "consumer_units_per_package",
            "package_completeness",
        ],
    )

    all_object_nos = set(part1) | set(part2) | set(part3)
    result: dict[str, dict[str, str]] = {}

    for object_no in sorted(all_object_nos, key=int):
        result[object_no] = {
            **part1.get(object_no, {}),
            **part2.get(object_no, {}),
            **part3.get(object_no, {}),
        }

    return result


def merge_drug_data(
    contract_info: dict[str, str],
    purchase_objects: dict[str, dict[str, str]],
    drug_details: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    for object_no in sorted(purchase_objects, key=int):
        row = {
            "registry_number": contract_info["registry_number"],
            "customer": contract_info["customer"],
            **purchase_objects[object_no],
            **drug_details.get(object_no, {}),
        }
        rows.append(row)

    return rows


def parse_contract_html(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    contract_info = parse_contract_info(soup)
    purchase_objects = parse_purchase_objects(soup)
    drug_details = parse_drug_details(soup)
    medicines = merge_drug_data(contract_info, purchase_objects, drug_details)

    return {
        "contract": contract_info,
        "medicines": medicines,
    }


def parse_contract_file(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    html = path.read_text(encoding="utf-8")
    return parse_contract_html(html)