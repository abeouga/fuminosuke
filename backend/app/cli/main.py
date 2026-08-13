from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from app.core.config import Settings
from app.domain.sale_notes import SALE_NOTE_SEX_SYMBOLS
from app.domain.template_config import TemplateConfig, load_template_config
from app.evaluation.runner import run_evaluation
from app.extraction.handwriting_isolator import HandwritingIsolator
from app.extraction.image_variants import create_variants
from app.extraction.region_cropper import RegionCropper
from app.image_processing.artifacts import encode_png
from app.image_processing.document_boundary import DocumentNormalizer
from app.image_processing.registration import TemplateAligner
from app.ocr.base import OcrProvider, OcrProviderRequest, OcrRegionInput
from app.ocr.disabled_provider import DisabledOcrProvider
from app.ocr.mock_provider import MockOcrProvider
from app.ocr.openai_provider import OpenAIOcrProvider
from app.storage.atomic_writer import atomic_write_bytes, atomic_write_text
from app.template_recovery.pipelines import PreparedTemplate, TemplateRecoveryPipelines
from app.validation.business_validators import DeterministicValidator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="牛繁殖台帳 OCR ローカルCLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("recover-reference", "recover-single"):
        item = subparsers.add_parser(command)
        item.add_argument("--source", type=Path, required=True)
        _common_recovery_arguments(item)
    consensus = subparsers.add_parser("recover-consensus")
    consensus.add_argument("--inputs", nargs="+", type=Path, required=True)
    _common_recovery_arguments(consensus)
    extract = subparsers.add_parser("extract-document")
    extract.add_argument("--source", type=Path, required=True)
    extract.add_argument("--template", type=Path, required=True)
    extract.add_argument("--template-config", type=Path, required=True)
    extract.add_argument("--ocr-provider", choices=("mock", "openai", "disabled"), default="mock")
    extract.add_argument("--output", type=Path, required=True)
    extract.add_argument("--env-file", type=Path, default=Path(".env"))
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--dataset", type=Path, required=True)
    evaluate.add_argument("--report", type=Path, required=True)
    evaluate.add_argument("--baseline", type=Path)
    evaluate.add_argument("--env-file", type=Path, default=Path(".env"))
    return parser


def _common_recovery_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--template-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.load(args.env_file)
    if args.command == "evaluate":
        report = run_evaluation(
            settings=settings,
            dataset=args.dataset,
            report_path=args.report,
            baseline_path=args.baseline,
        )
        print(json.dumps(report["synthetic"], ensure_ascii=False, indent=2))
        return 0
    config = load_template_config(args.template_config)
    if args.command.startswith("recover-"):
        paths = args.inputs if args.command == "recover-consensus" else [args.source]
        images = [_read_image(path) for path in paths]
        pipelines = TemplateRecoveryPipelines(settings, config)
        if args.command == "recover-reference":
            result = pipelines.reference(images)
        elif args.command == "recover-consensus":
            result = pipelines.consensus(images)
        else:
            result = pipelines.single_experimental(images)
        _write_recovery(args.output, result)
        return 0
    _extract_document(
        source_path=args.source,
        template_path=args.template,
        config=config,
        provider_name=args.ocr_provider,
        output=args.output,
        settings=settings,
    )
    return 0


def _read_image(path: Path) -> NDArray[np.uint8]:
    content = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(content, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"画像を読み込めません: {path}")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.uint8)


def _write_recovery(output: Path, result: PreparedTemplate) -> None:
    output.mkdir(parents=True, exist_ok=True)
    _write_image(output / "clean_template.png", result.clean_template_rgb)
    _write_image(output / "preview.png", result.preview_rgb)
    if result.handwriting_mask is not None:
        _write_image(output / "handwriting_mask.png", result.handwriting_mask, rgb=False)
    for _, (file_name, image, rgb) in result.images.items():
        _write_image(output / file_name, image, rgb=rgb)
    _write_json(output / "quality.json", result.quality)


def _extract_document(
    *,
    source_path: Path,
    template_path: Path,
    config: TemplateConfig,
    provider_name: str,
    output: Path,
    settings: Settings,
) -> None:
    source = _read_image(source_path)
    template = _read_image(template_path)
    normalizer = DocumentNormalizer(
        canonical_width=config.canonical_width, canonical_height=config.canonical_height
    )
    normalized = normalizer.normalize(source)
    registered = TemplateAligner(settings.registration).align(
        normalized.image_rgb, template
    )
    isolated = HandwritingIsolator(threshold=settings.segmentation.threshold).isolate(
        registered.aligned_rgb, template
    )
    variants = create_variants(
        registered.aligned_rgb, template, isolated.handwriting_only_rgb
    )
    regions = (
        config.mapped_ocr_regions()
        if provider_name == "openai"
        else config.ocr_regions()
    )
    if provider_name == "openai" and not regions:
        raise ValueError(
            "OpenAI OCR requires template fieldMappings; legacy region inference is disabled"
        )
    crops = RegionCropper(config, regions).crop(variants)
    output.mkdir(parents=True, exist_ok=True)
    _write_image(output / "aligned_source.png", registered.aligned_rgb)
    _write_image(output / "handwriting_mask.png", isolated.mask, rgb=False)
    _write_image(output / "handwriting_only.png", isolated.handwriting_only_rgb)
    for region_id, region in crops.items():
        _write_image(output / "field_crops" / region_id / "aligned_color.png", region.aligned_color)
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id=(
                    f"cell_{index:04d}"
                    if provider_name == "openai"
                    else region_id
                ),
                image_png=encode_png(
                    region.handwriting_only
                    if region.config.printed_label_text.strip()
                    else region.aligned_color
                ),
                handwriting_png=(
                    None
                    if region.config.printed_label_text.strip()
                    else encode_png(region.handwriting_only)
                ),
                target_path=(
                    region.config.expected_fields[0]
                    if len(region.config.expected_fields) == 1
                    else None
                ),
                source_region_id=(
                    region_id if provider_name == "openai" else None
                ),
                bounding_box=region.bbox,
                candidates=(
                    SALE_NOTE_SEX_SYMBOLS
                    if len(region.config.expected_fields) == 1
                    and region.config.expected_fields[0].startswith("saleCalves.")
                    and region.config.expected_fields[0].endswith(".notes")
                    else ()
                ),
                candidate_type=(
                    "sale_calf_note_prefix"
                    if len(region.config.expected_fields) == 1
                    and region.config.expected_fields[0].startswith("saleCalves.")
                    and region.config.expected_fields[0].endswith(".notes")
                    else None
                ),
                transcription_format=(
                    "japanese_era_date_numbers"
                    if len(region.config.expected_fields) == 1
                    and region.config.expected_fields[0] == "document.birthDate"
                    else None
                ),
                printed_label_excluded=bool(
                    region.config.printed_label_text.strip()
                ),
                printed_label_text=region.config.printed_label_text.strip(),
            )
            for index, (region_id, region) in enumerate(crops.items(), start=1)
        ]
    )
    provider = _provider(provider_name, settings)
    response = asyncio.run(provider.extract(request))
    quality: dict[str, Any] = {
        **normalized.quality.to_dict(),
        **registered.quality.to_dict(),
        "handwritingCoverageRatio": isolated.coverage_ratio,
    }
    report = DeterministicValidator().validate(response.document, image_quality=quality)
    document = DeterministicValidator().merge_review_items(response.document, report)
    _write_json(
        output / "result.json",
        {
            "result": document.model_dump(mode="json", by_alias=True),
            "validation": report.to_dict(),
            "quality": quality,
            "ocr": {"provider": response.provider, "modelId": response.model_id},
        },
    )


def _provider(name: str, settings: Settings) -> OcrProvider:
    if name == "mock":
        return MockOcrProvider()
    if name == "disabled":
        return DisabledOcrProvider()
    return OpenAIOcrProvider(settings.ocr)


def _write_image(path: Path, image: NDArray[np.uint8], *, rgb: bool = True) -> None:
    atomic_write_bytes(path, encode_png(image, rgb=rgb))


def _write_json(path: Path, value: object) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
