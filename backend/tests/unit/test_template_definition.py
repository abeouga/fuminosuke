import asyncio
from types import SimpleNamespace

from pydantic import SecretStr

from app.core.config import OcrSettings
from app.domain.generic_data import build_generic_data, build_generic_values
from app.domain.generic_template_mapping import generic_field_locations
from app.domain.ocr_factory import blank_ocr_document
from app.domain.ocr_models import FieldReading, ReadingStatus
from app.domain.template_definition import (
    TemplateBox,
    TemplateCell,
    TemplateDefinition,
    TemplateField,
    TemplateRecord,
    TemplateSection,
)
from app.layout_analysis.geometry import analyze_geometry
from app.layout_analysis.models import (
    LayoutCell,
    LayoutDefinition,
    LayoutMarkOption,
    LayoutMarkSet,
    LayoutRecordGroup,
    LayoutRecordSection,
    LayoutRelation,
)
from app.services import relationship_analyzer
from app.services.excel_export_service import ExcelExportService
from app.services.relationship_analyzer import (
    ProposedField,
    RelationshipProposal,
    infer_relationships,
    infer_relationships_with_openai,
)
from app.services.template_definition_service import TemplateDefinitionService
from app.validation.template_definition_validator import validate_template_definition


def _cell(
    cell_id: str,
    cell_type: str,
    x: int,
    y: int,
    width: int = 80,
    height: int = 30,
    text: str = "",
) -> TemplateCell:
    return TemplateCell.model_validate(
        {
            "id": cell_id,
            "type": cell_type,
            "text": text,
            "bbox": {"x": x, "y": y, "width": width, "height": height},
            "detectionConfidence": 0.9,
        }
    )


def test_geometry_finds_direction_clusters_and_repeated_values() -> None:
    cells = [
        _cell("label-1", "LABEL", 10, 10, text="氏名"),
        _cell("value-1", "VALUE", 120, 10),
        _cell("label-2", "LABEL", 10, 60, text="住所"),
        _cell("value-2", "VALUE", 120, 60),
    ]
    geometry = {item.cell_id: item for item in analyze_geometry(cells, 400, 200)}

    assert geometry["value-1"].nearest_left == "label-1"
    assert geometry["value-1"].nearest_below == "value-2"
    assert geometry["value-1"].record_pattern_candidate is True
    assert geometry["value-1"].normalized_bbox.left == 300


def test_relationship_inference_binds_nearest_left_label() -> None:
    cells = [
        _cell("label", "LABEL", 10, 10, text="氏名"),
        _cell("value", "VALUE", 120, 10),
    ]
    geometry = analyze_geometry(cells, 400, 200)

    draft = infer_relationships(cells, geometry)

    assert len(draft.standalone_fields) == 1
    assert draft.standalone_fields[0].label_cell_id == "label"
    assert draft.standalone_fields[0].value_cell_id == "value"
    assert draft.unassigned_cell_ids == []


def test_openai_low_coverage_proposal_falls_back_to_complete_geometry(
    monkeypatch,
) -> None:
    cells = []
    for index in range(5):
        y = 10 + index * 45
        cells.extend(
            [
                _cell(f"label-{index}", "LABEL", 10, y, text=f"項目{index}"),
                _cell(f"value-{index}", "VALUE", 120, y),
            ]
        )
    geometry = analyze_geometry(cells, 400, 300)
    proposal = RelationshipProposal(
        fields=[
            ProposedField(
                id="ai-field-1",
                key="firstField",
                name="項目0",
                label_cell_id="label-0",
                value_cell_id="value-0",
                confidence=0.9,
            )
        ],
        records=[],
        sections=[],
    )

    class FakeResponses:
        async def parse(self, **_kwargs):
            return SimpleNamespace(output_parsed=proposal)

    class FakeClient:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    monkeypatch.setattr(relationship_analyzer, "AsyncOpenAI", FakeClient)
    settings = OcrSettings(
        provider="openai",
        openai_api_key=SecretStr("test-key"),
        primary_model="test-model",
        fallback_model=None,
        image_detail="high",
        reasoning_effort="low",
        max_calls_per_job=1,
        max_output_tokens=4000,
        timeout_seconds=30,
        max_retries=0,
        prompt_version="test",
        schema_version="1.0",
    )

    draft = asyncio.run(infer_relationships_with_openai(cells, geometry, settings))

    assert len(draft.standalone_fields) == 5
    assert "AI_RELATIONSHIP_INFERENCE_LOW_FIELD_COVERAGE_FALLBACK" in draft.warnings


def test_validator_rejects_duplicate_value_ownership() -> None:
    cells = [
        _cell("label", "LABEL", 10, 10, text="氏名"),
        _cell("value", "VALUE", 120, 10),
    ]
    fields = [
        TemplateField(
            id="field_001",
            key="name",
            name="氏名",
            label_cell_id="label",
            value_cell_id="value",
            confidence=0.9,
        ),
        TemplateField(
            id="field_002",
            key="nameCopy",
            name="氏名控え",
            label_cell_id="label",
            value_cell_id="value",
            confidence=0.8,
        ),
    ]
    definition = TemplateDefinition(
        template_version_id="version-1",
        template_name="試験帳票",
        canonical_width=400,
        canonical_height=200,
        cells=cells,
        geometry=analyze_geometry(cells, 400, 200),
        standalone_fields=fields,
        unassigned_cell_ids=[],
        confidence=0.8,
    )

    report = validate_template_definition(definition)

    assert report.valid is False
    assert "DUPLICATE_VALUE_OWNERSHIP" in {issue.code for issue in report.issues}


def test_legacy_mark_sets_preserve_multiple_fields_in_one_rectangle() -> None:
    label = LayoutCell(
        id="status-label",
        role="label",
        x=10,
        y=10,
        width=80,
        height=30,
        confidence=1,
        text="分娩状況",
    )
    mark = LayoutCell(
        id="status-mark",
        role="mark",
        x=100,
        y=10,
        width=180,
        height=30,
        confidence=1,
        mark_sets=[
            LayoutMarkSet(
                id="outcome",
                target_path="calvings.0.outcome",
                options=[
                    LayoutMarkOption(
                        id="twin", label="双", value="twin", x=0, y=0, width=40, height=30
                    )
                ],
            ),
            LayoutMarkSet(
                id="sex",
                target_path="calvings.0.sex",
                options=[
                    LayoutMarkOption(
                        id="male", label="♂", value="male", x=100, y=0, width=40, height=30
                    )
                ],
            ),
        ],
    )
    layout = LayoutDefinition(
        canonical_width=320,
        canonical_height=100,
        cells=[label, mark],
        relations=[
            LayoutRelation(
                id="status-relation",
                input_cell_id=mark.id,
                label_cell_ids=[label.id],
                axis="horizontal",
                order=1,
                target_path="calvings.0.outcome",
            )
        ],
        record_sections=[
            LayoutRecordSection(
                id="calvings",
                title="繁殖履歴",
                collection_path="calvings",
                record_type="calving",
            )
        ],
        record_groups=[
            LayoutRecordGroup(
                id="calving-1",
                section_id="calvings",
                order=1,
                x=mark.x,
                y=mark.y,
                width=mark.width,
                height=mark.height,
                input_cell_ids=[mark.id],
                record_key_value="1",
            )
        ],
        record_structure_verified=True,
    )
    cells = TemplateDefinitionService._cells_from_layout(layout)
    geometry = analyze_geometry(cells, layout.canonical_width, layout.canonical_height)

    draft = infer_relationships(cells, geometry, layout)
    fields = draft.sections[0].records[0].fields
    definition = TemplateDefinition(
        template_version_id="version-1",
        template_name="分娩記録",
        canonical_width=layout.canonical_width,
        canonical_height=layout.canonical_height,
        cells=cells,
        geometry=geometry,
        sections=draft.sections,
        unassigned_cell_ids=draft.unassigned_cell_ids,
        confidence=0.99,
    )

    assert [field.key for field in fields] == ["outcome", "sex"]
    assert [field.mark_set_id for field in fields] == ["outcome", "sex"]
    assert validate_template_definition(definition).valid is True


def test_generic_definition_builds_data_and_excel_rows() -> None:
    cells = [
        _cell("label", "LABEL", 10, 10, text="氏名"),
        _cell("value", "VALUE", 120, 10),
    ]
    field = TemplateField(
        id="field_001",
        key="name",
        name="氏名",
        label_cell_id="label",
        value_cell_id="value",
        confidence=0.9,
    )
    definition = TemplateDefinition(
        template_version_id="version-1",
        template_name="申込書",
        state="APPROVED",
        canonical_width=400,
        canonical_height=200,
        cells=cells,
        geometry=analyze_geometry(cells, 400, 200),
        standalone_fields=[field],
        confidence=0.9,
    )
    location = next(generic_field_locations(definition))
    document = blank_ocr_document(provider="test", source_regions=[location.mapping_key])
    document.custom_fields[location.mapping_key] = FieldReading(
        raw="山田太郎",
        normalized="山田太郎",
        status=ReadingStatus.READABLE,
        source_region_id=location.mapping_key,
    )

    document.generic_data = build_generic_data(
        document, definition.model_dump(mode="json", by_alias=True)
    )
    document.generic_values = build_generic_values(document.generic_data)
    table = ExcelExportService().build_table(document.model_dump(mode="json", by_alias=True))

    assert document.generic_data is not None
    assert document.generic_data.standalone_fields[0].reading.normalized == "山田太郎"
    assert document.generic_values == {"name": "山田太郎"}
    assert table.file_name.startswith("申込書_")
    assert any(cell.display == "山田太郎" for row in table.rows for cell in row.cells)


def test_generic_data_keeps_repeated_column_field_ids_scoped_to_each_record() -> None:
    cells = [
        _cell("price-1", "VALUE", 10, 10),
        _cell("price-2", "VALUE", 10, 50),
    ]
    records = [
        TemplateRecord(
            id=f"record-{index}",
            name=f"行{index}",
            order=index,
            bbox=TemplateBox(x=0, y=index * 40, width=100, height=40),
            fields=[
                TemplateField(
                    id="price",
                    key="priceYen",
                    name="価格",
                    value_cell_id=f"price-{index}",
                    value_type="integer",
                    confidence=1,
                )
            ],
            confidence=1,
        )
        for index in (1, 2)
    ]
    definition = TemplateDefinition(
        template_version_id="version-1",
        template_name="価格表",
        state="APPROVED",
        canonical_width=200,
        canonical_height=200,
        cells=cells,
        geometry=analyze_geometry(cells, 200, 200),
        sections=[
            TemplateSection(
                id="sales",
                key="sales",
                name="売上",
                bbox=TemplateBox(x=0, y=0, width=100, height=100),
                records=records,
                confidence=1,
            )
        ],
        confidence=1,
    )
    locations = list(generic_field_locations(definition))
    document = blank_ocr_document(
        provider="test", source_regions=[location.mapping_key for location in locations]
    )
    for location, raw in zip(locations, ("540000", "770000"), strict=True):
        document.custom_fields[location.mapping_key] = FieldReading(
            raw=raw,
            normalized=raw,
            status=ReadingStatus.READABLE,
            source_region_id=location.mapping_key,
        )

    data = build_generic_data(document, definition.model_dump(mode="json", by_alias=True))
    values = build_generic_values(data)

    assert values == {"sales": [{"priceYen": 540000}, {"priceYen": 770000}]}


def test_generic_layout_uses_printed_row_numbers_and_excludes_empty_rows() -> None:
    labels = [
        LayoutCell(id="vehicle-label", role="label", x=80, y=0, width=100, height=30, confidence=1, text="車種名"),
        LayoutCell(id="chassis-label", role="label", x=180, y=0, width=100, height=30, confidence=1, text="車台番号"),
    ]
    row_keys = [
        LayoutCell(id=f"row-{order}", role="record_key_cell", x=40, y=30 + (order - 1) * 40, width=40, height=40, confidence=1, text=str(order))
        for order in (1, 2)
    ]
    inputs = [
        LayoutCell(id=f"{field}-{order}", role="input", x=x, y=30 + (order - 1) * 40, width=100, height=40, confidence=1)
        for order in (1, 2)
        for field, x in (("vehicle", 80), ("chassis", 180))
    ]
    relations = [
        LayoutRelation(
            id=f"{field}-{order}-relation",
            input_cell_id=f"{field}-{order}",
            label_cell_ids=[f"{field}-label"],
            axis="vertical",
            order=order,
            target_path=f"customFields.{label}_{order}",
        )
        for order in (1, 2)
        for field, label in (("vehicle", "車種名"), ("chassis", "車台番号"))
    ]
    layout = LayoutDefinition(
        canonical_width=320,
        canonical_height=130,
        cells=[*labels, *row_keys, *inputs],
        relations=relations,
        record_sections=[
            LayoutRecordSection(
                id="vehicle-lines",
                title="車両明細",
                collection_path="genericRecords.vehicleLines",
                record_type="generic",
                record_label_cell_ids=["vehicle-label", "chassis-label"],
                row_key_strategy="printed",
                row_key_cell_ids=["row-1", "row-2"],
                include_empty_rows=False,
            )
        ],
        record_groups=[
            LayoutRecordGroup(
                id=f"vehicle-line-{order}",
                section_id="vehicle-lines",
                order=order,
                x=80,
                y=30 + (order - 1) * 40,
                width=200,
                height=40,
                input_cell_ids=[f"vehicle-{order}", f"chassis-{order}"],
                record_key_field="rowNumber",
                record_key_value=str(order),
                record_key_cell_id=f"row-{order}",
            )
            for order in (1, 2)
        ],
        record_structure_verified=True,
    )
    template_cells = TemplateDefinitionService._cells_from_layout(layout)
    draft = infer_relationships(
        template_cells,
        analyze_geometry(template_cells, 320, 130),
        layout,
    )
    section = draft.sections[0]
    assert section.key == "vehicleLines"
    assert section.parent_label_cell_id is None
    assert [field.key for field in section.records[0].fields] == [
        field.key for field in section.records[1].fields
    ]

    definition = TemplateDefinition(
        template_version_id="version-1",
        template_name="領収書",
        state="APPROVED",
        canonical_width=320,
        canonical_height=130,
        cells=template_cells,
        geometry=analyze_geometry(template_cells, 320, 130),
        sections=draft.sections,
        confidence=1,
    )
    locations = list(generic_field_locations(definition))
    document = blank_ocr_document(
        provider="test",
        source_regions=[location.mapping_key for location in locations],
    )
    for location in locations:
        if location.record and location.record.order == 1:
            raw = "CT125" if location.field.name == "車種名" else "JA55-1020927"
            document.custom_fields[location.mapping_key] = FieldReading(
                raw=raw,
                normalized=raw,
                status=ReadingStatus.READABLE,
                source_region_id=location.mapping_key,
            )

    data = build_generic_data(document, definition.model_dump(mode="json", by_alias=True))
    assert data is not None
    assert len(data.sections[0].records) == 1
    assert data.sections[0].records[0].record_key_value == "1"
