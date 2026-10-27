"""Modul 3: Enkel startliste med et selvstændigt, justerbart starttidspunkt."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .course import Course, rounding_sides
from .starts import Start, positive_number


def write_pdf(
    path: Path,
    starts: list[Start],
    first_start: datetime,
    *,
    start_index: int = 1,
    title: str = "Onsdagsbane Afslutningssejlads",
    course: Course | None = None,
    initial_side: str = "port",
    distance_nm: Decimal | None = None,
) -> None:
    """Print colored course letters, then start time and boat/competitor.

    first_start is the time of the boat at the one-based start_index.
    Times use precomputed delay differences, independent of Start.time.
    Changing first_start shifts the whole list without recalculating handicaps.
    The input list is not mutated. Title and first_start date head the PDF.
    """
    if not starts:
        raise ValueError("Startlisten er tom.")
    if type(start_index) is not int or not 1 <= start_index <= len(starts):
        raise ValueError(f"Startindeks skal være et heltal mellem 1 og {len(starts)}.")
    ordered = sorted(starts, key=lambda item: (item.delay, item.boat.competitor))
    reference_delay = ordered[start_index - 1].delay
    style = ParagraphStyle("Cell", fontName="Helvetica", fontSize=10, leading=12)
    header = ParagraphStyle("Header", parent=style, fontName="Helvetica-Bold")
    rows = [
        [
            Paragraph("Starttid", header),
            Paragraph("Båd/Deltager", header),
            Paragraph("Bådtype", header),
        ]
    ]
    for start in ordered:
        if start.delay < 0:
            raise ValueError("Starttillæg må ikke være negativt.")
        time = first_start + timedelta(seconds=start.delay - reference_delay)
        days = (time.date() - first_start.date()).days
        clock = time.strftime("%H:%M:%S") + (f" ({days:+d} d)" if days else "")
        label = " / ".join(
            value for value in (start.boat.name, start.boat.competitor) if value
        )
        rows.append(
            [
                Paragraph(clock, style),
                Paragraph(escape(label), style),
                Paragraph(escape(start.boat.boat_type), style),
            ]
        )
    table = Table(rows, colWidths=[30 * mm, 113 * mm, 37 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
            ]
        )
    )
    title_style = ParagraphStyle(
        "RaceTitle", fontName="Helvetica-Bold", fontSize=18, leading=22
    )
    date_line = first_start.strftime("%d-%m-%Y")
    length = course.distance_nm if course is not None else distance_nm
    if length is not None:
        length_text = f"{positive_number(length):.2f}".replace(".", ",")
        date_line += f" · Banelængde: {length_text} sømil"
    story = [
        Paragraph(escape(title), title_style),
        Paragraph(date_line, style),
        Spacer(1, 3 * mm),
    ]
    if course is not None:
        sides = rounding_sides(course, initial_side)
        letters = " ".join(
            f'<font color="{"#008000" if side == "starboard" else "#d00000"}">{escape(mark.name)}</font>'
            for mark, side in zip(course.marks[1:-1], sides)
        )
        route_style = ParagraphStyle(
            "Route", fontName="Helvetica-Bold", fontSize=20, leading=24
        )
        story.extend([Paragraph("Bane: " + letters, route_style), Spacer(1, 6 * mm)])
    story.append(table)
    path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=title,
    ).build(story)
