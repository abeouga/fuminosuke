from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FieldTarget:
    path: str
    label: str
    section: str


DOCUMENT_FIELDS = {
    "unifiedEarTag": "個体識別番号",
    "cattleName": "牛名",
    "birthDate": "生年月日",
    "registrationNumber": "登録番号",
    "coatColor": "毛色",
    "breeder": "繁殖者",
    "owner": "所有者",
}

PEDIGREE_FIELDS = {
    "father": "父",
    "mother": "母",
    "paternalGrandfather": "父方祖父",
    "paternalGrandmother": "父方祖母",
    "maternalGrandfather": "母方祖父",
    "maternalGrandmother": "母方祖母",
}

SALE_CALF_FIELDS = {
    "parity": "産次",
    "ageDays": "日齢",
    "weightKg": "体重",
    "priceYen": "価格",
    "sireName": "種雄牛名",
    "saleDate": "売却日",
    "notes": "備考",
}

CALVING_FIELDS = {
    "parity": "産歴番号",
    "calvingDate": "分娩年月日",
    "sex": "性別",
    "outcome": "分娩状況",
    "notes": "備考",
}


def field_targets() -> list[FieldTarget]:
    targets = [
        FieldTarget(f"document.{name}", label, "基本情報")
        for name, label in DOCUMENT_FIELDS.items()
    ]
    targets.extend(
        FieldTarget(f"pedigree.{name}", label, "血統") for name, label in PEDIGREE_FIELDS.items()
    )
    for index in range(20):
        targets.extend(
            FieldTarget(
                f"saleCalves.{index}.{name}",
                f"売却子牛 {index + 1}行目・{label}",
                "売却子牛",
            )
            for name, label in SALE_CALF_FIELDS.items()
        )
        targets.extend(
            FieldTarget(
                f"calvings.{index}.{name}",
                f"繁殖履歴 {index + 1}行目・{label}",
                "繁殖履歴",
            )
            for name, label in CALVING_FIELDS.items()
        )
    return targets


FIELD_TARGET_BY_PATH = {target.path: target for target in field_targets()}
