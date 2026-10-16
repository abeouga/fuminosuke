from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Query, Request, Response

from app.db.mutation import TemplateWriteScope, require_revision, template_write_scope
from app.services.template_authoring_service import TemplateAuthoringService


async def template_revision_guard(
    request: Request,
    response: Response,
    version_id: str,
    expected_revision: Annotated[int, Query(alias="expectedRevision", ge=1)],
) -> AsyncIterator[TemplateWriteScope]:
    """Bind every write in this request to the generation shown by its caller."""
    with request.app.state.database.session() as session:
        version = TemplateAuthoringService.version(session, version_id)
        require_revision(version, expected_revision)

    def publish(revision: int) -> None:
        response.headers["X-Template-Revision"] = str(revision)

    publish(expected_revision)
    scope = TemplateWriteScope(
        version_id, expected_revision, f"{request.method} {request.url.path}"[:100], publish
    )
    with template_write_scope(scope):
        yield scope
