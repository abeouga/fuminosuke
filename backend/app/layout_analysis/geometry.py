from __future__ import annotations

from statistics import median

from app.domain.template_definition import CellGeometry, NormalizedBox, TemplateCell


def _overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> int:
    return max(0, min(end_a, end_b) - max(start_a, start_b))


def _clusters(values: list[tuple[str, float]], tolerance: float) -> dict[str, int]:
    result: dict[str, int] = {}
    centers: list[float] = []
    for cell_id, value in sorted(values, key=lambda item: item[1]):
        match = next(
            (index for index, center in enumerate(centers) if abs(value - center) <= tolerance),
            None,
        )
        if match is None:
            centers.append(value)
            match = len(centers) - 1
        else:
            members = [
                source_value for source_id, source_value in values if result.get(source_id) == match
            ]
            centers[match] = (sum(members) + value) / (len(members) + 1)
        result[cell_id] = match
    return result


def analyze_geometry(
    cells: list[TemplateCell], canonical_width: int, canonical_height: int
) -> list[CellGeometry]:
    if not cells:
        return []
    median_width = median(cell.bbox.width for cell in cells)
    median_height = median(cell.bbox.height for cell in cells)
    centers = {
        cell.id: (
            cell.bbox.x + cell.bbox.width / 2,
            cell.bbox.y + cell.bbox.height / 2,
        )
        for cell in cells
    }
    row_clusters = _clusters(
        [(cell.id, centers[cell.id][1]) for cell in cells], max(4, median_height * 0.55)
    )
    column_clusters = _clusters(
        [(cell.id, centers[cell.id][0]) for cell in cells], max(4, median_width * 0.55)
    )
    value_column_counts: dict[int, int] = {}
    for cell in cells:
        if cell.type == "VALUE":
            cluster = column_clusters[cell.id]
            value_column_counts[cluster] = value_column_counts.get(cluster, 0) + 1

    result: list[CellGeometry] = []
    for cell in cells:
        box = cell.bbox
        cx, cy = centers[cell.id]
        left: list[tuple[float, str]] = []
        right: list[tuple[float, str]] = []
        above: list[tuple[float, str]] = []
        below: list[tuple[float, str]] = []
        same_row: list[str] = []
        same_column: list[str] = []
        contained: list[str] = []
        containers: list[tuple[int, str]] = []
        for other in cells:
            if other.id == cell.id:
                continue
            other_box = other.bbox
            ox, oy = centers[other.id]
            vertical_overlap = _overlap(
                box.y, box.y + box.height, other_box.y, other_box.y + other_box.height
            )
            horizontal_overlap = _overlap(
                box.x, box.x + box.width, other_box.x, other_box.x + other_box.width
            )
            shares_row = vertical_overlap >= min(box.height, other_box.height) * 0.45
            shares_column = horizontal_overlap >= min(box.width, other_box.width) * 0.45
            if shares_row:
                same_row.append(other.id)
                if ox < cx:
                    left.append((max(0, box.x - (other_box.x + other_box.width)), other.id))
                else:
                    right.append((max(0, other_box.x - (box.x + box.width)), other.id))
            if shares_column:
                same_column.append(other.id)
                if oy < cy:
                    above.append((max(0, box.y - (other_box.y + other_box.height)), other.id))
                else:
                    below.append((max(0, other_box.y - (box.y + box.height)), other.id))
            if (
                box.x <= other_box.x
                and box.y <= other_box.y
                and box.x + box.width >= other_box.x + other_box.width
                and box.y + box.height >= other_box.y + other_box.height
            ):
                contained.append(other.id)
            if (
                other_box.x <= box.x
                and other_box.y <= box.y
                and other_box.x + other_box.width >= box.x + box.width
                and other_box.y + other_box.height >= box.y + box.height
            ):
                containers.append((other_box.width * other_box.height, other.id))
        left.sort()
        right.sort()
        above.sort()
        below.sort()
        result.append(
            CellGeometry(
                cell_id=cell.id,
                left=[item[1] for item in left],
                right=[item[1] for item in right],
                above=[item[1] for item in above],
                below=[item[1] for item in below],
                nearest_left=left[0][1] if left else None,
                nearest_right=right[0][1] if right else None,
                nearest_above=above[0][1] if above else None,
                nearest_below=below[0][1] if below else None,
                same_row_cells=sorted(same_row),
                same_column_cells=sorted(same_column),
                horizontal_distance=min([item[0] for item in left + right], default=None),
                vertical_distance=min([item[0] for item in above + below], default=None),
                row_cluster=row_clusters[cell.id],
                column_cluster=column_clusters[cell.id],
                contained_cells=sorted(contained),
                containing_cell=min(containers)[1] if containers else None,
                normalized_bbox=NormalizedBox(
                    left=round(box.x * 1000 / canonical_width),
                    top=round(box.y * 1000 / canonical_height),
                    right=round((box.x + box.width) * 1000 / canonical_width),
                    bottom=round((box.y + box.height) * 1000 / canonical_height),
                ),
                record_pattern_candidate=(
                    cell.type == "VALUE"
                    and value_column_counts.get(column_clusters[cell.id], 0) >= 2
                ),
            )
        )
    return result
