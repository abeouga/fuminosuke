import hashlib
import json
from pathlib import Path

from app.services.renderer_dependencies import file_record, validate_manifest


def rewrite_with_same_metadata(path, monkeypatch):
    before = path.stat()
    original_stat = Path.stat
    path.write_bytes(b"two")

    def same_stat(current, *args, **kwargs):
        if current == path:
            return before
        return original_stat(current, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", same_stat)


def test_dependency_content_is_rechecked_when_stat_metadata_is_identical(tmp_path, monkeypatch):
    path = tmp_path / "dependency"
    path.write_bytes(b"one")
    first = file_record(path)
    rewrite_with_same_metadata(path, monkeypatch)
    second = file_record(path)
    assert first["sha256"] == hashlib.sha256(b"one").hexdigest()
    assert second["sha256"] == hashlib.sha256(b"two").hexdigest()


def test_previous_manifest_is_rejected_after_same_metadata_rewrite(tmp_path, monkeypatch):
    path = tmp_path / "dependency"
    path.write_bytes(b"one")
    payload = {"application": [file_record(path)], "operatingSystem": []}
    manifest = {
        **payload,
        "sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
    }
    assert validate_manifest(manifest)
    rewrite_with_same_metadata(path, monkeypatch)
    assert not validate_manifest(manifest)
