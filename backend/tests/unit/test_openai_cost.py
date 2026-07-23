from decimal import Decimal

from app.ocr.openai_provider import _estimate_cost


def test_gpt_5_mini_cost_is_estimated_from_actual_usage() -> None:
    assert _estimate_cost("gpt-5-mini", 6_000, 2_000) == Decimal("0.005500")


def test_unknown_model_cost_is_not_guessed() -> None:
    assert _estimate_cost("other-model", 6_000, 2_000) is None
