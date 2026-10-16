from __future__ import annotations

import io
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
import yaml
from PIL import Image

from app.core.config import Settings
from app.core.errors import AppError
from app.db.base import Base
from app.db.models import JobArtifact, ProcessingJob, Template, TemplateVersion
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, TemplateMode, TemplateVersionStatus
from app.domain.template_config import FieldMappingConfig
from app.layout_analysis.models import LayoutCell, LayoutDefinition, LayoutRelation
from app.repositories.template_repository import TemplateRepository
from app.services.layout_service import LayoutService, _HandwritingFinalization
from app.services.template_management_service import TemplateManagementService
from app.storage.local_storage import LocalStorageService


def _layout(*, label: str = "Cow name") -> LayoutDefinition:
    return LayoutDefinition(
        canonical_width=64,
        canonical_height=48,
        detector="manual",
        cells=[
            LayoutCell(
                id="label",
                role="label",
                x=2,
                y=2,
                width=16,
                height=12,
                confidence=1,
                text=label,
            ),
            LayoutCell(
                id="input",
                role="input",
                x=20,
                y=2,
                width=40,
                height=12,
                confidence=1,
            ),
        ],
        relations=[
            LayoutRelation(
                id="input_horizontal",
                input_cell_id="input",
                label_cell_ids=["label"],
                axis="horizontal",
                order=1,
                target_path="document.cattleName",
            )
        ],
    )


def _mask_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("L", (64, 48), 255).save(buffer, format="PNG")
    return buffer.getvalue()


def _seed_version(
    settings_factory: Callable[..., Settings],
) -> tuple[Database, LocalStorageService, str, LayoutDefinition, str, str]:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    layout = _layout()
    with database.session() as session:
        job = ProcessingJob(
            job_type=JobType.PREPARE_TEMPLATE.value,
            status=JobStatus.REVIEW_REQUIRED.value,
            request_hash="c" * 64,
            config_snapshot_json={},
        )
        template = Template(
            name="verification-concurrency",
            document_type="generic-document",
        )
        session.add_all([job, template])
        session.flush()
        clean_artifact = JobArtifact(
            job_id=job.id,
            attempt_number=1,
            artifact_name="seedClean",
            media_type="image/png",
            relative_path="artifacts/seed-clean.png",
            sha256="d" * 64,
            width=64,
            height=48,
        )
        mask_artifact = JobArtifact(
            job_id=job.id,
            attempt_number=1,
            artifact_name="seedMask",
            media_type="image/png",
            relative_path="artifacts/seed-mask.png",
            sha256="e" * 64,
            width=64,
            height=48,
        )
        session.add_all([clean_artifact, mask_artifact])
        session.flush()
        config_path = f"templates/{template.id}/versions/1/template.yaml"
        version = TemplateVersion(
            template_id=template.id,
            version_number=1,
            mode=TemplateMode.REFERENCE_TEMPLATE.value,
            status=TemplateVersionStatus.DRAFT.value,
            canonical_width=64,
            canonical_height=48,
            source_job_id=job.id,
            clean_template_artifact_id=clean_artifact.id,
            mask_artifact_id=mask_artifact.id,
            config_relative_path=config_path,
            quality_json={
                "layoutFinalized": True,
                "layoutVerified": True,
                "layoutVerificationCandidateCount": 77,
            },
            layout_json=layout.model_dump(mode="json", by_alias=True),
        )
        session.add(version)
        session.commit()
        version_id = version.id
        template_id = template.id
        clean_artifact_id = clean_artifact.id
        mask_artifact_id = mask_artifact.id
    config = {
        "templateId": template_id,
        "templateVersion": 1,
        "name": "verification-concurrency",
        "canonicalWidth": 64,
        "canonicalHeight": 48,
        "registration": {
            "preferredMethod": "ORB_HOMOGRAPHY",
            "minFeatureMatches": 4,
            "minInliers": 4,
            "minInlierRatio": 0.5,
            "maxReprojectionErrorPx": 5,
            "minEccScore": 0.5,
        },
        "regions": [],
        "fieldMappings": [
            {
                "mappingId": "layout_input",
                "targetPath": "document.cattleName",
                "label": "Cow name",
                "x": 20,
                "y": 2,
                "width": 40,
                "height": 12,
                "padding": 4,
                "enabled": True,
                "modelDetail": "high",
            }
        ],
        "marks": [],
        "export": {"csvColumns": []},
    }
    storage.write_text(
        config_path,
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
    )
    return (
        database,
        storage,
        version_id,
        layout,
        clean_artifact_id,
        mask_artifact_id,
    )


def _finalization(clean_artifact_id: str, mask_artifact_id: str) -> _HandwritingFinalization:
    return _HandwritingFinalization(
        detector="test-detector",
        clean_artifact_id=clean_artifact_id,
        mask_artifact_id=mask_artifact_id,
        mask_source="TEST_FINALIZATION",
        edited_mask_applied=False,
        edited_mask_applied_sha256=None,
        input_cell_count=1,
        coverage_ratio=0.25,
        paddle_ocr_available=False,
        opencv_applied=False,
        candidate_count=999,
    )


def test_successful_verification_stores_its_input_fingerprint(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        database,
        storage,
        version_id,
        _layout_before,
        clean_artifact_id,
        mask_artifact_id,
    ) = _seed_version(settings_factory)
    try:
        monkeypatch.setattr(
            LayoutService,
            "_finalize_handwriting",
            lambda _self, _version_id, _layout: _finalization(
                clean_artifact_id,
                mask_artifact_id,
            ),
        )

        LayoutService(database, storage).verify(version_id)

        with database.session() as session:
            version = TemplateRepository(session).get_version(version_id)
            assert version is not None
            assert version.quality_json["layoutVerified"] is True
            assert version.quality_json["layoutVerificationDetector"] == "test-detector"
            assert version.quality_json["layoutVerificationCandidateCount"] == 999
            fingerprint = version.quality_json["layoutVerificationFingerprint"]
            assert isinstance(fingerprint, str)
            assert len(fingerprint) == 64
    finally:
        database.dispose()


@pytest.mark.parametrize("mutation", ["layout", "mapping", "mask"])
def test_verification_rejects_inputs_changed_by_another_thread(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    (
        database,
        storage,
        version_id,
        layout_before,
        _clean_artifact_id,
        _mask_artifact_id,
    ) = _seed_version(settings_factory)
    verification_started = Event()
    allow_verification_to_finish = Event()

    def paused_finalization(
        _service: LayoutService,
        _version_id: str,
        _layout_value: LayoutDefinition,
    ) -> _HandwritingFinalization:
        verification_started.set()
        if not allow_verification_to_finish.wait(timeout=5):
            raise AssertionError("verification test did not resume")
        return _finalization("stale-clean-artifact", "stale-mask-artifact")

    monkeypatch.setattr(LayoutService, "_finalize_handwriting", paused_finalization)
    service = LayoutService(database, storage)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            verification = executor.submit(service.verify, version_id)
            assert verification_started.wait(timeout=5)
            with database.session() as session:
                in_progress = TemplateRepository(session).get_version(version_id)
                assert in_progress is not None
                assert in_progress.quality_json["layoutVerified"] is False
                assert "layoutVerificationFingerprint" not in in_progress.quality_json

            try:
                if mutation == "layout":
                    service.save(version_id, _layout(label="Updated cow name"))
                elif mutation == "mapping":
                    TemplateManagementService(database, storage).save_field_mappings(
                        version_id,
                        [
                            FieldMappingConfig(
                                mapping_id="layout_input",
                                target_path="document.cattleName",
                                label="Updated mapping",
                                x=20,
                                y=2,
                                width=40,
                                height=12,
                            )
                        ],
                    )
                else:
                    TemplateManagementService(database, storage).save_edited_mask(
                        version_id,
                        _mask_png(),
                    )
                with database.session() as session:
                    mutated = TemplateRepository(session).get_version(version_id)
                    assert mutated is not None
                    expected_clean_artifact_id = mutated.clean_template_artifact_id
                    expected_mask_artifact_id = mutated.mask_artifact_id
            finally:
                allow_verification_to_finish.set()

            with pytest.raises(AppError) as caught:
                verification.result(timeout=5)

        assert caught.value.status == 409
        assert caught.value.code == "LAYOUT_VERIFICATION_CONFLICT"
        with database.session() as session:
            version = TemplateRepository(session).get_version(version_id)
            assert version is not None
            assert version.quality_json["layoutVerified"] is False
            assert "layoutVerificationFingerprint" not in version.quality_json
            assert version.quality_json["layoutInputRevision"] >= 1
            assert version.clean_template_artifact_id == expected_clean_artifact_id
            assert version.mask_artifact_id == expected_mask_artifact_id
            assert version.quality_json["layoutVerificationCandidateCount"] == 77
            assert version.quality_json.get("maskSource") != "TEST_FINALIZATION"
            if mutation == "layout":
                assert version.layout_json != layout_before.model_dump(
                    mode="json",
                    by_alias=True,
                )
            if mutation == "mask":
                assert version.quality_json["editedMaskSha256"]
    finally:
        allow_verification_to_finish.set()
        database.dispose()
