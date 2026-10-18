"""Explicit complete business metadata for new PostgreSQL tenant schemas."""

from app.ai import models as ai_models  # noqa: F401
from app.assistant import models as assistant_models  # noqa: F401
from app.assistant import stream_models, voice_models  # noqa: F401
from app.db import models as database_models  # noqa: F401
from app.db import mutation_models  # noqa: F401
from app.db.base import Base
from app.editing import models as editing_models  # noqa: F401
from app.editing.infrastructure import command_models  # noqa: F401
from app.template_analysis import models as template_analysis_models  # noqa: F401

__all__ = ["Base"]
