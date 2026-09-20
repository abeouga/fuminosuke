"""Synthetic Python identity for protocol tests, not installation evidence."""


def bootstrap_python_evidence():
    return {
        "policy": "wsl-bootstrap-python-tree-v2", "pythonVersion": "3.10.12",
        "searchPaths": ["/usr/lib/python310.zip", "/usr/lib/python3.10",
                        "/usr/lib/python3.10/lib-dynload"],
        "isolated": True, "noSite": True, "noBytecodeWrite": True,
        "sha256": "a" * 64, "entryCount": 4, "fileCount": 2, "totalBytes": 100,
        "nativeDependencies": {
            "policy": "wsl-bootstrap-elf-startup-v1", "sha256": "b" * 64,
            "rootCount": 12, "fileCount": 24, "totalBytes": 200, "preloadAbsent": True,
        },
    }
