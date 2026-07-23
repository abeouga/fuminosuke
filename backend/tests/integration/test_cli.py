import json
from pathlib import Path

import cv2

from app.cli.main import main
from tests.synthetic.image_factory import ledger_image


def test_reference_recovery_cli(tmp_path: Path) -> None:
    source = tmp_path / "blank.png"
    success, encoded = cv2.imencode(
        ".png", cv2.cvtColor(ledger_image(), cv2.COLOR_RGB2BGR)
    )
    assert success
    source.write_bytes(encoded.tobytes())
    output = tmp_path / "output"
    config = (
        Path(__file__).resolve().parents[2]
        / "resources"
        / "templates"
        / "cow-breeding-ledger-v1"
        / "template.yaml"
    )
    assert (
        main(
            [
                "recover-reference",
                "--source",
                str(source),
                "--template-config",
                str(config),
                "--output",
                str(output),
                "--env-file",
                str(tmp_path / "missing.env"),
            ]
        )
        == 0
    )
    assert (output / "clean_template.png").is_file()
    quality = json.loads((output / "quality.json").read_text(encoding="utf-8"))
    assert quality["mode"] == "REFERENCE_TEMPLATE"


def test_evaluation_cli_generates_report(tmp_path: Path) -> None:
    dataset = tmp_path / "annotations"
    dataset.mkdir()
    (dataset / "manifest.json").write_text('{"samples": []}', encoding="utf-8")
    report = tmp_path / "report.json"
    assert (
        main(
            [
                "evaluate",
                "--dataset",
                str(dataset),
                "--report",
                str(report),
                "--env-file",
                str(tmp_path / "missing.env"),
            ]
        )
        == 0
    )
    value = json.loads(report.read_text(encoding="utf-8"))
    assert value["synthetic"]["registration"]["accepted"] is True
    assert 0 <= value["synthetic"]["handwritingMask"]["f1"] <= 1
