from app.domain.template_config import FieldMappingConfig
from app.domain.text_encoding import repair_utf8_mojibake
from app.layout_analysis.detector import _parse_paddle_result
from app.layout_analysis.models import LayoutCell, LayoutRecordSection


def test_repairs_utf8_text_misdecoded_as_latin1() -> None:
    corrupted = "牛繁殖台帳".encode().decode("latin-1")

    assert repair_utf8_mojibake(corrupted) == "牛繁殖台帳"
    assert repair_utf8_mojibake("牛繁殖台帳") == "牛繁殖台帳"
    assert repair_utf8_mojibake("cell_0001") == "cell_0001"


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
        id="section_calvings",
        title="繁殖履歴".encode().decode("latin-1"),
        collectionPath="calvings",
        recordType="calving",
    )
    mapping = FieldMappingConfig(
        mappingId="cell_0001",
        targetPath="calvings.0.calvingDate",
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
