"""Controlled train/holdout matrix. Run from backend; no OCR/remote APIs."""
from __future__ import annotations

import argparse
import importlib
import json
import math
import sys
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.print_layout import PrintLayoutRequest, page_plan
from app.services.geometry_calibration import compare_boundaries, grid_boundaries, statistics
from app.services.office_reference_renderer import render_office, sha
from app.services.print_layout_source import read_source
from app.services.workbook_geometry import apply_geometry, office_geometry


def definitions():
    base = dict(widths=[8.43] * 3, heights=[24] * 11, font="Calibri", fontSize=11,
                scale=100, origin=[2, 2], margins=[10, 10, 10, 10], center=False, merge=False)
    cases = []
    def add(name, split="calibration", **changes):
        cases.append({**base, "name": name, "split": split, **changes})
    add("default", widths=[None] * 5, heights=[None] * 10)
    add("explicit843")
    add("width-matrix", widths=[5, 8.43, 10, 12, 15, 20, 30], scale=70)
    add("height-matrix", heights=[10, 12, 15, 18, 20, 25, 30, 17.35] * 3)
    add("many-columns", widths=[5] * 15, scale=70)
    for scale in [95, 90, 80, 70]:
        add(f"scale-{scale}", scale=scale)
    add("margins", margins=[23, 7, 18, 12])
    add("centering", center=True, margins=[23, 7, 18, 12])
    add("origin", origin=[4, 7])
    for font in ["Arial", "Meiryo", "Yu Gothic", "Aptos"]:
        add("font-" + font.replace(" ", ""), font=font)
    add("font-size", fontSize=14)
    # These combinations are fixed before evaluating candidate models.
    add("holdout-mixed", "validation", widths=[6.25, 11.7, 9.125, 17.375, 7.6],
        heights=[11.25, 19.75, 22.35, 13.6, 27.1] * 3, scale=90)
    add("holdout-many", "validation", widths=[6.875] * 12,
        heights=[13.75] * 35, scale=80)
    add("holdout-bottom", "validation", heights=[25] * 27, margins=[12, 18, 29, 5], scale=95)
    add("holdout-center", "validation", widths=[12.25, 7.25, 16.5, 5.75],
        heights=[12.5, 23.75] * 8, scale=80, center=True, origin=[3, 5])
    add("holdout-merged", "validation", widths=[7.5, 11.25, 16.75], merge=True)
    add("holdout-right-bottom", "validation", widths=[15] * 4, heights=[25] * 20,
        margins=[80, 5, 80, 5])
    add("defaultColWidth", widths=[None] * 5, defaultWidth=10)
    add("column-flags", columnFlags=True)
    add("producer-hint", heights=[13.75] * 25, producerHint="LibreOffice")
    return cases


def workbook(case):
    xl = importlib.import_module("openpyxl")
    styles = importlib.import_module("openpyxl.styles")
    util = importlib.import_module("openpyxl.utils")
    book = xl.Workbook()
    sheet = book.active
    sheet.title = "Calibration"
    book._named_styles[0].font = styles.Font(name=case["font"], size=case["fontSize"])
    c1, r1 = case["origin"]
    c2, r2 = c1 + len(case["widths"]) - 1, r1 + len(case["heights"]) - 1
    sheet.print_area = f"{util.get_column_letter(c1)}{r1}:{util.get_column_letter(c2)}{r2}"
    if case.get("defaultWidth"):
        sheet.sheet_format.defaultColWidth = case["defaultWidth"]
    for c, width in enumerate(case["widths"], c1):
        if width is not None:
            sheet.column_dimensions[util.get_column_letter(c)].width = width
        if case.get("columnFlags"):
            sheet.column_dimensions[util.get_column_letter(c)].bestFit = True
            sheet.column_dimensions[util.get_column_letter(c)].font = styles.Font(
                name="Arial", size=18)
    for r, height in enumerate(case["heights"], r1):
        if height is not None:
            sheet.row_dimensions[r].height = height
        for c in range(c1, c2 + 1):
            sheet.cell(r, c).border = styles.Border(**{
                k: styles.Side(style="thin") for k in ("left", "right", "top", "bottom")})
    if case["merge"]:
        sheet.merge_cells(start_row=r1, end_row=r1, start_column=c1, end_column=c1 + 1)
    buffer = BytesIO()
    book.save(buffer)
    if case.get("producerHint"):
        # Controlled producer metadata experiment, NOT an actual LibreOffice-authored XLSX.
        from xml.etree import ElementTree as ET
        rewritten = BytesIO()
        with ZipFile(BytesIO(buffer.getvalue())) as archive, ZipFile(rewritten, "w") as output:
            for item in archive.infolist():
                data = archive.read(item)
                if item.filename == "docProps/app.xml":
                    root = ET.fromstring(data)
                    for node in root:
                        if node.tag.endswith("}Application"):
                            node.text = case["producerHint"]
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                output.writestr(item, data)
        return rewritten.getvalue()
    return buffer.getvalue()


def prediction(source, case, columns, rows):
    c1, r1 = case["origin"]
    widths = columns[c1 - 1:c1 - 1 + len(case["widths"])]
    heights = rows[r1 - 1:r1 - 1 + len(case["heights"])]
    left, right, top, bottom = case["margins"]
    scale = case["scale"] / 100
    px = 150 / 72
    ox, oy = left * 72 / 25.4, top * 72 / 25.4
    if case["center"]:
        ox += ((210 - left - right) * 72 / 25.4 - sum(widths) * scale) / 2
        oy += ((297 - top - bottom) * 72 / 25.4 - sum(heights) * scale) / 2
    x, y = [ox * px], [oy * px]
    for width in widths:
        x.append(x[-1] + width * scale * px)
    for height in heights:
        y.append(y[-1] + height * scale * px)
    return x, y


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    dataset = {"schemaVersion": 1, "productionQualified": False, "fixtures": []}
    if args.only and (args.output / "dataset.json").exists():
        dataset = json.loads((args.output / "dataset.json").read_text())
    for case in definitions():
        if args.only and case["name"] not in args.only:
            continue
        folder = args.output / case["name"]
        folder.mkdir(exist_ok=True)
        path = folder / "source.xlsx"
        content = path.read_bytes() if path.exists() else workbook(case)
        path.write_bytes(content)
        source = read_source(content, "Calibration", {"cells": []})
        dims = office_geometry(content, source)
        corrected = apply_geometry(source, dims)
        left, right, top, bottom = case["margins"]
        plan = page_plan(corrected, PrintLayoutRequest(base_revision="0" * 64, break_after_rows=[],
            margins=dict(left=left, right=right, top=top, bottom=bottom),
            horizontal_centering=case["center"], vertical_centering=case["center"]))
        # Explicit diagnostic scale; identical scale is sent to Office and prediction.
        plan["commonScale"] = case["scale"] / 100
        image_path = folder / "office.png"
        identity, images = render_office(content, plan)
        image_path.write_bytes(images[0])
        ax, ay = grid_boundaries(images[0], "x"), grid_boundaries(images[0], "y")
        font = dims["normalFont"]
        # Reference-device digit metrics independently queried through UNO before
        # this matrix. Missing/unmeasured fonts are not assigned fake metrics.
        mdw = {("Calibri", 11): 111, ("Arial", 11): 122, ("Meiryo", 11): 137,
               ("Yu Gothic", 11): 122}.get((font["family"], font["sizePt"]))
        models = {}
        c1, r1 = case["origin"]
        old_rows = [15.0] * (r1 - 1) + [h or 15 for h in case["heights"]]
        default_width = case.get("defaultWidth", 8)
        candidates = {"A-existing": [(w or default_width) * 7 * .75 + 5 * .75
                                     for w in case["widths"]],
                      "B-OOXML-7px": [math.floor((256 * (w if w is not None else
                                                        case.get("defaultWidth", 8 + 5 / 7))
                                      + math.floor(128 / 7)) / 256 * 7) * .75
                                     for w in case["widths"]]}
        if mdw:
            candidates["C-font-twip"] = [math.floor((w * mdw if w is not None else
                                                   default_width * mdw +
                                                   (0 if "defaultWidth" in case else 75)) + .5) / 20
                                          for w in case["widths"]]
        for name, widths in candidates.items():
            x, y = prediction(source, case, [0] * (c1 - 1) + widths, old_rows)
            models[name] = {"x": compare_boundaries(x, ax), "y": compare_boundaries(y, ay)}
        x, y = prediction(source, case, dims["columns"], dims["rows"])
        models["D-office-import"] = {"x": compare_boundaries(x, ax), "y": compare_boundaries(y, ay)}
        repeat = None
        if case["name"] in {"explicit843", "holdout-center"}:
            repeat_id, again = render_office(content, plan)
            repeat = identity == repeat_id and images == again
        row = {"definition": case, "sourceSha256": sha(content), "renderer": identity,
               "normalFont": font, "referenceSha256": sha(images[0]), "geometry": dims,
               "models": models, "repeatIdentical": repeat}
        dataset["fixtures"] = [f for f in dataset["fixtures"]
                               if f["definition"]["name"] != case["name"]]
        dataset["fixtures"].append(row)
        (folder / "measurement.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
        print(case["name"], font["family"], {n: (v["x"].get("statistics", {}).get("max"),
                                               v["y"].get("statistics", {}).get("max"))
                                          for n, v in models.items()}, flush=True)
    summary = {}
    for split in ["calibration", "validation"]:
        summary[split] = {}
        for model in ["A-existing", "B-OOXML-7px", "C-font-twip", "D-office-import"]:
            summary[split][model] = {}
            for axis in ["x", "y"]:
                errors = [b["signedError"] for row in dataset["fixtures"]
                          if row["definition"]["split"] == split
                          for b in row["models"].get(model, {}).get(axis, {}).get("boundaries", [])]
                summary[split][model][axis] = statistics(errors)
    dataset["summary"] = summary
    (args.output / "dataset.json").write_text(json.dumps(dataset, indent=2), encoding="utf-8")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
