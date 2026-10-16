"""Public command limits and bounded, validated authoring-draft compilation."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CompilationLimits:
    operations: int
    references: int
    cells: int = 1000


COMMAND_LIMITS = CompilationLimits(50, 1000)
AUTHORING_LIMITS = CompilationLimits(5000, 20000)
