import copy

import pytest

from app.services import prepared_reference_evidence as evidence
from app.services.renderer_wsl_worker import native_dependency_paths, validate_bootstrap_python
from tests.bootstrap_python_fixture import bootstrap_python_evidence
from tests.unit.test_prepared_reference_replay import sample


def test_loader_addresses_are_not_identity_and_duplicate_paths_are_deduplicated():
    assert native_dependency_paths(
        "\tlinux-vdso.so.1 (0x123)\n"
        "libc.so.6 => /lib/libc.so.6 (0x456)\n"
        "/lib/libc.so.6 (0xabc)\n"
        "/lib64/ld-linux-x86-64.so.2 => /usr/lib/ld.so (0x789)\n"
    ) == ["/lib/libc.so.6", "/usr/lib/ld.so"]
    assert native_dependency_paths("\tstatically linked\n") == []


@pytest.mark.parametrize("output", [
    "", "linux-vdso.so.1 (0x123)", "libc.so.6 => not found", "not a dynamic executable",
    "libc.so.6 => relative/libc.so.6 (0x123)", "libc.so.6 => /lib/../tmp/a (0x123)",
    "statically linked\nunrecognized", "libc.so.6 => /lib/libc.so.6 (0x123)\nunknown",
])
def test_unparsed_or_unresolved_dependency_lines_are_not_partial_success(output):
    with pytest.raises(RuntimeError, match="bootstrap ELF"):
        native_dependency_paths(output)


@pytest.mark.parametrize("key,value", [
    ("policy", "old"), ("sha256", "not a hash"), ("preloadAbsent", 1),
    ("rootCount", 0), ("rootCount", True), ("rootCount", 257), ("fileCount", 1),
    ("fileCount", 1025), ("totalBytes", 0), ("totalBytes", 1.5),
    ("totalBytes", 512 * 1024 * 1024 + 1),
])
def test_invalid_native_identity_is_rejected_at_existing_bridge_boundary(key, value):
    report = bootstrap_python_evidence()
    report["nativeDependencies"][key] = value
    with pytest.raises(RuntimeError, match="Bootstrap ELF evidence"):
        validate_bootstrap_python(report)


@pytest.mark.parametrize("change", ["missing", "sha", "invalid", "old-policy"])
def test_replay_binds_native_dependencies_without_backfilling_old_reports(change):
    original, images = sample()
    original["bootstrapPythonIdentity"] = bootstrap_python_evidence()
    replay = copy.deepcopy(original)
    assert evidence.compare_reference_replay(original, replay, images) is True
    bootstrap = replay["bootstrapPythonIdentity"]
    if change == "missing":
        bootstrap.pop("nativeDependencies")
    elif change == "sha":
        bootstrap["nativeDependencies"]["sha256"] = "f" * 64
    elif change == "old-policy":
        bootstrap["policy"] = "wsl-bootstrap-python-tree-v1"
    else:
        bootstrap["nativeDependencies"] = {"qualified": True}
    with pytest.raises(ValueError, match="environment|execution evidence"):
        evidence.compare_reference_replay(original, replay, images)
