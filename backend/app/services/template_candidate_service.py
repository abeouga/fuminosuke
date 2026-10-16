from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import TypedDict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, FieldError
from app.db.models import (
    Template,
    TemplateCandidateBinding,
    TemplateCandidateSet,
    TemplateVersion,
)
from app.db.mutation import require_revision, touch_template_version
from app.db.session import Database
from app.domain.template_candidates import (
    MAX_TEMPLATE_CANDIDATE_BINDING_COUNT,
    MAX_TEMPLATE_CANDIDATE_NAME_LENGTH,
    MAX_TEMPLATE_CANDIDATE_SET_COUNT,
    MAX_TEMPLATE_CANDIDATE_VALUE_COUNT,
    MAX_TEMPLATE_CANDIDATE_VALUE_LENGTH,
    clean_candidate_text,
    normalize_candidate_target,
    normalize_candidate_text,
)
from app.domain.template_config import parse_template_config
from app.storage.local_storage import LocalStorageService


class _PreparedCandidateSet(TypedDict):
    name: str
    normalizedName: str
    values: list[str]
    allowedSuffixes: list[str]
    targetPatterns: list[str]


class TemplateCandidateService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self._database = database
        self._storage = storage

    def get_settings(self, template_id: str, template_version_id: str) -> dict[str, object]:
        with self._database.session() as session:
            self._require_template(session, template_id)
            version = self._version(session, template_id, template_version_id)
            return {**self._view(session, template_id), "resourceRevision": version.revision}

    def replace_settings(
        self,
        template_id: str,
        template_version_id: str,
        candidate_sets: Sequence[Mapping[str, object]],
        *, expected_revision: int,
    ) -> dict[str, object]:
        if len(candidate_sets) > MAX_TEMPLATE_CANDIDATE_SET_COUNT:
            raise self._validation_error(
                "sets",
                f"読み取り候補は{MAX_TEMPLATE_CANDIDATE_SET_COUNT}種類まで設定できます。",
            )
        with self._database.transaction() as session:
            self._require_template(session, template_id)
            version = self._version(session, template_id, template_version_id)
            require_revision(version, expected_revision)
            config = parse_template_config(
                self._storage.read_bytes(version.config_relative_path).decode("utf-8")
            )
            available_targets = {
                normalize_candidate_target(mapping.target_path): mapping.label
                for mapping in config.field_mappings
                if mapping.enabled and mapping.mapping_type == "text"
            }
            prepared = self._prepare(candidate_sets, available_targets)
            session.execute(
                delete(TemplateCandidateBinding).where(
                    TemplateCandidateBinding.template_id == template_id
                )
            )
            session.execute(
                delete(TemplateCandidateSet).where(
                    TemplateCandidateSet.template_id == template_id
                )
            )
            session.flush()
            for item in prepared:
                candidate_set = TemplateCandidateSet(
                    template_id=template_id,
                    name=item["name"],
                    normalized_name=item["normalizedName"],
                    values_json=item["values"],
                    allowed_suffixes_json=item["allowedSuffixes"],
                )
                session.add(candidate_set)
                session.flush()
                for target_pattern in item["targetPatterns"]:
                    session.add(
                        TemplateCandidateBinding(
                            template_id=template_id,
                            candidate_set_id=candidate_set.id,
                            target_pattern=target_pattern,
                            field_label=available_targets[target_pattern],
                        )
                    )
            # Candidate pools belong to the series. Invalidate proposals and saves
            # opened through any version of that series, including active versions.
            for affected in session.scalars(select(TemplateVersion).where(
                TemplateVersion.template_id == template_id,
            )):
                touch_template_version(affected)
            session.flush()
            return {**self._view(session, template_id), "resourceRevision": version.revision}

    @staticmethod
    def _version(session: Session, template_id: str, version_id: str) -> TemplateVersion:
        version = session.get(TemplateVersion, version_id)
        if version is None or version.template_id != template_id:
            raise AppError(status=404, code="TEMPLATE_VERSION_NOT_FOUND",
                           title="Template version not found", detail="指定されたテンプレート版が見つかりません。")
        return version

    @staticmethod
    def snapshot(session: Session, template_id: str) -> dict[str, object]:
        sets = list(
            session.scalars(
                select(TemplateCandidateSet)
                .where(TemplateCandidateSet.template_id == template_id)
                .order_by(TemplateCandidateSet.created_at, TemplateCandidateSet.id)
            )
        )
        if not sets:
            return {"templateCandidatePools": [], "templateCandidatePoolsHash": None}
        bindings = list(
            session.scalars(
                select(TemplateCandidateBinding)
                .where(TemplateCandidateBinding.template_id == template_id)
                .order_by(TemplateCandidateBinding.target_pattern)
            )
        )
        bindings_by_set: dict[str, list[TemplateCandidateBinding]] = {}
        for binding in bindings:
            bindings_by_set.setdefault(binding.candidate_set_id, []).append(binding)
        pools = [
            {
                "candidateSetId": item.id,
                "name": item.name,
                "targetPattern": binding.target_pattern,
                "candidates": list(item.values_json),
                "allowedSuffixes": list(item.allowed_suffixes_json),
            }
            for item in sets
            for binding in bindings_by_set.get(item.id, [])
        ]
        canonical = json.dumps(
            pools,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "templateCandidatePools": pools,
            "templateCandidatePoolsHash": hashlib.sha256(canonical).hexdigest(),
        }

    def _prepare(
        self,
        candidate_sets: Sequence[Mapping[str, object]],
        available_targets: Mapping[str, str],
    ) -> list[_PreparedCandidateSet]:
        prepared: list[_PreparedCandidateSet] = []
        used_names: set[str] = set()
        used_targets: set[str] = set()
        binding_count = 0
        for set_index, item in enumerate(candidate_sets):
            field_prefix = f"sets.{set_index}"
            raw_name = item.get("name")
            if not isinstance(raw_name, str):
                raise self._validation_error(
                    f"{field_prefix}.name",
                    "候補の種類名を入力してください。",
                )
            try:
                name = clean_candidate_text(
                    raw_name,
                    maximum=MAX_TEMPLATE_CANDIDATE_NAME_LENGTH,
                )
                normalized_name = normalize_candidate_text(name)
            except ValueError:
                raise self._validation_error(
                    f"{field_prefix}.name",
                    f"候補の種類名は{MAX_TEMPLATE_CANDIDATE_NAME_LENGTH}文字以内で入力してください。",
                ) from None
            if normalized_name in used_names:
                raise self._validation_error(
                    f"{field_prefix}.name",
                    "同じ候補の種類名を重複して登録できません。",
                )
            used_names.add(normalized_name)
            values = self._clean_unique_values(
                item.get("values"),
                field=f"{field_prefix}.values",
                maximum_count=MAX_TEMPLATE_CANDIDATE_VALUE_COUNT,
                required=True,
            )
            suffixes = self._clean_unique_values(
                item.get("allowedSuffixes", []),
                field=f"{field_prefix}.allowedSuffixes",
                maximum_count=30,
                required=False,
            )
            raw_targets = item.get("targetPatterns", [])
            if not isinstance(raw_targets, list):
                raise self._validation_error(
                    f"{field_prefix}.targetPatterns",
                    "適用する欄を帳票プレビューから選択してください。",
                )
            target_patterns: list[str] = []
            for target_index, raw_target in enumerate(raw_targets):
                if not isinstance(raw_target, str):
                    raise self._validation_error(
                        f"{field_prefix}.targetPatterns.{target_index}",
                        "適用する欄の指定が不正です。",
                    )
                target_pattern = normalize_candidate_target(raw_target)
                if target_pattern not in available_targets:
                    raise self._validation_error(
                        f"{field_prefix}.targetPatterns.{target_index}",
                        "現在のテンプレートに存在する文字欄を選択してください。",
                    )
                if target_pattern in used_targets:
                    raise self._validation_error(
                        f"{field_prefix}.targetPatterns.{target_index}",
                        "一つの欄に複数の読み取り候補を設定できません。",
                    )
                used_targets.add(target_pattern)
                target_patterns.append(target_pattern)
            binding_count += len(target_patterns)
            if binding_count > MAX_TEMPLATE_CANDIDATE_BINDING_COUNT:
                raise self._validation_error(
                    "sets",
                    f"適用する欄は合計{MAX_TEMPLATE_CANDIDATE_BINDING_COUNT}件までです。",
                )
            prepared.append(
                {
                    "name": name,
                    "normalizedName": normalized_name,
                    "values": values,
                    "allowedSuffixes": suffixes,
                    "targetPatterns": target_patterns,
                }
            )
        return prepared

    @staticmethod
    def _clean_unique_values(
        raw_values: object,
        *,
        field: str,
        maximum_count: int,
        required: bool,
    ) -> list[str]:
        if not isinstance(raw_values, list) or len(raw_values) > maximum_count:
            raise TemplateCandidateService._validation_error(
                field,
                f"候補は{maximum_count}件以内で入力してください。",
            )
        values: list[str] = []
        normalized_values: set[str] = set()
        for index, raw_value in enumerate(raw_values):
            if not isinstance(raw_value, str):
                raise TemplateCandidateService._validation_error(
                    f"{field}.{index}",
                    "候補の文字を入力してください。",
                )
            try:
                value = clean_candidate_text(
                    raw_value,
                    maximum=MAX_TEMPLATE_CANDIDATE_VALUE_LENGTH,
                )
                normalized = normalize_candidate_text(value)
            except ValueError:
                raise TemplateCandidateService._validation_error(
                    f"{field}.{index}",
                    f"候補は{MAX_TEMPLATE_CANDIDATE_VALUE_LENGTH}文字以内で入力してください。",
                ) from None
            if normalized in normalized_values:
                continue
            normalized_values.add(normalized)
            values.append(value)
        if required and not values:
            raise TemplateCandidateService._validation_error(
                field,
                "読み取り候補を1件以上入力してください。",
            )
        return values

    def _view(self, session: Session, template_id: str) -> dict[str, object]:
        sets = list(
            session.scalars(
                select(TemplateCandidateSet)
                .where(TemplateCandidateSet.template_id == template_id)
                .order_by(TemplateCandidateSet.created_at, TemplateCandidateSet.id)
            )
        )
        bindings = list(
            session.scalars(
                select(TemplateCandidateBinding)
                .where(TemplateCandidateBinding.template_id == template_id)
                .order_by(TemplateCandidateBinding.target_pattern)
            )
        )
        bindings_by_set: dict[str, list[TemplateCandidateBinding]] = {}
        for binding in bindings:
            bindings_by_set.setdefault(binding.candidate_set_id, []).append(binding)
        return {
            "templateId": template_id,
            "sets": [
                {
                    "id": item.id,
                    "name": item.name,
                    "values": list(item.values_json),
                    "allowedSuffixes": list(item.allowed_suffixes_json),
                    "bindings": [
                        {
                            "targetPattern": binding.target_pattern,
                            "label": binding.field_label,
                        }
                        for binding in bindings_by_set.get(item.id, [])
                    ],
                }
                for item in sets
            ],
        }


    @staticmethod
    def _require_template(session: Session, template_id: str) -> Template:
        template = session.get(Template, template_id)
        if template is None:
            raise AppError(
                status=404,
                code="TEMPLATE_NOT_FOUND",
                title="Template not found",
                detail="指定されたテンプレートが見つかりません。",
            )
        return template

    @staticmethod
    def _validation_error(field: str, message: str) -> AppError:
        return AppError(
            status=422,
            code="TEMPLATE_CANDIDATE_SETTINGS_INVALID",
            title="Template candidate settings are invalid",
            detail=message,
            field_errors=[FieldError(field=field, message=message)],
        )
