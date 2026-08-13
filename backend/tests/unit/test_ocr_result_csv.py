from app.services.ocr_result_service import OcrResultService


def test_csv_cells_neutralize_spreadsheet_formulas() -> None:
    assert OcrResultService._csv_safe("=HYPERLINK(\"https://example.test\")") == (
        "'=HYPERLINK(\"https://example.test\")"
    )
    assert OcrResultService._csv_safe("\t+1+1") == "'\t+1+1"
    assert OcrResultService._csv_safe("-10") == "'-10"
    assert OcrResultService._csv_safe("@SUM(A1:A2)") == "'@SUM(A1:A2)"


def test_csv_cells_keep_normal_text_and_numbers() -> None:
    assert OcrResultService._csv_safe("勝忠平ET") == "勝忠平ET"
    assert OcrResultService._csv_safe("762300") == "762300"
    assert OcrResultService._csv_safe(762300) == 762300
