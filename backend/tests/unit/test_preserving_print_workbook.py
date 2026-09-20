from __future__ import annotations

import copy
import hashlib
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from app.core.errors import AppError
from app.services import print_xml
from app.services.preserving_print_workbook import ADAPTER, MAIN, REL, inspect
from app.services.print_workbook import workbook_copy
from tests.unit.test_print_workbook import case

S = "{" + MAIN + "}"


def rewrite(content, changed):
    buffer = BytesIO()
    with ZipFile(BytesIO(content)) as source, ZipFile(buffer, "w") as target:
        for entry in source.infolist():
            target.writestr(entry, changed.pop(entry.filename, source.read(entry)))
        for name, data in changed.items():
            target.writestr(name, data)
    return buffer.getvalue()


def advanced(*, prefixed=False):
    source, plan = case([15])
    with ZipFile(BytesIO(source)) as archive:
        book = archive.read("xl/workbook.xml")
        sheet = archive.read("xl/worksheets/sheet1.xml")
        rels = archive.read("xl/_rels/workbook.xml.rels")
    # Include a management sheet, passive extension, inert names, hidden rows,
    # and tail metadata that the old diagnostic adapter intentionally rejects.
    book = book.replace(
        b"</sheets>",
        (
            f'<sheet xmlns:r="{REL}" name="Management" sheetId="2" state="hidden" r:id="rId99"/>'
            "</sheets>"
        ).encode(),
    )
    book = book.replace(
        b"</definedNames>", b'<definedName name="inert">#REF!</definedName></definedNames>'
    )
    book = book.replace(
        b"</workbook>",
        (
            b'<extLst><ext uri="passive"><x15:workbookPr '
            b'xmlns:x15="http://schemas.microsoft.com/office/spreadsheetml/2010/11/main" '
            b'chartTrackingRefBase="1"/></ext></extLst></workbook>'
        ),
    )
    rels = rels.replace(
        b"</Relationships>",
        (
            f'<Relationship Id="rId99" Type="{REL}/worksheet" Target="worksheets/sheet2.xml"/>'
            "</Relationships>"
        ).encode(),
    )
    sheet = sheet.replace(b'<row r="8"', b'<row r="8" hidden="1"')
    sheet = sheet.replace(
        b"</worksheet>",
        b'<ignoredErrors><ignoredError sqref="B2" numberStoredAsText="1"/>'
        b"</ignoredErrors></worksheet>",
    )
    if prefixed:
        import re

        for name, data in (("book", book), ("sheet", sheet)):
            data = data.replace(f'xmlns="{MAIN}"'.encode(), f'xmlns:s="{MAIN}"'.encode(), 1)
            data = re.sub(rb"<(\/?)([A-Za-z][A-Za-z0-9]*)(?=[\s/>])", rb"<\1s:\2", data)
            if name == "book":
                book = data
            else:
                sheet = data
    source = rewrite(
        source,
        {
            "xl/workbook.xml": book,
            "xl/worksheets/sheet1.xml": sheet,
            "xl/_rels/workbook.xml.rels": rels,
            "xl/worksheets/sheet2.xml": (
                f'<worksheet xmlns="{MAIN}"><sheetData/></worksheet>'.encode()
            ),
        },
    )
    plan["sourceSha256"] = hashlib.sha256(source).hexdigest()
    plan["printAdapter"] = ADAPTER
    return source, plan


@pytest.mark.parametrize("prefixed", [False, True])
def test_print_copy_preserves_every_non_print_element_and_package_part(prefixed):
    source, plan = advanced(prefixed=prefixed)
    original_plan = copy.deepcopy(plan)
    result = workbook_copy(source, plan)
    assert plan == original_plan
    with ZipFile(BytesIO(source)) as before, ZipFile(BytesIO(result)) as after:
        assert before.namelist() == after.namelist()
        for name in before.namelist():
            if name not in {"xl/workbook.xml", "xl/worksheets/sheet1.xml"}:
                assert before.read(name) == after.read(name)
        for name, changed in (
            ("xl/workbook.xml", {"definedNames"}),
            (
                "xl/worksheets/sheet1.xml",
                {"sheetPr", "printOptions", "pageMargins", "pageSetup", "rowBreaks", "colBreaks"},
            ),
        ):
            a, b = before.read(name), after.read(name)
            old, new = print_xml.parse(a), print_xml.parse(b)
            for child in old.children:
                if child.name.rsplit("}", 1)[-1] in changed:
                    continue
                equivalent = [n for n in new.children if n.name == child.name]
                assert len(equivalent) == 1
                assert a[child.start : child.end] == b[equivalent[0].start : equivalent[0].end]
        root = ET.fromstring(after.read("xl/worksheets/sheet1.xml"))
        order = [node.tag.rsplit("}", 1)[-1] for node in root]
        assert order.index("pageSetup") < order.index("rowBreaks") < order.index("ignoredErrors")
        assert root.find(S + "sheetData/" + S + "row[@r='8']").get("hidden") == "1"
        book = ET.fromstring(after.read("xl/workbook.xml"))
        assert book.find(S + "definedNames/" + S + "definedName[@name='inert']").text == "#REF!"
    loaded = load_workbook(BytesIO(result))
    assert loaded["Management"].sheet_state == "hidden"
    assert [b.id for b in loaded["帳票"].row_breaks.brk] == [15]
    assert loaded["帳票"].page_setup.scale == 100


@pytest.mark.parametrize(
    "mutation",
    [
        "external",
        "formula",
        "unknown_extension",
        "defined_formula",
        "binary",
        "visible_sheet",
        "fractional",
        "unknown_adapter",
        "source",
        "range",
        "missing_page",
    ],
)
def test_unsafe_or_ambiguous_copies_fail_closed(mutation):
    source, plan = advanced()
    with ZipFile(BytesIO(source)) as archive:
        book = archive.read("xl/workbook.xml")
        rels = archive.read("xl/_rels/workbook.xml.rels")
    changed = {}
    if mutation == "external":
        changed["xl/_rels/workbook.xml.rels"] = rels.replace(
            b'Target="worksheets/sheet2.xml"',
            b'Target="https://example.invalid/a" TargetMode="External"',
        )
    elif mutation == "formula":
        changed["xl/worksheets/sheet2.xml"] = (
            f'<worksheet xmlns="{MAIN}"><sheetData><row r="1">'
            '<c r="A1"><f>1+1</f></c></row></sheetData></worksheet>'
        ).encode()
    elif mutation == "unknown_extension":
        changed["xl/workbook.xml"] = book.replace(b"x15:workbookPr", b"x15:unknownCommand")
    elif mutation == "defined_formula":
        changed["xl/workbook.xml"] = book.replace(
            b">#REF!<", b">WEBSERVICE(&quot;https://example.invalid&quot;)<"
        )
    elif mutation == "binary":
        changed["xl/vbaProject.bin"] = b"not executable fixture"
    elif mutation == "visible_sheet":
        changed["xl/workbook.xml"] = book.replace(b'state="hidden"', b'state="visible"')
    elif mutation == "fractional":
        plan["commonScale"] = 0.6957339287812906
    elif mutation == "unknown_adapter":
        plan["printAdapter"] = "unknown"
    elif mutation == "range":
        plan["pages"][1]["startRow"] += 1
    elif mutation == "missing_page":
        plan["pages"].pop()
    source = rewrite(source, changed)
    plan["sourceSha256"] = "0" * 64 if mutation == "source" else hashlib.sha256(source).hexdigest()
    with pytest.raises(AppError):
        workbook_copy(source, plan)


def test_printer_bytes_require_exact_content_type_and_are_preserved():
    source, plan = advanced()
    with ZipFile(BytesIO(source)) as archive:
        types = archive.read("[Content_Types].xml")
    name = "xl/printerSettings/printerSettings1.bin"
    data = b"\x00bounded opaque printer metadata\xff"
    changed = {name: data}
    invalid = rewrite(source, changed.copy())
    with pytest.raises(AppError):
        inspect(invalid)
    changed["[Content_Types].xml"] = types.replace(
        b"</Types>",
        (
            f'<Override PartName="/{name}" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.printerSettings"/>'
            "</Types>"
        ).encode(),
    )
    source = rewrite(source, changed)
    plan["sourceSha256"] = hashlib.sha256(source).hexdigest()
    with ZipFile(BytesIO(workbook_copy(source, plan))) as result:
        assert result.read(name) == data


@pytest.mark.parametrize(
    "data",
    [b'<a x="a>b"><b/></a>', b'<?xml version="1.0"?><a><!-- <b/> --><b><![CDATA[</b>]]></b></a>'],
)
def test_xml_span_boundaries_ignore_quotes_comments_and_cdata(data):
    root = print_xml.parse(data)
    child = root.children[0]
    assert data[root.start : root.end].endswith(b"</a>")
    changed = print_xml.edits(data, [(child.start, child.end, b"<c/>")])
    assert print_xml.parse(changed).children[0].qname == b"c"


def test_self_closing_names_and_entity_rejection():
    data = b"<a><names/></a>"
    root = print_xml.parse(data)
    result = print_xml.edits(data, [print_xml.insert_children(data, root.children[0], b"<b/>")])
    assert result == b"<a><names><b/></names></a>"
    with pytest.raises(ValueError):
        print_xml.parse(b'<!DOCTYPE a [<!ENTITY x "unsafe">]><a>&x;</a>')


@pytest.mark.parametrize(
    "value,allowed",
    [
        ('"A,B,C"', True),
        ("1", True),
        ("32", True),
        ('WEBSERVICE("x")', False),
        ("Sheet2!A1", False),
    ],
)
def test_only_literal_data_validation_is_preserved(value, allowed):
    source, plan = advanced()
    with ZipFile(BytesIO(source)) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml")
    validation = (
        '<dataValidations count="1"><dataValidation type="list" sqref="B2">'
        f"<formula1>{value}</formula1></dataValidation></dataValidations>"
    ).encode()
    sheet = sheet.replace(b"<pageMargins", validation + b"<pageMargins", 1)
    source = rewrite(source, {"xl/worksheets/sheet1.xml": sheet})
    plan["sourceSha256"] = hashlib.sha256(source).hexdigest()
    if allowed:
        result = workbook_copy(source, plan)
        with ZipFile(BytesIO(result)) as archive:
            assert validation in archive.read("xl/worksheets/sheet1.xml")
    else:
        with pytest.raises(AppError):
            workbook_copy(source, plan)
