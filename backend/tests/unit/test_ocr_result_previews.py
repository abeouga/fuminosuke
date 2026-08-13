from types import SimpleNamespace

from app.services.ocr_result_service import OcrResultService


def test_field_preview_follows_current_reading_source_cell() -> None:
    job = SimpleNamespace(
        id="job-1",
        config_snapshot_json={
            "fieldMappings": [
                {
                    "mappingId": "price_row_2",
                    "targetPath": "saleCalves.1.priceYen",
                    "x": 100,
                    "y": 200,
                    "width": 50,
                    "height": 20,
                }
            ],
            "recordStructure": {"fieldBindings": []},
        },
    )
    document = {
        "saleCalves": [
            {
                "priceYen": {
                    "raw": "540000",
                    "normalized": "540000",
                    "status": "readable",
                    "sourceRegionId": "price_row_2",
                    "boundingBox": {
                        "x": 100,
                        "y": 200,
                        "width": 50,
                        "height": 20,
                    },
                }
            }
        ]
    }

    previews = OcrResultService._field_previews(job, document)

    assert previews[0]["targetPath"] == "saleCalves.0.priceYen"
    assert previews[0]["mappingId"] == "price_row_2"
    assert previews[0]["artifactUrl"].endswith(
        "/fieldCrop.price_row_2.alignedColor"
    )
