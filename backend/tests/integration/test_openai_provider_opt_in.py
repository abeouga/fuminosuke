import os

import pytest


@pytest.mark.skipif(
    os.getenv("RUN_OPENAI_INTEGRATION_TESTS") != "true",
    reason="External OpenAI tests are explicitly opt-in",
)
def test_openai_provider_opt_in_marker() -> None:
    assert os.getenv("OPENAI_API_KEY"), "OPENAI_API_KEY is required for opt-in tests"
    assert os.getenv("OPENAI_PRIMARY_MODEL"), "OPENAI_PRIMARY_MODEL is required"

