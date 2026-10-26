"""The extraction Lambda: the API's container image with this module as its command.

  python -m app.cloud.extract_worker

It implements the Lambda runtime API itself (no extra package), reads the source and template
by their immutable keys, runs the same run_pipeline() the API runs, and writes the reply's
PNGs as one zip under scratch/ (see app.extraction.remote). No database, no API keys.
"""
from __future__ import annotations

import hashlib
import json
import os
import traceback
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from app.domain.template_config import TemplateConfig
from app.extraction.pipeline import (
    PipelineSettings,
    artifact_images,
    failure_images,
    run_pipeline,
)
from app.extraction.remote import PROTOCOL, SCRATCH, encode_images, pack
from app.image_processing.artifacts import encode_png
from app.image_processing.image_loader import ImageLoader
from app.image_processing.registration import RegistrationFailure

MAX_INPUT = 64 * 1024**2


def fetch(s3: Any, bucket: str, item: dict[str, Any]) -> bytes:
    key, sha, size = str(item["key"]), str(item["sha256"]), int(item["size"])
    if "/tenants/" not in key or "/objects/" not in key or not 0 <= size <= MAX_INPUT:
        raise ValueError("The extraction reads stored business files only")
    body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    try:
        content = body.read(size + 1)
    finally:
        body.close()
    if len(content) != size or hashlib.sha256(content).hexdigest() != sha:
        raise ValueError("Stored file SHA mismatch")
    return bytes(content)


def cpu() -> dict[str, Any]:
    """What OpenCV's optimized code paths depend on (they must match the API's host)."""
    import cv2

    info = Path("/proc/cpuinfo")
    lines = info.read_text(encoding="utf-8").splitlines() if info.exists() else []
    model = next((line.split(":", 1)[1].strip() for line in lines
                  if line.startswith("model name")), "")
    return {"model": model, "cpus": os.cpu_count(), "opencv": cv2.__version__,
            "features": cv2.getCPUFeaturesLine(),
            "disabled": os.environ.get("OPENCV_CPU_DISABLE", "")}


def handle(event: dict[str, Any], s3: Any) -> dict[str, Any]:
    if event.get("protocol") != PROTOCOL:
        raise ValueError("Another extraction protocol version")
    if event.get("probe"):
        return {"protocol": PROTOCOL, "cpu": cpu()}
    bucket = str(event["bucket"])
    settings = PipelineSettings.load(event["settings"])
    config = TemplateConfig.model_validate(event["config"])
    # The same regions the API chose (mapped OCR or configured regions), in the same order.
    candidates = {
        r.region_id: r
        for r in (config.mapped_ocr_regions() if event["mapped"] else config.ocr_regions())
    }
    ids = [str(region_id) for region_id in event["cropRegionIds"]]
    if any(region_id not in candidates for region_id in ids):
        raise ValueError("Unknown crop region")
    regions = [candidates[region_id] for region_id in ids]
    loader = ImageLoader(settings.storage, settings.image_processing)
    template = loader.load(fetch(s3, bucket, event["template"]), "image/png").rgb
    source = fetch(s3, bucket, event["source"])
    reply: dict[str, Any] = {"protocol": PROTOCOL}
    extra: dict[str, bytes] = {}
    try:
        result = run_pipeline(
            settings, source_bytes=source, source_media_type=event["source"].get("mediaType"),
            template=template, config=config, crop_regions=regions,
        )
    except RegistrationFailure as failure:
        images = encode_images(failure_images(failure, template))
        reply.update(outcome="registrationFailure", failure={"diagnostics": failure.metadata})
    else:
        images = encode_images(artifact_images(result, template))
        crops = list(result.crops.items())
        extra = {
            f"k/{index}.png": encode_png(region.ink_difference, rgb=False)
            for index, (_region_id, region) in enumerate(crops)
        }
        reply.update(
            outcome="ok",
            crops=[[region_id, list(region.bbox)] for region_id, region in crops],
            marks=result.marks,
            qualityHead=result.quality_head,
            registrationDiagnostics=result.registration_diagnostics,
            photoRegistration=result.photo_registration,
            unknown=result.unknown,
        )
    bundle = pack(images, extra)
    key = f"{SCRATCH}{uuid.uuid4().hex}.zip"
    s3.put_object(Bucket=bucket, Key=key, Body=bundle, ServerSideEncryption="AES256")
    reply["bundle"] = {
        "key": key, "size": len(bundle), "sha256": hashlib.sha256(bundle).hexdigest(),
    }
    reply["images"] = [[name, file, width, height] for name, file, _png, width, height in images]
    return reply


def main() -> None:
    import boto3
    from botocore.config import Config

    api = "http://" + os.environ["AWS_LAMBDA_RUNTIME_API"] + "/2018-06-01/runtime"
    s3 = boto3.client("s3", config=Config(
        retries={"total_max_attempts": 2}, connect_timeout=5, read_timeout=30,
    ))

    def post(path: str, body: dict[str, Any], error: bool = False) -> None:
        request = urllib.request.Request(api + path, data=json.dumps(body).encode(),
                                         method="POST")
        if error:
            request.add_header("Lambda-Runtime-Function-Error-Type", "Unhandled")
        urllib.request.urlopen(request).read()

    while True:
        with urllib.request.urlopen(api + "/invocation/next") as response:
            request_id = response.headers["Lambda-Runtime-Aws-Request-Id"]
            event = json.loads(response.read())
        try:
            reply = handle(event, s3)
        except Exception as exc:
            print(json.dumps({"job": event.get("job"), "error": traceback.format_exc()[-4000:]}))
            post(f"/invocation/{request_id}/error",
                 {"errorType": type(exc).__name__, "errorMessage": str(exc)[:1000]}, error=True)
            continue
        print(json.dumps({"job": event.get("job"), "outcome": reply.get("outcome")}))
        post(f"/invocation/{request_id}/response", reply)


if __name__ == "__main__":
    main()
