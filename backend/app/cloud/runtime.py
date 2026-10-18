"""Exclusive runtime/maintenance ownership for local and PostgreSQL cloud modes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from app.cloud.config import CloudSettings
from app.cloud.lease import runtime_lease
from app.cloud.relational import ControlDatabase


@contextmanager
def cloud_runtime(
    config: CloudSettings, *, allow_retired: bool = False, serving: bool = False
) -> Iterator[ControlDatabase | None]:
    with runtime_lease(config.root):
        if config.database_url is None:
            yield None
            return
        control = ControlDatabase(
            config.database_url.get_secret_value(), str(config.installation_id)
        )
        try:
            with control.runtime_lease(allow_retired=allow_retired, maintenance=not serving):
                yield control
        finally:
            control.dispose()
