"""
CV Assistant - CV Generation
Service: column_detector

Pure geometry. Given the text lines of ONE PDF page (with their bounding
boxes) it decides whether the page is single- or two-column, isolates a
full-width header band (name / title / contact bar that spans the whole
page width above the columns), and returns the lines grouped in reading
order — with a 0..1 confidence for the reconstruction.

No PyMuPDF, no AI, no section knowledge. ``pdf_layout_reader`` feeds this
module and turns its result back into text.

Detection, in order (spec §2-§6):

1. sort lines top-to-bottom, then left-to-right;
2. find a rough gutter, ignoring the top ~12% of the page (a full-width
   name/title bar would otherwise hide it);
3. peel the full-width header: the columns "begin" at the first y where a
   line clearly LEFT of the gutter and a line clearly RIGHT of it coexist
   within a short vertical window; everything above is the header (name /
   title lines straddle the gutter, so they never count as column body,
   and a column that starts a few lines lower than the other does not leak
   up into the header);
4. re-find the gutter on the body = the MIDDLE of the widest vertical band
   that no line crosses, in the central 25%..75% of the page;
5. accept TWO_COLUMNS only when the gutter is genuinely empty over most of
   the body height AND both sides carry a real share of the lines AND the
   two sides vertically interleave (side-by-side, not stacked);
6. confidence blends gutter cleanliness, column balance and interleave. A
   SINGLE_COLUMN fallback on a page that still shows strong horizontal
   structure (many rows split into widely-separated fragments — a
   multi-zone / Canva layout) gets a LOW confidence instead of 1.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class LayoutType(str, Enum):
    SINGLE_COLUMN = "SINGLE_COLUMN"
    TWO_COLUMNS = "TWO_COLUMNS"


@dataclass
class TextLine:
    """One visual line of text with its bounding box (PDF points)."""
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0


@dataclass
class PageLayout:
    """Result of analysing one page."""
    layout: LayoutType
    header: list[TextLine] = field(default_factory=list)          # full-width, read first
    columns: list[list[TextLine]] = field(default_factory=list)   # [all] or [left, right]
    confidence: float = 1.0                                       # 0..1
    split_x: float | None = None

    @property
    def has_full_width_header(self) -> bool:
        return bool(self.header)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

_MIN_LINES_FOR_DETECTION = 6      # below this, a page is single-column by default
_MIN_GUTTER_PT = 24.0            # a real inter-column gutter is at least this wide
_MIN_SMALLER_SIDE_RATIO = 0.15   # the thinner column must hold >= 15% of body lines
_MIN_SMALLER_SIDE_LINES = 3
_HEADER_MAX_Y_FRAC = 0.42        # the header never reaches past this fraction of the page
_HEADER_WIDE_FRAC = 0.52         # a "wide" line spans >= this fraction of the page width
_GUTTER_CLEAN_MIN = 0.62         # gutter must be text-free over >= this fraction of body height
_INTERLEAVE_MIN = 0.30           # the two sides' y-ranges must overlap by >= this
_HEADER_COL_START_WIN_FRAC = 0.075   # vert. window in which both columns must appear
_HEADER_CENTERED_FRAC = 0.18        # a line whose centre is within this of page centre is header-ish


def detect_page_layout(
    lines: list[TextLine],
    *,
    page_width: float,
    page_height: float,
) -> PageLayout:
    """Analyse one page. Never raises; unclear pages fall back to SINGLE_COLUMN."""
    lines = sorted((l for l in lines if l.text.strip()), key=lambda l: (round(l.y0, 1), l.x0))
    if len(lines) < _MIN_LINES_FOR_DETECTION:
        return PageLayout(LayoutType.SINGLE_COLUMN, columns=[lines], confidence=1.0)

    # Three or more stacked text columns (a Canva / Europass grid) cannot be
    # reconstructed reliably as either one or two columns — whatever layout
    # we pick below, its confidence is capped so the pipeline flags it
    # (spec §15, §19). One text column plus a right-aligned tab stop for
    # dates / durations / locations is NOT three columns.
    _multi_zone = _text_column_count(lines, page_height) >= 3

    def _single(header_lines: list[TextLine], body_lines: list[TextLine]) -> PageLayout:
        """SINGLE_COLUMN result, low confidence on a genuine multi-zone grid."""
        return PageLayout(LayoutType.SINGLE_COLUMN, header=header_lines,
                          columns=[body_lines], confidence=0.5 if _multi_zone else 1.0)

    # First locate the gutter on the whole page, then peel off everything
    # strictly above the point where BOTH columns start (the full-width
    # header — name / title / contact bar, spec §6).
    # A full-width name / title / contact bar crosses the whole page and
    # would hide the gutter, so the first gutter pass ignores the top ~12%
    # of the page (a rough header exclusion). The real header cut is then
    # made by ``_peel_header`` on the full line set.
    top_y = lines[0].y0
    rough_body = [l for l in lines if l.y0 > top_y + 0.12 * page_height]
    split_x, _ = _find_gutter(rough_body if len(rough_body) >= _MIN_LINES_FOR_DETECTION
                              else lines, page_width)
    header, body = _peel_header(
        lines, split_x if split_x is not None else page_width / 2.0,
        page_width, page_height,
    )
    if len(body) < _MIN_LINES_FOR_DETECTION:
        return _single(header, body)

    # re-locate the gutter on the body alone (more precise once the
    # full-width header is out of the way)
    body_split_x, _ = _find_gutter(body, page_width)
    if body_split_x is None:
        return _single(header, body)
    split_x = body_split_x

    left = sorted((l for l in body if l.cx < split_x), key=lambda l: (round(l.y0, 1), l.x0))
    right = sorted((l for l in body if l.cx >= split_x), key=lambda l: (round(l.y0, 1), l.x0))

    smaller, larger = sorted((len(left), len(right)))
    if smaller < _MIN_SMALLER_SIDE_LINES or smaller / len(body) < _MIN_SMALLER_SIDE_RATIO:
        return _single(header, body)

    # A genuine gutter (min horizontal x-gap between the two clusters) must exist.
    if _cluster_gap(left, right) < _MIN_GUTTER_PT:
        return _single(header, body)

    cleanliness = _gutter_cleanliness(body, split_x)
    interleave = _interleave(left, right)
    balance = smaller / larger if larger else 0.0

    if cleanliness < _GUTTER_CLEAN_MIN or interleave < _INTERLEAVE_MIN:
        # looks stacked, not side-by-side -> not really two columns
        return _single(header, body)

    confidence = round(
        0.5 * cleanliness + 0.3 * min(1.0, interleave * 1.6) + 0.2 * min(1.0, balance * 2.2),
        3,
    )
    if _multi_zone:
        confidence = min(confidence, 0.5)
    return PageLayout(
        LayoutType.TWO_COLUMNS,
        header=header,
        columns=[left, right],
        confidence=confidence,
        split_x=split_x,
    )


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

def _peel_header(
    lines: list[TextLine], split_x: float, page_width: float, page_height: float
) -> tuple[list[TextLine], list[TextLine]]:
    """
    Isolate the full-width header band (spec §6): the name / title / contact
    lines that sit ABOVE the two columns, spanning (or floating across) the
    whole page width.

    The columns "begin" at the smallest y where a line that is *clearly*
    left of the gutter and a line that is *clearly* right of it coexist
    within a short vertical window. Name / title lines float near the page
    centre and straddle the gutter, so they are never "clearly" on one
    side — they stay in the header. A staggered column start (one column a
    few lines taller than the other, very common on sidebar CVs) does not
    leak into the header because the window tolerates the offset.

    Returns ``([], lines)`` when there is no genuine top band (columns start
    at the very top, or the peeled band does not look like a header).
    """
    if len(lines) < _MIN_LINES_FOR_DETECTION:
        return [], lines

    # "clearly" on one side = does not straddle the gutter. Name / title
    # lines are centred and straddle it, so they never count as column body.
    left_ys = sorted(l.y0 for l in lines if l.x1 <= split_x)
    right_ys = sorted(l.y0 for l in lines if l.x0 >= split_x)
    if not left_ys or not right_ys:
        return [], lines

    win = _HEADER_COL_START_WIN_FRAC * page_height
    col_start_y: float | None = None
    for ly in left_ys:
        for ry in right_ys:
            if abs(ly - ry) <= win:
                col_start_y = min(ly, ry)
                break
        if col_start_y is not None:
            break
    if col_start_y is None:
        return [], lines

    top_y = lines[0].y0
    if col_start_y - top_y < 4.0:                                   # columns start at the top
        return [], lines
    if col_start_y > top_y + _HEADER_MAX_Y_FRAC * page_height:      # "header" reaches too far down
        return [], lines

    cut = col_start_y - 1.0
    header = [l for l in lines if l.y0 < cut]
    body = [l for l in lines if l.y0 >= cut]
    if not header or len(body) < _MIN_LINES_FOR_DETECTION:
        return [], lines

    # The band must actually look like a header: at least one wide,
    # gutter-crossing or roughly page-centred line (a name or a contact bar),
    # not just a short indented run that happens to sit high on one side.
    looks_like_header = any(
        l.width >= _HEADER_WIDE_FRAC * page_width
        or (l.x0 < split_x < l.x1)
        or abs(l.cx - page_width / 2.0) < page_width * _HEADER_CENTERED_FRAC
        for l in header
    )
    if not looks_like_header:
        return [], lines
    return header, body


# ---------------------------------------------------------------------------
# Gutter
# ---------------------------------------------------------------------------

def _find_gutter(body: list[TextLine], page_width: float) -> tuple[float | None, int]:
    """
    Return ``(split_x, crossings)`` — the x in the central 25%..75% band
    with the fewest lines crossing it, or ``(None, ...)`` when even the
    best candidate is crossed a lot (=> single column).
    """
    lo, hi = page_width * 0.25, page_width * 0.75
    samples: list[tuple[float, int]] = []
    x = lo
    while x <= hi:
        crossings = sum(1 for l in body if l.x0 < x - 1 and l.x1 > x + 1)
        samples.append((x, crossings))
        x += 3.0
    if not samples:
        return None, 0

    best_crossings = min(c for _, c in samples)
    # allow at most a couple of stray full-width lines to cross the gutter
    if best_crossings > max(2, int(0.08 * len(body))):
        return None, best_crossings

    # The gutter is the MIDDLE of the widest band that stays at the minimum
    # crossing count — not its left edge, so a slightly-indented full-width
    # line is not mistaken for one column's content.
    longest: list[float] = []
    current: list[float] = []
    for x, c in samples:
        if c <= best_crossings:
            current.append(x)
        else:
            if len(current) > len(longest):
                longest = current
            current = []
    if len(current) > len(longest):
        longest = current
    return longest[len(longest) // 2], best_crossings


_COL_CLUSTER_GAP = 55.0          # x0 values >55pt apart start a new left-edge cluster
_COL_MIN_SHARE = 0.20           # a real text column holds >= 20% of the body lines
_COL_MIN_Y_SPAN = 0.30         # ... spanning >= 30% of the page height


def _text_column_count(lines: list[TextLine], page_height: float) -> int:
    """
    Number of distinct vertical text columns on the page, from the
    clustering of line LEFT edges (``x0``).

    A cluster counts as a real column only if it holds >= 20% of the body
    lines AND its lines span >= 30% of the page height. So a single-column
    CV with a right-aligned tab stop (dates / locations) still counts as
    ONE column when that tab stop is sparse, and as at most two when it is
    dense — while a true 3-zone grid returns 3+.
    """
    if len(lines) < 6:
        return 1
    xs = sorted(lines, key=lambda l: l.x0)
    groups: list[list[TextLine]] = [[xs[0]]]
    for ln in xs[1:]:
        if ln.x0 - groups[-1][-1].x0 > _COL_CLUSTER_GAP:
            groups.append([ln])
        else:
            groups[-1].append(ln)

    n = len(lines)
    real = 0
    for g in groups:
        if len(g) / n < _COL_MIN_SHARE:
            continue
        y_span = (max(l.y1 for l in g) - min(l.y0 for l in g)) / max(1.0, page_height)
        if y_span >= _COL_MIN_Y_SPAN:
            real += 1
    return max(1, real)


def _cluster_gap(left: list[TextLine], right: list[TextLine]) -> float:
    """Horizontal gap between the right edge of the left cluster and the left edge of the right one."""
    if not left or not right:
        return 0.0
    return min(r.x0 for r in right) - max(l.x1 for l in left)


def _gutter_cleanliness(body: list[TextLine], split_x: float) -> float:
    """Fraction of the body's vertical span over which NO line crosses `split_x`."""
    if not body:
        return 1.0
    y0 = min(l.y0 for l in body)
    y1 = max(l.y1 for l in body)
    span = max(1.0, y1 - y0)
    crossing = [(l.y0, l.y1) for l in body if l.x0 < split_x - 1 and l.x1 > split_x + 1]
    covered = _union_length(crossing)
    return max(0.0, min(1.0, 1.0 - covered / span))


def _interleave(left: list[TextLine], right: list[TextLine]) -> float:
    """How much the two columns' vertical ranges overlap (0..1 of the smaller range)."""
    if not left or not right:
        return 0.0
    l0, l1 = min(l.y0 for l in left), max(l.y1 for l in left)
    r0, r1 = min(r.y0 for r in right), max(r.y1 for r in right)
    overlap = max(0.0, min(l1, r1) - max(l0, r0))
    smaller = min(l1 - l0, r1 - r0)
    return overlap / smaller if smaller > 0 else 0.0


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _union_length(intervals: list[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    intervals = sorted(intervals)
    total = 0.0
    cur_start, cur_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
    total += cur_end - cur_start
    return total
