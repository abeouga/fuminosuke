import io
from collections.abc import Callable

import pytest
from PIL import Image

from app.core.config import Settings
from app.core.errors import AppError
from app.db.base import Base
from app.db.models import ProcessingJob, Template, TemplateVersion
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, TemplateMode, TemplateVersionStatus
from app.services.template_management_service import TemplateManagementService
from app.storage.local_storage import LocalStorageService


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("L", (width, height), 255).save(buffer, format="PNG")
    return buffer.getvalue()


def test_mask_dimensions_are_checked_before_opencv_decode(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    storage = LocalStorageService(settings.storage.data_dir)
    storage.initialize()
    try:
        with database.session() as session:
            job = ProcessingJob(
                job_type=JobType.PREPARE_TEMPLATE.value,
                status=JobStatus.REVIEW_REQUIRED.value,
                request_hash="a" * 64,
                config_snapshot_json={},
            )
            template = Template(
                name="mask-header-test",
                document_type="cow-breeding-ledger",
            )
            session.add_all([job, template])
            session.flush()
            version = TemplateVersion(
                template_id=template.id,
                version_number=1,
                mode=TemplateMode.REFERENCE_TEMPLATE.value,
                status=TemplateVersionStatus.DRAFT.value,
                canonical_width=32,
                canonical_height=24,
                source_job_id=job.id,
                clean_template_artifact_id=None,
                mask_artifact_id=None,
                config_relative_path="templates/mask-header-test/template.yaml",
                quality_json={},
                layout_json={},
            )
            session.add(version)
            session.commit()
            version_id = version.id

        def fail_if_decoded(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("OpenCV decode must not run for a mismatched header")

        monkeypatch.setattr(
            "app.services.template_management_service.cv2.imdecode",
            fail_if_decoded,
        )
        with pytest.raises(AppError) as caught:
            TemplateManagementService(database, storage).save_edited_mask(
                version_id,
                _png(33, 24),
            )

        assert caught.value.code == "INVALID_MASK_DIMENSIONS"
    finally:
        database.dispose()
