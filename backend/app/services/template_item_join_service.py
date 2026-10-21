"""Items shown as one value: 「調査場所」 = 高槻市美大町3丁目1番12号 (2026-10-20).

Display only, per template series like the display names: each joined item keeps its own
reading, crop, warning and correction, and accepted job snapshots are never touched. The
joined value is built from the items in the saved order, each with the printed text
around it; an empty item drops its printed text too (「（　番地）」 disappears).

Suggestions come from the headings of a template version: a heading whose items are all
text fields on one line is offered with its items left to right and the printed text
between them (「町」「丁目」「番」…), so most forms need only 「まとめる」.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Any, Protocol

from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, FieldError
from app.db.models import Template, TemplateItemJoin, TemplateVersion
from app.db.session import Database
from app.domain.field_targets import is_supported_field_path
from app.domain.template_config import parse_template_config
from app.layout_analysis.models import LayoutCell, LayoutDefinition
from app.services.review_display_structure import build_review_display_structure
from app.storage.local_storage import LocalStorageService

MAX_JOINS = 50
MAX_MEMBERS = 30
MAX_TEXT = 30
MAX_LINES = 3
MAX_SEPARATOR = 5
KIND_NAMES = {"join": "まとめた項目", "order": "順番"}
# Printed text that opens around the next item: 「号（　番地）」 → 「（」 goes with 番地.
_OPENING = "(（「『[［〔【<＜"


def joined_text(
    members: Sequence[Mapping[str, Any]], values: Mapping[str, str | None], separator: str = ""
) -> str:
    """The joined value: each non-empty item with its printed text, in order.

    ``separator`` goes only between two items with no printed text between them
    (姓・名 → 「山田 太郎」); printed text such as 「町」「丁目」 already separates the rest.
    """
    text = ""
    previous_after: str | None = None
    for member in members:
        value = (values.get(str(member.get("targetPath"))) or "").strip()
        if not value:
            continue
        before, after = str(member.get("before") or ""), str(member.get("after") or "")
        if previous_after is not None and not previous_after and not before:
            text += separator
        text += f"{before}{value}{after}"
        previous_after = after
    return text


def ordered(keys: Sequence[str], orders: Sequence[Mapping[str, Any]]) -> list[str]:
    """``keys`` (item paths) in a saved display order; unlisted items go after, in place."""
    for order in orders:
        position = {
            str(member.get("targetPath")): index
            for index, member in enumerate(order.get("members", []))
            if isinstance(member, Mapping)
        }
        if sum(key in position for key in keys) >= 2:
            fallback = len(position)
            return sorted(keys, key=lambda key: position.get(key, fallback))
    return list(keys)


def item_joins(session: Session, template_id: object) -> list[dict[str, Any]]:
    """Saved joins and orders of a template series in an open session (empty if not migrated)."""
    if not isinstance(template_id, str) or not inspect(session.connection()).has_table(
        TemplateItemJoin.__tablename__
    ):
        return []
    return _joins(session, template_id)


class JoinChooser(Protocol):
    async def choose(
        self, context: str, questions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]: ...


AI_CHOICES = (
    ("keep", "まとめない（別々の欄のまま）"),
    ("glue", "まとめる（区切りなし）"),
    ("space", "まとめる（空白で区切る）"),
    ("hyphen", "まとめる（「-」で区切る）"),
    ("dot", "まとめる（「・」で区切る）"),
)
AI_SEPARATORS = {"glue": "", "space": " ", "hyphen": "-", "dot": "・"}
AI_THRESHOLD = 0.5
MAX_AI_SECTIONS = 12


class TemplateItemJoinService:
    def __init__(self, database: Database, storage: LocalStorageService | None = None) -> None:
        self._database = database
        self._storage = storage

    def joins(self, template_id: str) -> list[dict[str, Any]]:
        """Saved joins; empty before the database is migrated (display falls back)."""
        with self._database.session() as session:
            return item_joins(session, template_id)

    def settings(self, template_id: str, template_version_id: str | None) -> dict[str, Any]:
        with self._database.session() as session:
            _require_table(session)
            _require_template(session, template_id)
            sections: list[dict[str, Any]] = []
            suggestions: list[dict[str, Any]] = []
            if template_version_id:
                sections, suggestions = self._analysis(
                    _version(session, template_id, template_version_id)
                )
            return {
                "items": _joins(session, template_id),
                "suggestions": suggestions,
                "sections": [
                    {"name": section["name"], "members": section["members"]}
                    for section in sections
                ],
            }

    def replace(self, template_id: str, items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        cleaned = _validate(items)
        with self._database.transaction() as session:
            _require_table(session)
            _require_template(session, template_id)
            session.execute(
                delete(TemplateItemJoin).where(TemplateItemJoin.template_id == template_id)
            )
            for position, item in enumerate(cleaned):
                session.add(TemplateItemJoin(
                    template_id=template_id, position=position, name=item["name"],
                    kind=item["kind"], separator=item["separator"], members_json=item["members"],
                ))
        return self.joins(template_id)

    async def ai_suggestions(
        self, template_id: str, template_version_id: str, chooser: JoinChooser
    ) -> list[dict[str, Any]]:
        """GPT-6 Luna picks, per heading, whether and how its text items are joined.

        One Decisions request over heading names, item names and printed text (no image,
        no readings). Order and printed text still come from the form; the model only picks
        a fixed answer, and an unsure answer keeps the items apart.
        """
        with self._database.session() as session:
            _require_table(session)
            _require_template(session, template_id)
            sections, _ = self._analysis(_version(session, template_id, template_version_id))
        candidates = [s for s in sections if len(s["textMembers"]) >= 2][:MAX_AI_SECTIONS]
        if not candidates:
            return []
        lines = []
        for index, section in enumerate(candidates):
            sample = joined_text(
                section["textMembers"],
                {m["targetPath"]: f"［{m['label'] or '欄'}］" for m in section["textMembers"]},
            )
            others = [m["label"] for m in section["members"] if m["kind"] != "text"]
            extra = f"（ほかにチェック・記号の欄: {'、'.join(others)}）" if others else ""
            heading = f"s{index}: 見出し「{section['name']}」"
            lines.append(f"{heading}の文字の欄（順に）: {sample}{extra}")
        context = (
            "帳票の読み取り結果を人が見るとき、見出しの下でばらばらに読み取った文字の欄を"
            "1つの値にまとめて表示するかを決めます。住所・日付・氏名・電話番号のように"
            "ひと続きで読むものはまとめ、性質の違う項目が並んでいるだけならまとめません。"
            "［］は記入欄、それ以外は帳票に印字された文字です。印字の文字で区切られて"
            "いない欄どうしの間に入れる区切りも選んでください。\n" + "\n".join(lines)
        )
        questions = [
            {
                "name": f"s{index}",
                "instructions": f"s{index}「{section['name']}」の欄をどう表示するかを選ぶ。",
                "choices": [
                    {"value": value, "description": label} for value, label in AI_CHOICES
                ],
            }
            for index, section in enumerate(candidates)
        ]
        answers = await chooser.choose(context, questions)
        allowed = {value for value, _ in AI_CHOICES}
        result: list[dict[str, Any]] = []
        for index, section in enumerate(candidates):
            answer = next((a for a in answers if a.get("name") == f"s{index}"), None)
            if not answer or answer.get("type") != "choice":
                continue
            choice, confidence = answer.get("choice"), answer.get("confidence")
            if (
                choice not in allowed
                or choice == "keep"
                or isinstance(confidence, bool)
                or not isinstance(confidence, int | float)
                or confidence < AI_THRESHOLD
            ):
                continue
            result.append({
                "name": section["name"],
                "kind": "join",
                "separator": AI_SEPARATORS[str(choice)],
                "members": section["textMembers"],
            })
        return result

    def _analysis(
        self, version: TemplateVersion
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Headings with their items in reading order, and the plain join suggestions."""
        if self._storage is None or not version.layout_json:
            return [], []
        try:
            config = parse_template_config(
                self._storage.read_bytes(version.config_relative_path).decode("utf-8")
            )
            layout = LayoutDefinition.model_validate(version.layout_json)
        except Exception:
            return [], []
        mappings = [mapping for mapping in config.field_mappings if mapping.enabled]
        serialized = [mapping.model_dump(mode="json", by_alias=True) for mapping in mappings]
        structure: dict[str, Any] = build_review_display_structure(layout, serialized)
        type_by_id = {mapping.mapping_id: mapping.mapping_type for mapping in mappings}
        fields = {
            str(field["id"]): field
            for field in structure.get("fields", [])
            if isinstance(field, dict)
        }
        cells = {cell.id: cell for cell in layout.cells}
        sections: list[dict[str, Any]] = []
        suggestions: list[dict[str, Any]] = []
        for section in structure.get("parentSections", []):
            if not isinstance(section, dict):
                continue
            placed = [
                (fields[i], cells[str(fields[i].get("inputCellId"))])
                for i in section.get("fieldIds", [])
                if i in fields and fields[i].get("inputCellId") in cells
            ]
            if len(placed) < 2:
                continue
            reading = {
                cell.id: index
                for index, cell in enumerate(
                    c for line in _lines([c for _, c in placed]) for c in line
                )
            }
            placed.sort(key=lambda pair: reading[pair[1].id])
            name = str(section.get("title") or "まとめた項目")
            heading = str(section.get("id") or "")
            text = [
                (field, cell) for field, cell in placed
                if type_by_id.get(str(field["id"])) == "text"
            ]
            lines = _lines([cell for _, cell in text]) if len(text) >= 2 else []
            text_members: list[dict[str, Any]] = []
            if lines and len(lines) <= MAX_LINES:
                glue = _cross_line_brackets(
                    [pair for line in lines for pair in _printed_glue(line, cells, heading)],
                    lines,
                )
                text_order = {
                    cell.id: index
                    for index, cell in enumerate(c for line in lines for c in line)
                }
                text.sort(key=lambda pair: text_order[pair[1].id])
                text_members = [
                    {
                        "targetPath": str(field["targetPath"]),
                        "label": str(field.get("label") or ""),
                        "before": before,
                        "after": after,
                    }
                    for (field, _), (before, after) in zip(text, glue, strict=True)
                ]
            sections.append({
                "name": name,
                "members": [
                    {
                        "targetPath": str(field["targetPath"]),
                        "label": str(field.get("label") or ""),
                        "kind": "text" if type_by_id.get(str(field["id"])) == "text" else "mark",
                    }
                    for field, _ in placed
                ],
                "textMembers": text_members,
            })
            if text_members and len(text_members) == len(placed):
                suggestions.append(
                    {"name": name, "kind": "join", "separator": "", "members": text_members}
                )
        return sections, suggestions


def _lines(cells: list[LayoutCell]) -> list[list[LayoutCell]]:
    """Items in reading order: lines from the top, each left to right."""
    lines: list[list[LayoutCell]] = []
    for cell in sorted(cells, key=lambda item: item.y + item.height / 2):
        line = lines[-1] if lines else None
        if line is not None:
            top = max(other.y for other in [*line, cell])
            bottom = min(other.y + other.height for other in [*line, cell])
            if bottom - top >= 0.5 * min(other.height for other in [*line, cell]):
                line.append(cell)
                continue
        lines.append([cell])
    return [sorted(line, key=lambda item: item.x) for line in lines]


def _cross_line_brackets(
    glue: list[tuple[str, str]], lines: list[list[LayoutCell]]
) -> list[tuple[str, str]]:
    """Move an opening bracket ending a line to the next line; close brackets get openers."""
    result = [list(pair) for pair in glue]
    end = 0
    for line in lines[:-1]:
        end += len(line)
        after = result[end - 1][1]
        stripped = after.rstrip(_OPENING)
        if stripped != after:
            result[end - 1][1] = stripped
            result[end][0] = after[len(stripped):] + result[end][0]
    for pair in result:
        # 「番地）」 printed without its 「（」: the item gets the opener; empty drops both.
        for opener, closer in (("（", "）"), ("(", ")")):
            text = pair[0] + pair[1]
            if text.count(closer) > text.count(opener) and pair[1].endswith(closer):
                pair[0] = opener + pair[0]
    return [(before[:MAX_TEXT], after[:MAX_TEXT]) for before, after in result]


def _printed_glue(
    members: list[LayoutCell], cells: Mapping[str, LayoutCell], heading_id: str
) -> list[tuple[str, str]]:
    """Printed text before/after each item on the line (members sorted left to right)."""
    member_ids = {cell.id for cell in members}
    top = min(cell.y for cell in members)
    bottom = max(cell.y + cell.height for cell in members)
    height = min(cell.height for cell in members)

    def on_line(cell: LayoutCell) -> bool:
        overlap = min(bottom, cell.y + cell.height) - max(top, cell.y)
        return overlap >= 0.5 * min(height, cell.height)

    line = sorted(
        (cell for cell in cells.values() if cell.id not in member_ids and on_line(cell)),
        key=lambda cell: cell.x,
    )

    def text(cell: LayoutCell) -> str:
        return (cell.printed_label_text or cell.text or "").strip()

    def printed(cell: LayoutCell) -> bool:
        return cell.role in ("label", "ignored") and cell.id != heading_id and bool(text(cell))

    gap = max(8, height // 2)

    def walk(start: float, direction: int) -> str:
        """Contiguous printed cells from an edge, stopping at anything else."""
        found: list[LayoutCell] = []
        edge = start
        ordered = line if direction > 0 else list(reversed(line))
        for cell in ordered:
            near = cell.x if direction > 0 else cell.x + cell.width
            if (near - edge) * direction < -gap:
                continue
            if abs(near - edge) > gap or not printed(cell):
                break
            found.append(cell)
            edge = cell.x + cell.width if direction > 0 else cell.x
        if direction < 0:
            found.reverse()
        return "".join(text(cell) for cell in found)

    between: list[str] = []
    for left, right in pairwise(members):
        inside = [
            cell for cell in line
            if cell.x >= left.x + left.width - gap and cell.x + cell.width <= right.x + gap
        ]
        between.append("".join(text(cell) for cell in inside if printed(cell)))
    befores = [walk(members[0].x, -1)] + [""] * (len(members) - 1)
    afters = [*between, walk(members[-1].x + members[-1].width, 1)]
    # An opening bracket at the end of the text between two items belongs to the next one.
    for index, value in enumerate(between):
        stripped = value.rstrip(_OPENING)
        if stripped != value:
            afters[index] = stripped
            befores[index + 1] = value[len(stripped):]
    return [
        (before[:MAX_TEXT], after[:MAX_TEXT])
        for before, after in zip(befores, afters, strict=True)
    ]


def _joins(session: Session, template_id: str) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(TemplateItemJoin)
        .where(TemplateItemJoin.template_id == template_id)
        .order_by(TemplateItemJoin.position)
    )
    return [
        {
            "id": row.id, "name": row.name, "kind": row.kind, "separator": row.separator,
            "members": list(row.members_json),
        }
        for row in rows
    ]


def _validate(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if len(items) > MAX_JOINS:
        raise _invalid("items", f"まとめ方と順番は合わせて{MAX_JOINS}件までです。")
    used: dict[str, set[str]] = {"join": set(), "order": set()}
    result: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        kind = str(item.get("kind") or "join")
        if kind not in used:
            raise _invalid(f"items.{index}.kind", "まとめ方の種類を確認できません。")
        name = " ".join(str(item.get("name") or "").split())
        if not name or len(name) > 100:
            raise _invalid(
                f"items.{index}.name", "まとめた項目の名前を100文字以内で入れてください。"
            )
        separator = str(item.get("separator") or "") if kind == "join" else ""
        if len(separator) > MAX_SEPARATOR:
            raise _invalid(
                f"items.{index}.separator", f"区切りは{MAX_SEPARATOR}文字以内にしてください。"
            )
        members = item.get("members")
        if not isinstance(members, list) or not 2 <= len(members) <= MAX_MEMBERS:
            raise _invalid(f"items.{index}.members", f"「{name}」には欄を2つ以上選んでください。")
        cleaned: list[dict[str, str]] = []
        for member in members:
            path = str(member.get("targetPath") or "") if isinstance(member, Mapping) else ""
            if not is_supported_field_path(path):
                raise _invalid(f"items.{index}.members", f"「{name}」の欄を確認できません。")
            if path in used[kind]:
                raise _invalid(
                    f"items.{index}.members",
                    f"「{name}」の欄が、ほかの{KIND_NAMES[kind]}にも入っています。",
                )
            used[kind].add(path)
            before, after = (
                (str(member.get(key) or "") for key in ("before", "after"))
                if kind == "join" else ("", "")
            )
            if len(before) > MAX_TEXT or len(after) > MAX_TEXT:
                raise _invalid(
                    f"items.{index}.members",
                    f"前後に付ける文字は{MAX_TEXT}文字以内にしてください。",
                )
            cleaned.append({"targetPath": path, "before": before, "after": after})
        result.append({"name": name, "kind": kind, "separator": separator, "members": cleaned})
    return result


def _version(session: Session, template_id: str, version_id: str) -> TemplateVersion:
    version = session.get(TemplateVersion, version_id)
    if version is None or version.template_id != template_id:
        raise AppError(
            status=404, code="TEMPLATE_VERSION_NOT_FOUND", title="Template version not found",
            detail="指定されたテンプレートが見つかりません。",
        )
    return version


def _require_table(session: Session) -> None:
    if not inspect(session.connection()).has_table(TemplateItemJoin.__tablename__):
        raise AppError(
            status=503, code="ITEM_JOINS_NOT_MIGRATED", title="Database migration required",
            detail="項目のまとめ方を使うにはデータベースの移行が必要です。",
        )


def _require_template(session: Session, template_id: str) -> Template:
    template = session.get(Template, template_id)
    if template is None:
        raise AppError(
            status=404, code="TEMPLATE_NOT_FOUND", title="Template not found",
            detail="指定されたテンプレートが見つかりません。",
        )
    return template


def _invalid(field: str, message: str) -> AppError:
    return AppError(
        status=422, code="ITEM_JOIN_INVALID", title="Invalid item join", detail=message,
        field_errors=[FieldError(field=field, message=message)],
    )
