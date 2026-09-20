"""Synthetic evidence for protocol tests, never a production certificate."""


def core_guard_evidence():
    return {
        "policy": "linux-core-limit-seccomp-v1", "architecture": "x86_64",
        "coreLimits": [1, 1], "noNewPrivileges": True, "seccompMode": 2,
        "filterSha256": "9382165a2839306b47868187ad837d0674a97cc3b6cb94bb59b3d9c21efd1c36",
        "filterBytes": 192, "deniedMutationProbes": 4, "librarySha256": "a" * 64,
    }
