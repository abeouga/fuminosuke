from app.domain.field_targets import (
    CALVING_ROW_COUNT,
    SALE_CALF_ROW_COUNT,
    field_targets,
    is_supported_field_path,
)


def test_sale_calf_and_calving_targets_use_physical_row_limits() -> None:
    paths = {target.path for target in field_targets()}

    assert SALE_CALF_ROW_COUNT == 10
    assert CALVING_ROW_COUNT == 20
    assert "saleCalves.9.priceYen" in paths
    assert "saleCalves.9.weightKg" in paths
    assert "saleCalves.10.priceYen" not in paths
    assert "saleCalves.10.weightKg" not in paths
    assert "calvings.19.calvingDate" in paths
    assert is_supported_field_path("saleCalves.9.priceYen")
    assert not is_supported_field_path("saleCalves.10.priceYen")
