"""Run the image part of a reading in the extraction Lambda (cloud), and its wire format.

The Lambda gets the source and template by their immutable S3 keys, runs run_pipeline() with
the settings sent here, and returns the PNGs the reading stores (encoded with encode_png, the
same bytes the API would produce) in one zip under scratch/. It never touches a database or
the object catalog: the API stores the images through its own storage exactly as before.
Anything unexpected (not configured, throttled, timed out, another code version) returns None
and the reading continues in the API process.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import zipfile
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from app.domain.template_config import RegionConfig, TemplateConfig
from app.extraction.pipeline import (
    CROP_VARIANTS,
    ImageItem,
    PipelineResult,
    PipelineSettings,
)
from app.extraction.region_cropper import RegionCrops
from app.image_processing.artifacts import encode_png
from app.image_processing.registration import RegistrationFailure

logger = logging.getLogger(__name__)

# Bump when the event or the reply changes; a Lambda on another version is not used.
PROTOCOL = 1
SCRATCH = "scratch/extract/"
# (name, file_name, png, width, height): ArtifactWriter.encoded_images items.
EncodedImage = tuple[str, str, bytes, int, int]


def encode_images(items: list[ImageItem]) -> list[EncodedImage]:
    return [
        (name, file, encode_png(pixels, rgb=rgb), pixels.shape[1], pixels.shape[0])
        for name, file, pixels, rgb in items
    ]


def decode_png(content: bytes, rgb: bool) -> NDArray[np.uint8]:
    """The exact array encode_png(array, rgb=rgb) was made from (PNG is lossless)."""
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError("Unreadable PNG in the extraction reply")
    if rgb and image.ndim == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(image, dtype=np.uint8)


def pack(images: list[EncodedImage], extra: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for index, (_name, _file, content, _w, _h) in enumerate(images):
            archive.writestr(f"i/{index}.png", content)
        for name, content in extra.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@dataclass(slots=True)
class RemoteOutcome:
    images: list[EncodedImage]
    result: PipelineResult | None = None
    failure: RegistrationFailure | None = None


class RemoteExtraction:
    def __init__(self, storage: Any, function: str, client: Any | None = None) -> None:
        self.storage, self.function = storage, function
        self._client = client

    @classmethod
    def configured(cls, storage: Any) -> RemoteExtraction | None:
        """Only for the cloud object storage, and only when the function is configured."""
        function = os.environ.get("BETA_EXTRACT_FUNCTION", "").strip()
        if not function or not hasattr(storage, "object_location"):
            return None
        return cls(storage, function)

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config

            region = os.environ.get("AWS_REGION") or "ap-northeast-1"
            # No SDK retry: a timed-out invocation may still be running, and the API then
            # reads the document itself.
            self._client = boto3.client("lambda", region_name=region, config=Config(
                retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=170,
            ))
        return self._client

    def run(
        self, *, job_id: str, attempt: int, settings: PipelineSettings,
        source_path: str, source_sha256: str, source_media_type: str | None,
        template_path: str, template_sha256: str, template: NDArray[np.uint8],
        config: TemplateConfig, crop_regions: list[RegionConfig], mapped: bool,
    ) -> RemoteOutcome | None:
        try:
            source = self.storage.object_location(source_path)
            reference = self.storage.object_location(template_path)
            if source["sha256"] != source_sha256 or reference["sha256"] != template_sha256:
                raise ValueError("Stored file differs from the accepted reading")
            event = {
                "protocol": PROTOCOL,
                "job": f"{job_id}/{attempt}",
                "bucket": self.storage.bucket,
                "source": {**source, "mediaType": source_media_type},
                "template": reference,
                "config": config.model_dump(mode="json", by_alias=True),
                "mapped": mapped,
                "cropRegionIds": [region.region_id for region in crop_regions],
                "settings": settings.dump(),
            }
            response = self.client.invoke(
                FunctionName=self.function, InvocationType="RequestResponse",
                Payload=json.dumps(event).encode(),
            )
            reply = json.loads(response["Payload"].read())
            if response.get("FunctionError") or reply.get("protocol") != PROTOCOL:
                raise RuntimeError(f"Extraction function failed: {str(reply)[:500]}")
            return self._outcome(reply, template, crop_regions)
        except Exception:
            logger.warning("Extraction Lambda unavailable; reading in the API",
                           extra={"jobId": job_id}, exc_info=True)
            return None

    def _outcome(
        self, reply: dict[str, Any], template: NDArray[np.uint8], crop_regions: list[RegionConfig],
    ) -> RemoteOutcome:
        bundle = reply["bundle"]
        if not str(bundle["key"]).startswith(SCRATCH):
            raise ValueError("Unexpected extraction bundle key")
        body = self.storage.client.get_object(Bucket=self.storage.bucket, Key=bundle["key"])["Body"]
        try:
            content = body.read(int(bundle["size"]) + 1)
        finally:
            body.close()
        try:
            self.storage.client.delete_object(Bucket=self.storage.bucket, Key=bundle["key"])
        except Exception:
            logger.warning("Extraction bundle left for the scratch lifecycle", exc_info=True)
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != bundle["size"] or digest != bundle["sha256"]:
            raise ValueError("Extraction bundle SHA mismatch")
        archive = zipfile.ZipFile(io.BytesIO(content))
        images: list[EncodedImage] = [
            (str(name), str(file), archive.read(f"i/{index}.png"), int(width), int(height))
            for index, (name, file, width, height) in enumerate(reply["images"])
        ]
        by_name = {item[0]: item for item in images}

        def pixels(name: str, rgb: bool) -> NDArray[np.uint8]:
            item = by_name[name]
            image = decode_png(item[2], rgb)
            if image.shape[1] != item[3] or image.shape[0] != item[4]:
                raise ValueError("Extraction image size mismatch")
            return image

        if reply["outcome"] == "registrationFailure":
            failure = RegistrationFailure(
                reply["failure"]["diagnostics"], pixels("registrationRejectedDebug", True),
            )
            failure.normalized_rgb = pixels("registrationNormalized", True)
            return RemoteOutcome(images=images, failure=failure)
        regions = {region.region_id: region for region in crop_regions}
        if [region_id for region_id, _ in reply["crops"]] != list(regions):
            raise ValueError("Extraction crops differ from the accepted regions")
        crops: dict[str, RegionCrops] = {}
        for index, (region_id, bbox) in enumerate(reply["crops"]):
            arrays = {
                file: pixels(f"fieldCrop.{region_id}.{name}", rgb)
                for name, file, rgb in CROP_VARIANTS
            }
            crops[region_id] = RegionCrops(
                config=regions[region_id],
                bbox=(int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])),
                ink_difference=decode_png(archive.read(f"k/{index}.png"), False),
                **arrays,
            )
        if not np.array_equal(pixels("cleanTemplate", True), template):
            raise ValueError("Extraction used another template image")
        result = PipelineResult(
            normalized=pixels("normalized", True),
            aligned=pixels("alignedSource", True),
            handwriting_probability=pixels("handwritingProbability", False),
            handwriting_mask=pixels("handwritingMask", False),
            handwriting_only=pixels("handwritingOnly", True),
            debug_overlay=pixels("debugOverlay", True),
            registration_debug=pixels("registrationDebug", True),
            crops=crops,
            marks=reply["marks"],
            quality_head=reply["qualityHead"],
            registration_diagnostics=reply["registrationDiagnostics"],
            photo_registration=reply["photoRegistration"],
            unknown=reply["unknown"],
        )
        return RemoteOutcome(images=images, result=result)
