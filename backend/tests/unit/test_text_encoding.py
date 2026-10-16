from app.domain.template_config import FieldMappingConfig
from app.domain.text_encoding import normalize_paddle_japanese_text, repair_utf8_mojibake
from app.layout_analysis.detector import _parse_paddle_result
from app.layout_analysis.models import LayoutCell, LayoutRecordSection


def test_repairs_utf8_text_misdecoded_as_latin1() -> None:
    corrupted = "牛繁殖台帳".encode().decode("latin-1")

    assert repair_utf8_mojibake(corrupted) == "牛繁殖台帳"
    assert repair_utf8_mojibake("牛繁殖台帳") == "牛繁殖台帳"
    assert repair_utf8_mojibake("cell_0001") == "cell_0001"


def test_normalizes_simplified_chinese_paddle_glyphs_to_japanese() -> None:
    assert normalize_paddle_japanese_text("壳却子牛一覧") == "売却子牛一覧"
    assert normalize_paddle_japanese_text("分娩状况") == "分娩状況"
    assert (
        normalize_paddle_japanese_text("产次 价格 备考 种雄牛名")
        == "産次 価格 備考 種雄牛名"
    )
    assert normalize_paddle_japanese_text("牛繁殖台帳") == "牛繁殖台帳"


def test_normalizes_paddle_text_when_parsing_detector_results() -> None:
    payload = {
        "rec_polys": [[(1, 2), (20, 2), (20, 15), (1, 15)]],
        "rec_texts": ["壳却日 分娩状况"],
        "rec_scores": [0.98],
    }

    regions = _parse_paddle_result(payload)

    assert regions[0].text == "売却日 分娩状況"


def test_repairs_paddle_layout_and_mapping_text_at_their_boundaries() -> None:
    corrupted_label = "分娩年月日".encode().decode("latin-1")
    payload = {
        "rec_polys": [[(1, 2), (20, 2), (20, 15), (1, 15)]],
        "rec_texts": [corrupted_label],
        "rec_scores": [0.98],
    }

    regions = _parse_paddle_result(payload)
    cell = LayoutCell(
        id="cell_0001",
        role="label",
        x=0,
        y=0,
        width=20,
        height=15,
        confidence=0.9,
        text=corrupted_label,
    )
    section = LayoutRecordSection(
        id="section_history",
        title="繁殖履歴".encode().decode("latin-1"),
        collectionPath="genericRecords.history",
        recordType="generic",
    )
    mapping = FieldMappingConfig(
        mappingId="cell_0001",
        targetPath="fields.eventDate",
        label=corrupted_label,
        x=0,
        y=0,
        width=20,
        height=15,
        printedLabelText=corrupted_label,
    )

    assert regions[0].text == "分娩年月日"
    assert cell.text == "分娩年月日"
    assert section.title == "繁殖履歴"
    assert mapping.label == "分娩年月日"
    assert mapping.printed_label_text == "分娩年月日"
