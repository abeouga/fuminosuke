import type { LayoutCell } from "../types";

export type Point = { x: number; y: number };
export type Rect = Point & { width: number; height: number };
function pointToSegmentDistance(point: Point, start: Point, end: Point) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  if (dx === 0 && dy === 0) return Math.hypot(point.x - start.x, point.y - start.y);
  const position = Math.max(0, Math.min(
    1,
    ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy),
  ));
  return Math.hypot(
    point.x - (start.x + position * dx),
    point.y - (start.y + position * dy),
  );
}
function segmentOrientation(first: Point, second: Point, third: Point) {
  return (second.x - first.x) * (third.y - first.y)
    - (second.y - first.y) * (third.x - first.x);
}
function pointOnSegment(point: Point, start: Point, end: Point) {
  const epsilon = 0.000001;
  return Math.abs(segmentOrientation(start, end, point)) <= epsilon
    && point.x >= Math.min(start.x, end.x) - epsilon
    && point.x <= Math.max(start.x, end.x) + epsilon
    && point.y >= Math.min(start.y, end.y) - epsilon
    && point.y <= Math.max(start.y, end.y) + epsilon;
}
function segmentsIntersect(firstStart: Point, firstEnd: Point, secondStart: Point, secondEnd: Point) {
  const firstSide = segmentOrientation(firstStart, firstEnd, secondStart);
  const secondSide = segmentOrientation(firstStart, firstEnd, secondEnd);
  const thirdSide = segmentOrientation(secondStart, secondEnd, firstStart);
  const fourthSide = segmentOrientation(secondStart, secondEnd, firstEnd);
  if (
    ((firstSide > 0 && secondSide < 0) || (firstSide < 0 && secondSide > 0))
    && ((thirdSide > 0 && fourthSide < 0) || (thirdSide < 0 && fourthSide > 0))
  ) return true;
  return pointOnSegment(secondStart, firstStart, firstEnd)
    || pointOnSegment(secondEnd, firstStart, firstEnd)
    || pointOnSegment(firstStart, secondStart, secondEnd)
    || pointOnSegment(firstEnd, secondStart, secondEnd);
}
export function segmentDistance(
  firstStart: Point,
  firstEnd: Point,
  secondStart: Point,
  secondEnd: Point,
) {
  if (segmentsIntersect(firstStart, firstEnd, secondStart, secondEnd)) return 0;
  return Math.min(
    pointToSegmentDistance(firstStart, secondStart, secondEnd),
    pointToSegmentDistance(firstEnd, secondStart, secondEnd),
    pointToSegmentDistance(secondStart, firstStart, firstEnd),
    pointToSegmentDistance(secondEnd, firstStart, firstEnd),
  );
}
export type MeaningRouteSegment = { start: Point; end: Point };
export function rectCenter(rect: Rect): Point {
  return {
    x: rect.x + rect.width / 2,
    y: rect.y + rect.height / 2,
  };
}
export function routeSegments(points: Point[]): MeaningRouteSegment[] {
  return points.slice(1).flatMap((end, index) => {
    const start = points[index];
    return Math.hypot(end.x - start.x, end.y - start.y) < 0.01
      ? []
      : [{ start, end }];
  });
}
export function routeSegmentKey(segment: MeaningRouteSegment) {
  const start = `${segment.start.x.toFixed(2)},${segment.start.y.toFixed(2)}`;
  const end = `${segment.end.x.toFixed(2)},${segment.end.y.toFixed(2)}`;
  return start < end ? `${start}|${end}` : `${end}|${start}`;
}
export function cellsSelectedByRect(
  cells: LayoutCell[],
  selection: Rect,
): LayoutCell[] {
  if (selection.width <= 0 || selection.height <= 0) return [];
  return cells.filter((cell) => {
    const overlapWidth = Math.max(
      0,
      Math.min(selection.x + selection.width, cell.x + cell.width)
        - Math.max(selection.x, cell.x),
    );
    const overlapHeight = Math.max(
      0,
      Math.min(selection.y + selection.height, cell.y + cell.height)
        - Math.max(selection.y, cell.y),
    );
    if (overlapWidth === 0 || overlapHeight === 0) return false;
    const centerX = cell.x + cell.width / 2;
    const centerY = cell.y + cell.height / 2;
    const centerInside = centerX >= selection.x
      && centerX <= selection.x + selection.width
      && centerY >= selection.y
      && centerY <= selection.y + selection.height;
    const coveredRatio = overlapWidth * overlapHeight / (cell.width * cell.height);
    return centerInside || coveredRatio >= 0.25;
  });
}
export function pointInsideRect(point: Point, rect: Rect): boolean {
  return point.x >= rect.x
    && point.x <= rect.x + rect.width
    && point.y >= rect.y
    && point.y <= rect.y + rect.height;
}
export function boundingRect(
  cells: LayoutCell[],
  canonicalWidth: number,
  canonicalHeight: number,
): Rect {
  const padding = 4;
  const left = Math.max(0, Math.min(...cells.map((cell) => cell.x)) - padding);
  const top = Math.max(0, Math.min(...cells.map((cell) => cell.y)) - padding);
  const right = Math.min(
    canonicalWidth,
    Math.max(...cells.map((cell) => cell.x + cell.width)) + padding,
  );
  const bottom = Math.min(
    canonicalHeight,
    Math.max(...cells.map((cell) => cell.y + cell.height)) + padding,
  );
  return {
    x: left,
    y: top,
    width: Math.max(1, right - left),
    height: Math.max(1, bottom - top),
  };
}
export function distanceToCenterSquared(cell: LayoutCell, point: Point): number {
  const deltaX = cell.x + cell.width / 2 - point.x;
  const deltaY = cell.y + cell.height / 2 - point.y;
  return deltaX * deltaX + deltaY * deltaY;
}
export function intervalOverlap(
  startA: number,
  lengthA: number,
  startB: number,
  lengthB: number,
): number {
  return Math.max(
    0,
    Math.min(startA + lengthA, startB + lengthB) - Math.max(startA, startB),
  );
}
export function segmentIntersectsRect(
  start: Point,
  end: Point,
  rect: Rect,
  padding = 0,
): boolean {
  const left = rect.x - padding;
  const right = rect.x + rect.width + padding;
  const top = rect.y - padding;
  const bottom = rect.y + rect.height + padding;
  const deltaX = end.x - start.x;
  const deltaY = end.y - start.y;
  let minimum = 0;
  let maximum = 1;
  const edges = [
    [-deltaX, start.x - left],
    [deltaX, right - start.x],
    [-deltaY, start.y - top],
    [deltaY, bottom - start.y],
  ];
  for (const [direction, distance] of edges) {
    if (direction === 0) {
      if (distance < 0) return false;
      continue;
    }
    const ratio = distance / direction;
    if (direction < 0) minimum = Math.max(minimum, ratio);
    else maximum = Math.min(maximum, ratio);
    if (minimum > maximum) return false;
  }
  return true;
}
