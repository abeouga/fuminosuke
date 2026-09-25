"""Add native Excel print settings absent from the spreadsheet authoring API."""

from pathlib import Path
from zipfile import ZipFile
import os

book = Path(__file__).with_name("記号判定テスト用_設備点検票_A4.xlsx")
temporary = book.with_suffix(".print-tmp")

with ZipFile(book, "r") as source, ZipFile(temporary, "w") as target:
    for entry in source.infolist():
        data = source.read(entry.filename)
        if entry.filename == "xl/workbook.xml":
            xml = data.decode("utf-8")
            assert "<x:definedNames>" not in xml
            xml = xml.replace(
                "</x:workbook>",
                '<x:definedNames><x:definedName name="_xlnm.Print_Area" '
                'localSheetId="0">\'設備点検票\'!$B$2:$D$13'
                "</x:definedName></x:definedNames></x:workbook>",
            )
            data = xml.encode("utf-8")
        elif entry.filename == "xl/worksheets/sheet1.xml":
            xml = data.decode("utf-8")
            assert "<x:pageSetup" not in xml
            assert "<x:pageMargins " in xml
            xml = xml.replace(
                "<x:sheetViews>",
                '<x:sheetPr><x:pageSetUpPr fitToPage="1" '
                'autoPageBreaks="0" /></x:sheetPr><x:sheetViews>',
            )
            xml = xml.replace(
                "<x:pageMargins ",
                '<x:printOptions horizontalCentered="1" gridLines="0" '
                'headings="0" /><x:pageMargins ',
            )
            xml = xml.replace(
                "</x:worksheet>",
                '<x:pageSetup paperSize="9" orientation="portrait" '
                'fitToWidth="1" fitToHeight="1" /></x:worksheet>',
            )
            data = xml.encode("utf-8")
        target.writestr(entry, data)

os.replace(temporary, book)
