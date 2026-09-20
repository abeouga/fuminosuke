import pytest

from app.services.renderer_wsl_worker import validate_bootstrap_python
from tests.bootstrap_python_fixture import bootstrap_python_evidence


def test_current_identity_protocol_is_not_an_environment_certificate():
    value = bootstrap_python_evidence()
    validate_bootstrap_python(value)
    assert "qualified" not in value


@pytest.mark.parametrize("value", [None, [], True, {"qualified": True}])
def test_missing_identity_is_not_success(value):
    with pytest.raises(RuntimeError, match="Bootstrap Python evidence"):
        validate_bootstrap_python(value)


@pytest.mark.parametrize("key,value", [
    ("policy", "old"), ("pythonVersion", "3.11.0"), ("pythonVersion", None),
    ("searchPaths", []), ("searchPaths", ["/tmp"]), ("isolated", 1), ("noSite", False),
    ("noBytecodeWrite", False), ("sha256", "changed"), ("entryCount", True),
    ("entryCount", 20001), ("entryCount", 1), ("fileCount", 0), ("fileCount", 1.0),
    ("totalBytes", 0), ("totalBytes", 512 * 1024 * 1024 + 1),
])
def test_inconsistent_or_changed_identity_is_rejected(key, value):
    evidence = bootstrap_python_evidence()
    evidence[key] = value
    with pytest.raises(RuntimeError, match="Bootstrap Python evidence"):
        validate_bootstrap_python(evidence)
