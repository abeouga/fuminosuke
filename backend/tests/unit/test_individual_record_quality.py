from app.services.individual_record_service import IndividualRecordService


def test_legacy_confirmed_record_with_invalid_parity_and_sire_is_flagged() -> None:
    warnings = IndividualRecordService._confirmed_quality_warnings(
        {
            "saleCalves": [
                {
                    "parity": {"raw": "/", "normalized": None},
                    "sireName": {"raw": "、", "normalized": "、"},
                }
            ]
        }
    )

    assert warnings == [
        "売却子牛1行目の産次が数値ではありません。",
        "売却子牛1行目の種雄牛名を再確認してください。",
    ]


def test_valid_legacy_confirmed_record_is_not_flagged() -> None:
    warnings = IndividualRecordService._confirmed_quality_warnings(
        {
            "saleCalves": [
                {
                    "parity": {"raw": "3", "normalized": "3"},
                    "sireName": {"raw": "勝忠平ET", "normalized": "勝忠平ET"},
                }
            ]
        }
    )

    assert warnings == []
