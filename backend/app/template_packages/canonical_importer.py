from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    JobArtifact,
    ProcessingJob,
    Template,
    TemplateDefinitionRevision,
    TemplatePackageIdentity,
    TemplatePackageInstallation,
    TemplateVersion,
    uuid_string,
)
from app.domain.enums import JobStatus, JobType, TemplateVersionStatus
from app.domain.template_config import TemplateConfig
from app.template_packages.import_models import ValidatedTemplatePackage
from app.validation.template_definition_validator import validate_template_definition


@dataclass(frozen=True)
class CanonicalImportPlan:
    installation: TemplatePackageInstallation
    identity: TemplatePackageIdentity | None
    template: Template | None
    job: ProcessingJob
    version: TemplateVersion
    artifacts: list[JobArtifact]
    definition: TemplateDefinitionRevision | None
    files: dict[str, bytes]


class CanonicalTemplateImporter:
    def build_plan(
        self,
        session: Session,
        package: ValidatedTemplatePackage,
        *,
        existing_identity: TemplatePackageIdentity | None,
        source_type: str = "FILE",
    ) -> CanonicalImportPlan:
        canonical = package.template
        manifest = package.manifest
        template_id = (
            existing_identity.local_template_id if existing_identity is not None else uuid_string()
        )
        template = (
            None
            if existing_identity is not None
            else Template(
                id=template_id,
                name=canonical.name,
                document_type=canonical.document_type,
                active_version_id=None,
            )
        )
        identity = (
            None
            if existing_identity is not None
            else TemplatePackageIdentity(
                publisher_id=manifest.publisher_id,
                package_id=manifest.package_id,
                local_template_id=template_id,
            )
        )
        version_number = (
            session.scalar(
                select(func.max(TemplateVersion.version_number)).where(
                    TemplateVersion.template_id == template_id
                )
            )
            or 0
        ) + 1
        job_id = uuid_string()
        version_id = uuid_string()
        job = ProcessingJob(
            id=job_id,
            job_type=JobType.PREPARE_TEMPLATE.value,
            status=JobStatus.REVIEW_REQUIRED.value,
            mode=canonical.mode.value,
            request_hash=manifest.content_digest.removeprefix("sha256:"),
            attempt_number=1,
            progress_stage="PACKAGE_IMPORTED",
            progress_percent=100,
            config_snapshot_json={
                "sourceType": (
                    "template-package-drive" if source_type == "DRIVE" else "template-package"
                ),
                "publisherId": manifest.publisher_id,
                "packageId": manifest.package_id,
                "releaseVersion": manifest.version,
                "contentDigest": manifest.content_digest,
                "packageSha256": package.package_sha256,
            },
        )
        config = TemplateConfig(
            template_id=template_id,
            template_version=version_number,
            name=canonical.name,
            canonical_width=canonical.canonical_width,
            canonical_height=canonical.canonical_height,
            registration=canonical.runtime.registration,
            regions=canonical.runtime.regions,
            field_mappings=canonical.runtime.field_mappings,
            document_identity_fields=canonical.runtime.document_identity_fields,
            document_name=canonical.runtime.document_name,
            marks=canonical.runtime.marks,
        )
        config_content = yaml.safe_dump(
            config.model_dump(mode="json", by_alias=True),
            allow_unicode=True,
            sort_keys=False,
        ).encode("utf-8")
        base = f"templates/{template_id}/versions/{version_number}"
        config_path = f"templates/configs/{hashlib.sha256(config_content).hexdigest()}-{version_id}.yaml"
        files: dict[str, bytes] = {config_path: config_content}
        artifacts: list[JobArtifact] = []
        artifact_ids: dict[str, str] = {}
        artifact_paths: dict[str, str] = {}
        asset_contract = {
            "assets/clean-template.png": ("cleanTemplate", "clean_template.png", "image/png"),
            "assets/handwriting-mask.png": (
                "handwritingMask",
                "handwriting_mask.png",
                "image/png",
            ),
            "assets/handwriting-mask-edited.png": (
                "handwritingMaskEdited",
                "handwriting_mask_edited.png",
                "image/png",
            ),
            "assets/preview.png": ("preview", "preview.png", "image/png"),
            "optional/handwriting-candidates.json": (
                "handwritingCandidates",
                "handwriting_candidates.json",
                "application/json",
            ),
            "optional/spreadsheet-source.xlsx": (
                "sourceWorkbook",
                "spreadsheet_source.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        }
        for package_path, (artifact_name, file_name, media_type) in asset_contract.items():
            content = package.payloads.get(package_path)
            if content is None:
                continue
            artifact_id = uuid_string()
            relative_path = f"artifacts/{job_id}/attempts/1/{file_name}"
            dimensions = package.image_dimensions.get(package_path)
            artifacts.append(
                JobArtifact(
                    id=artifact_id,
                    job_id=job_id,
                    attempt_number=1,
                    artifact_name=artifact_name,
                    media_type=media_type,
                    relative_path=relative_path,
                    sha256=hashlib.sha256(content).hexdigest(),
                    width=dimensions[0] if dimensions else None,
                    height=dimensions[1] if dimensions else None,
                )
            )
            artifact_ids[package_path] = artifact_id
            artifact_paths[package_path] = relative_path
            files[relative_path] = content
            permanent_path = f"{base}/{file_name}"
            files[permanent_path] = content

        quality = self._runtime_quality(package, artifact_paths)
        mask_id = artifact_ids.get("assets/handwriting-mask-edited.png") or artifact_ids.get(
            "assets/handwriting-mask.png"
        )
        version = TemplateVersion(
            id=version_id,
            template_id=template_id,
            version_number=version_number,
            mode=canonical.mode.value,
            status=TemplateVersionStatus.DRAFT.value,
            canonical_width=canonical.canonical_width,
            canonical_height=canonical.canonical_height,
            source_job_id=job_id,
            clean_template_artifact_id=artifact_ids["assets/clean-template.png"],
            mask_artifact_id=mask_id,
            config_relative_path=config_path,
            quality_json=quality,
            layout_json=canonical.layout.model_dump(mode="json", by_alias=True),
        )
        definition = self._definition(package, version_id)
        candidate_mapping = {
            item.id: {
                "localCandidateSetId": uuid_string(),
                "localBindingIds": {target: uuid_string() for target in item.target_patterns},
            }
            for item in canonical.candidate_sets
        }
        mapping: dict[str, Any] = {
            "localTemplateId": template_id,
            "localTemplateVersionId": version_id,
            "localJobId": job_id,
            "configRelativePath": config_path,
            "assetArtifactIds": artifact_ids,
            "candidateSets": candidate_mapping,
            "candidateMaterialization": "DEFERRED_UNTIL_ACTIVATION",
            "outputLayoutMaterialization": "DEFERRED_UNTIL_ACTIVATION",
        }
        installation = TemplatePackageInstallation(
            id=uuid_string(),
            publisher_id=manifest.publisher_id,
            package_id=manifest.package_id,
            release_version=manifest.version,
            content_digest=manifest.content_digest,
            local_template_id=template_id,
            local_template_version_id=version_id,
            local_job_id=job_id,
            source_type=source_type,
            trust_status="UNVERIFIED",
            manifest_json=manifest.model_dump(mode="json", by_alias=True),
            canonical_template_json=canonical.model_dump(mode="json", by_alias=True),
            canonical_quality_json=package.quality.model_dump(mode="json", by_alias=True),
            output_layout_json=(
                package.output_layout.model_dump(mode="json", by_alias=True)
                if package.output_layout is not None
                else None
            ),
            local_mapping_json=mapping,
        )
        return CanonicalImportPlan(
            installation=installation,
            identity=identity,
            template=template,
            job=job,
            version=version,
            artifacts=artifacts,
            definition=definition,
            files=files,
        )

    @staticmethod
    def add_plan(session: Session, plan: CanonicalImportPlan) -> None:
        if plan.template is not None:
            session.add(plan.template)
            session.flush()
        if plan.identity is not None:
            session.add(plan.identity)
            session.flush()
        session.add(plan.job)
        session.flush()
        session.add_all(plan.artifacts)
        session.flush()
        session.add(plan.version)
        session.flush()
        if plan.definition is not None:
            session.add(plan.definition)
            session.flush()
        session.add(plan.installation)
        session.flush()

    @staticmethod
    def _runtime_quality(
        package: ValidatedTemplatePackage,
        artifact_paths: dict[str, str],
    ) -> dict[str, Any]:
        canonical = package.template
        quality = package.quality
        runtime: dict[str, Any] = {
            "sourceType": "template-package",
            "layoutVerified": quality.layout_verified,
            "layoutFinalized": quality.layout_finalized,
            "fieldMappingCount": quality.field_mapping_count,
            "recordStructureVerified": quality.record_structure_verified,
            "warnings": list(quality.warnings),
            "fallbackUsed": not quality.document_boundary_verified,
            "layoutInputRevision": 1,
        }
        if canonical.approved_definition is not None:
            runtime.update(
                {
                    "templateDefinitionState": "APPROVED",
                    "templateDefinitionValid": True,
                    "templateDefinitionRevision": 1,
                    "templateDefinitionLayoutInputRevision": 1,
                }
            )
        if canonical.spreadsheet is not None:
            runtime.update(
                {
                    "sourceFileName": canonical.spreadsheet.file_name,
                    "sourceSheetName": canonical.spreadsheet.sheet_name,
                    "spreadsheetSourcePath": artifact_paths.get("optional/spreadsheet-source.xlsx"),
                    "layoutVerificationDetector": "xlsx-structure",
                }
            )
        return runtime

    @staticmethod
    def _definition(
        package: ValidatedTemplatePackage,
        version_id: str,
    ) -> TemplateDefinitionRevision | None:
        canonical = package.template.approved_definition
        if canonical is None:
            return None
        definition = canonical.as_runtime_definition(
            template_name=package.template.name
        ).model_copy(
            update={"template_version_id": version_id},
            deep=True,
        )
        validation = validate_template_definition(definition)
        return TemplateDefinitionRevision(
            id=uuid_string(),
            template_version_id=version_id,
            revision=1,
            source="APPROVED",
            definition_json=definition.model_dump(mode="json", by_alias=True),
            validation_json=validation.model_dump(mode="json", by_alias=True),
        )
