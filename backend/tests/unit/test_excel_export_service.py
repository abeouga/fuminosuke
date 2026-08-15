from datetime import date, datetime
from io import BytesIO

from openpyxl import load_workbook

from app.services.excel_export_service import ExcelExportService


def _reading(raw: str | None, normalized: str | None = None) -> dict[str, object | None]:
    return {"raw": raw, "normalized": normalized}


def test_excel_table_prefers_normalized_values_and_skips_empty_fixed_rows() -> None:
    document = {
        "document": {
            "unifiedEarTag": _reading("0012345678"),
            "cattleName": _reading("raw-name", "よつば"),
            "birthDate": _reading("28 7 25", "2016年7月25日"),
        },
        "pedigree": {},
        "saleCalves": [
            {
                "parity": _reading("1"),
                "priceYen": _reading("850,000"),
                "weightKg": _reading("320.5"),
                "sireName": _reading("福之姫"),
            },
            {"parity": _reading("2")},
        ],
        "calvings": [],
        "customFields": {},
        "reviewItems": [],
    }
    table = ExcelExportService().build_table(
        document,
        config_snapshot={
            "recordStructure": {
                "sections": [
                    {
                        "collectionPath": "saleCalves",
                        "title": "売却子牛一覧".encode().decode("latin-1"),
                    },
                    {
                        "collectionPath": "calvings",
                        "title": "繁殖履歴".encode().decode("latin-1"),
                    },
                ]
            }
        },
        individual_id="0012345678",
        confirmed_at=datetime(2026, 8, 15),
    )

    cells = [cell for row in table.rows for cell in row.cells]
    assert any(cell.display == "よつば" for cell in cells)
    assert not any(cell.display == "raw-name" for cell in cells)
    assert sum(cell.display == "1" for cell in cells) == 1
    assert not any(cell.display == "2" for cell in cells)
    assert any(cell.display == "売却子牛一覧" for cell in cells)
    assert any(cell.display == "繁殖履歴" for cell in cells)
    assert table.file_name == "繁殖台帳_0012345678_よつば_20260815.xlsx"


def test_xlsx_preserves_types_formats_and_formula_like_text() -> None:
    document = {
        "document": {
            "unifiedEarTag": _reading("0012345678"),
            "birthDate": _reading("28 7 25", "2016年7月25日"),
            "cattleName": _reading("=1+1"),
        },
        "pedigree": {},
        "saleCalves": [
            {
                "parity": _reading("1"),
                "priceYen": _reading("850000"),
                "weightKg": _reading("320.5"),
                "sireName": _reading("福之姫"),
            }
        ],
        "calvings": [],
        "customFields": {},
        "reviewItems": [],
    }
    service = ExcelExportService()
    workbook = load_workbook(BytesIO(service.render_xlsx(service.build_table(document))))
    sheet = workbook["読み取り結果"]
    cells = [cell for row in sheet.iter_rows() for cell in row]

    identifier = next(cell for cell in cells if cell.value == "0012345678")
    formula_like = next(cell for cell in cells if cell.value == "=1+1")
    birth_date = next(
        cell
        for cell in cells
        if (cell.value.date() if isinstance(cell.value, datetime) else cell.value)
        == date(2016, 7, 25)
    )
    price = next(cell for cell in cells if cell.value == 850000)
    weight = next(cell for cell in cells if cell.value == 320.5)
    assert identifier.data_type == "s"
    assert identifier.number_format == "@"
    assert formula_like.data_type == "s"
    assert birth_date.number_format == 'yyyy"年"m"月"d"日"'
    assert price.number_format == '#,##0"円"'
    assert weight.number_format == '#,##0.###"kg"'
