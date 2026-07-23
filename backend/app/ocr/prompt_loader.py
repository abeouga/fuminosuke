from pathlib import Path


def load_ocr_prompt() -> str:
    path = (
        Path(__file__).resolve().parents[2]
        / "resources"
        / "templates"
        / "cow-breeding-ledger-v1"
        / "prompt.md"
    )
    return path.read_text(encoding="utf-8")


def load_transcription_prompt() -> str:
    path = (
        Path(__file__).resolve().parents[2]
        / "resources"
        / "templates"
        / "cow-breeding-ledger-v1"
        / "transcription-prompt.md"
    )
    return path.read_text(encoding="utf-8")
