"""
Unit tests for ``services.cv.column_detector`` — pure page geometry.

Synthetic ``TextLine`` lists stand in for a PyMuPDF page so the layout
detection can be exercised without any PDF I/O:

* single-column pages stay SINGLE_COLUMN (spec §18-A / §3);
* real side-by-side columns become TWO_COLUMNS with left/right in their own
  internal order (§18-B / §5);
* a name / title bar spanning the page width is peeled into ``header`` and
  read BEFORE the columns (§18-C / §6);
* common false positives (centred title, a short contact line, a stacked
  two-block page) do NOT trigger a two-column split (§4).
"""

from __future__ import annotations

from services.cv.column_detector import (
    LayoutType,
    TextLine,
    _text_column_count,
    detect_page_layout,
)

PAGE_W = 596.0
PAGE_H = 842.0


def _line(text: str, x0: float, y0: float, w: float = 180.0, h: float = 12.0) -> TextLine:
    return TextLine(text=text, x0=x0, y0=y0, x1=x0 + w, y1=y0 + h)


def _single_column_lines() -> list[TextLine]:
    return [_line(f"line {i}", 60.0, 80.0 + i * 20.0, w=430.0) for i in range(12)]


def _two_column_lines(*, header: list[TextLine] | None = None) -> list[TextLine]:
    left = [_line(f"L{i}", 50.0, 220.0 + i * 22.0, w=150.0) for i in range(9)]
    right = [_line(f"R{i}", 340.0, 200.0 + i * 22.0, w=200.0) for i in range(9)]
    return (header or []) + left + right


# ---------------------------------------------------------------------------
# A. single column
# ---------------------------------------------------------------------------

def test_single_column_page_is_single():
    res = detect_page_layout(_single_column_lines(), page_width=PAGE_W, page_height=PAGE_H)
    assert res.layout is LayoutType.SINGLE_COLUMN
    assert res.header == []
    assert len(res.columns) == 1
    assert [l.text for l in res.columns[0]] == [f"line {i}" for i in range(12)]


def test_very_short_page_is_single():
    lines = [_line("x", 60.0, 80.0 + i * 20.0, w=400.0) for i in range(4)]
    res = detect_page_layout(lines, page_width=PAGE_W, page_height=PAGE_H)
    assert res.layout is LayoutType.SINGLE_COLUMN


# ---------------------------------------------------------------------------
# B. two columns
# ---------------------------------------------------------------------------

def test_two_column_page_detected_and_ordered():
    res = detect_page_layout(_two_column_lines(), page_width=PAGE_W, page_height=PAGE_H)
    assert res.layout is LayoutType.TWO_COLUMNS
    assert len(res.columns) == 2
    left, right = res.columns
    assert [l.text for l in left] == [f"L{i}" for i in range(9)]
    assert [l.text for l in right] == [f"R{i}" for i in range(9)]
    assert res.confidence > 0.6


def test_two_column_left_then_right_never_interleaved():
    # rows at the *same* y must not be zipped together
    lines = []
    for i in range(9):
        y = 200.0 + i * 22.0
        lines.append(_line(f"left-{i}", 50.0, y, w=150.0))
        lines.append(_line(f"right-{i}", 340.0, y, w=190.0))
    res = detect_page_layout(lines, page_width=PAGE_W, page_height=PAGE_H)
    assert res.layout is LayoutType.TWO_COLUMNS
    order = [l.text for l in res.columns[0]] + [l.text for l in res.columns[1]]
    assert order == [f"left-{i}" for i in range(9)] + [f"right-{i}" for i in range(9)]


# ---------------------------------------------------------------------------
# C. two columns + full-width header
# ---------------------------------------------------------------------------

def test_full_width_header_peeled_before_columns():
    header = [
        _line("JANE MARTIN", 200.0, 40.0, w=200.0),
        _line("Full Stack Developer", 220.0, 66.0, w=160.0),
        _line("jane@example.com | +212 600 000000 | linkedin.com/in/jane", 90.0, 92.0, w=420.0),
    ]
    res = detect_page_layout(_two_column_lines(header=header), page_width=PAGE_W, page_height=PAGE_H)
    assert res.layout is LayoutType.TWO_COLUMNS
    assert res.has_full_width_header
    assert [l.text for l in res.header] == [
        "JANE MARTIN",
        "Full Stack Developer",
        "jane@example.com | +212 600 000000 | linkedin.com/in/jane",
    ]
    # header lines are NOT duplicated into the columns
    body_text = {l.text for col in res.columns for l in col}
    assert "JANE MARTIN" not in body_text


def test_staggered_column_start_does_not_leak_into_header():
    # left column starts ~40pt lower than the right one (very common on
    # sidebar CVs) — the offset must not pull right-column lines up into
    # the header band.
    header = [_line("NAME SURNAME", 210.0, 40.0, w=180.0)]
    left = [_line(f"L{i}", 50.0, 240.0 + i * 22.0, w=150.0) for i in range(8)]
    right = [_line(f"R{i}", 340.0, 200.0 + i * 22.0, w=190.0) for i in range(9)]
    res = detect_page_layout(header + left + right, page_width=PAGE_W, page_height=PAGE_H)
    assert res.layout is LayoutType.TWO_COLUMNS
    assert [l.text for l in res.header] == ["NAME SURNAME"]
    assert res.columns[1][0].text == "R0"


# ---------------------------------------------------------------------------
# false positives (spec §4)
# ---------------------------------------------------------------------------

def test_centered_title_line_is_not_a_two_column_page():
    lines = [_line("MY BIG CV TITLE", 210.0, 60.0, w=170.0)]
    lines += [_line(f"body {i}", 60.0, 90.0 + i * 20.0, w=440.0) for i in range(11)]
    res = detect_page_layout(lines, page_width=PAGE_W, page_height=PAGE_H)
    assert res.layout is LayoutType.SINGLE_COLUMN


def test_contact_line_with_gaps_is_not_two_column():
    lines = [_line("Name Surname", 60.0, 60.0, w=200.0)]
    lines.append(_line("email@x.com", 60.0, 84.0, w=110.0))
    lines.append(_line("+212 600 000000", 360.0, 84.0, w=120.0))  # far right, one row only
    lines += [_line(f"para {i}", 60.0, 120.0 + i * 20.0, w=450.0) for i in range(10)]
    res = detect_page_layout(lines, page_width=PAGE_W, page_height=PAGE_H)
    assert res.layout is LayoutType.SINGLE_COLUMN


def test_two_stacked_blocks_are_not_side_by_side_columns():
    # a right-aligned block that sits BELOW the left block, not beside it
    left = [_line(f"L{i}", 50.0, 100.0 + i * 20.0, w=150.0) for i in range(6)]
    right = [_line(f"R{i}", 340.0, 320.0 + i * 20.0, w=190.0) for i in range(6)]
    res = detect_page_layout(left + right, page_width=PAGE_W, page_height=PAGE_H)
    assert res.layout is LayoutType.SINGLE_COLUMN


# ---------------------------------------------------------------------------
# D. per-page: a document mixes layouts page by page — detector is per page
# ---------------------------------------------------------------------------

def test_detector_is_pure_per_page():
    p1 = detect_page_layout(_two_column_lines(), page_width=PAGE_W, page_height=PAGE_H)
    p2 = detect_page_layout(_single_column_lines(), page_width=PAGE_W, page_height=PAGE_H)
    assert p1.layout is LayoutType.TWO_COLUMNS
    assert p2.layout is LayoutType.SINGLE_COLUMN


# ---------------------------------------------------------------------------
# confidence reflects reconstruction quality (spec §15)
# ---------------------------------------------------------------------------

def test_messy_gutter_lowers_confidence_or_falls_back():
    # many lines straddle the gutter -> either SINGLE_COLUMN or low confidence
    lines = _two_column_lines()
    for i in range(5):
        lines.append(_line(f"wide-{i}", 80.0, 240.0 + i * 40.0, w=430.0))
    res = detect_page_layout(lines, page_width=PAGE_W, page_height=PAGE_H)
    assert res.layout is LayoutType.SINGLE_COLUMN or res.confidence < 0.9


def test_text_column_count_ignores_sparse_right_aligned_tab():
    # one text column + a sparse right-aligned date/location tab stop
    lines = [_line(f"body line number {i}", 55.0, 90.0 + i * 22.0, w=360.0) for i in range(16)]
    for i, tag in enumerate(["2025", "Rabat", "2024"]):
        lines.append(_line(tag, 520.0, 90.0 + i * 90.0, w=45.0))
    assert _text_column_count(lines, PAGE_H) == 1


def test_text_column_count_a_dense_right_column_is_not_flagged():
    # even a busy right-aligned column of dates stays < 3 (no false flag)
    lines = [_line(f"body line number {i}", 55.0, 90.0 + i * 22.0, w=360.0) for i in range(16)]
    for i in range(10):
        lines.append(_line(f"20{i:02d}", 520.0, 90.0 + i * 30.0, w=45.0))
    assert _text_column_count(lines, PAGE_H) < 3


def test_text_column_count_flags_three_stacked_columns():
    lines: list[TextLine] = []
    for i in range(16):
        y = 90.0 + i * 30.0
        lines.append(_line(f"left col line {i} words", 40.0, y, w=150.0))
        lines.append(_line(f"middle col line {i} words", 240.0, y, w=150.0))
        lines.append(_line(f"right col line {i} words", 430.0, y, w=140.0))
    assert _text_column_count(lines, PAGE_H) >= 3
