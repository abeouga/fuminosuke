"""Synthetic execution observations, never production qualification evidence."""


def wsl_execution_evidence(*, windows_fonts=True):
    return {
        "uid": 65534,
        "networkInterfaces": ["lo"],
        "networkDenied": True,
        "hostFilesystemHidden": True,
        "noNewPrivileges": True,
        "referenceFontMountReadOnly": windows_fonts,
        "unitControls": unit_control_evidence(),
        "resourceLimits": {
            "memory.max": "1073741824", "memory.swap.max": "0",
            "pids.max": "64", "cpu.max": "100000 100000",
        },
    }


def unit_control_evidence():
    return {
        "policy": "wsl-effective-unit-controls-v1",
        "properties": {
            "Restart": "no", "RemainAfterExit": "no", "KillMode": "control-group",
            "SendSIGKILL": "yes", "RuntimeMaxUSec": "1min", "TimeoutStopUSec": "2s",
            "LimitFSIZE": "67108864", "LimitFSIZESoft": "67108864",
            "LimitCORE": "1", "LimitCORESoft": "1",
            "MemoryMax": "1073741824", "MemorySwapMax": "0", "TasksMax": "64",
            "RootDirectory": "", "RootImage": "", "User": "", "Group": "",
            "DropInPaths": "", "Transient": "yes",
        },
    }
