from __future__ import annotations

import os

# Environment variables (not .env) naming the soffice executable. LIBREOFFICE_PATH is the
# one name; LLM_OCR_LIBREOFFICE_PATH is still read for setups that set the older name.
LIBREOFFICE_PATH_VARIABLES = ("LIBREOFFICE_PATH", "LLM_OCR_LIBREOFFICE_PATH")


def configured_libreoffice_path() -> str | None:
    """The soffice executable named by the environment, or None when none is set."""
    for name in LIBREOFFICE_PATH_VARIABLES:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None
