"""Finite physical intents; references are local to a server-owned page context."""

from typing import Annotated, Literal

from pydantic import Field

from app.editing.operation_contracts import Contract

CellRef = Annotated[str, Field(pattern=r"^c[1-9][0-9]{0,3}$", strict=True)]
NewRef = Annotated[str, Field(pattern=r"^n[1-9][0-9]{0,3}$", strict=True)]
RegionRef = Annotated[str, Field(pattern=r"^[cn][1-9][0-9]{0,3}$", strict=True)]
Coordinate = Annotated[int, Field(ge=0, le=20000, strict=True)]
Dimension = Annotated[int, Field(ge=1, le=20000, strict=True)]


class Rectangle(Contract):
    x: Coordinate
    y: Coordinate
    width: Dimension
    height: Dimension


class NewRectangle(Contract):
    ref: NewRef
    rect: Rectangle


class Add(Contract):
    op: Literal["add"]
    ref: NewRef
    rect: Rectangle


class Resize(Contract):
    op: Literal["resize"]
    cell: CellRef
    rect: Rectangle


class Split(Contract):
    op: Literal["split"]
    cell: CellRef
    parts: Annotated[list[NewRectangle], Field(min_length=2, max_length=100)]


class Merge(Contract):
    op: Literal["merge"]
    cells: Annotated[list[CellRef], Field(min_length=2, max_length=100)]
    ref: NewRef


class RemoveDuplicate(Contract):
    op: Literal["remove_duplicate"]
    cell: CellRef
    survivors: Annotated[list[RegionRef], Field(min_length=1, max_length=100)]


GeometryOperation = Add | Resize | Split | Merge | RemoveDuplicate
GeometryOperations = Annotated[list[GeometryOperation], Field(max_length=100)]
