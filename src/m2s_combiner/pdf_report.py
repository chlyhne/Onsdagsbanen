from __future__ import annotations

import colorsys
from datetime import datetime
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import pandas as pd
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.pagesizes import A4
from reportlab.lib.pagesizes import landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Flowable
from reportlab.platypus import PageBreak
from reportlab.platypus import Paragraph
from reportlab.platypus import SimpleDocTemplate
from reportlab.platypus import Spacer
from reportlab.platypus import Table
from reportlab.platypus import TableStyle

from .combine import HIGH_POINT_RULE
from .combine import LOW_POINT_RULE


_DANISH_TZ = ZoneInfo("Europe/Copenhagen")
_REPORT_CREDIT = "hummesse@gmail.com"


def _danish_now() -> datetime:
    return datetime.now(_DANISH_TZ)


def _title_case_words(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return " ".join(word[:1].upper() + word[1:].lower() for word in text.split(" "))


def _table_data(frame: pd.DataFrame) -> list[list[object]]:
    header = list(frame.columns)
    rows = frame.values.tolist()
    return [header] + rows


def _race_sort_key(label: str) -> tuple[int, str]:
    match = re.fullmatch(r"R(\d+)", str(label).strip(), flags=re.IGNORECASE)
    if not match:
        return (10**9, str(label))
    return (int(match.group(1)), str(label))


def _shrink_styles_by_one_point(styles) -> None:
    for style in styles.byName.values():
        font_size = getattr(style, "fontSize", None)
        if isinstance(font_size, (int, float)) and font_size > 1:
            style.fontSize = font_size - 1

        leading = getattr(style, "leading", None)
        if isinstance(leading, (int, float)):
            target = getattr(style, "fontSize", leading)
            style.leading = max(target + 1, leading - 1)


def _styled_table(
    frame: pd.DataFrame,
    center_columns: set[str] | None = None,
    font_size: float | None = None,
    header_bg_color: str = "#1f3a5f",
    row_bg_colors: tuple[str, str] = ("#f5f5f5", "#edf3f8"),
) -> Table:
    table = Table(_table_data(frame), repeatRows=1)
    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg_color)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#a0a0a0")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor(row_bg_colors[0]), colors.HexColor(row_bg_colors[1])]),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]

    if font_size is not None:
        style_commands.append(("FONTSIZE", (0, 0), (-1, -1), float(font_size)))
        style_commands.append(("LEADING", (0, 0), (-1, -1), float(font_size) + 1))

    center_columns = center_columns or set()
    for index, column in enumerate(frame.columns):
        if str(column) in center_columns:
            style_commands.append(("ALIGN", (index, 0), (index, -1), "CENTER"))

    table.setStyle(TableStyle(style_commands))
    return table


def _int_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(int(value))


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    r, g, b = rgb
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


def _theme_for_group_index(group_index: int) -> dict[str, object]:
    # Golden-angle stepping keeps adjacent group colors visibly distinct.
    hue = (0.58 + group_index * 0.6180339887498949) % 1.0

    header_bg_color = _rgb_to_hex(colorsys.hsv_to_rgb(hue, 0.62, 0.58))
    row_bg_1 = _rgb_to_hex(colorsys.hsv_to_rgb(hue, 0.10, 0.98))
    row_bg_2 = _rgb_to_hex(colorsys.hsv_to_rgb(hue, 0.18, 0.94))
    title_color = _rgb_to_hex(colorsys.hsv_to_rgb(hue, 0.75, 0.46))

    return {
        "header_bg_color": header_bg_color,
        "row_bg_colors": (row_bg_1, row_bg_2),
        "title_color": title_color,
    }


class _MathPoint(Flowable):
    """Small flowable for math-style point text with optional LaTeX-like cancel slash."""

    def __init__(self, value: str, canceled: bool, font_size: float, bold: bool = False):
        super().__init__()
        self.value = value
        self.canceled = canceled
        self.bold = bold
        # Slide-like math styling tends to read better in sans oblique than book-style serif italics.
        self.font_name = "Helvetica-BoldOblique" if self.bold else "Helvetica-Oblique"
        self.font_size = max(6.0, float(font_size) + 0.2)
        text_width = pdfmetrics.stringWidth(self.value, self.font_name, self.font_size)
        self.width = max(8.0, text_width + 4.0)
        self.height = max(self.font_size + 3.0, 9.0)

    def draw(self) -> None:
        y_baseline = max(1.0, (self.height - self.font_size) / 2.0)
        self.canv.setFont(self.font_name, self.font_size)
        self.canv.setFillColor(colors.black)
        self.canv.drawCentredString(self.width / 2.0, y_baseline, self.value)

        if self.canceled:
            # Diagonal slash to mimic LaTeX \cancel{...} style.
            self.canv.setStrokeColor(colors.HexColor("#B00020"))
            self.canv.setLineWidth(0.9)
            self.canv.line(1.0, 1.0, self.width - 1.0, self.height - 1.0)


def _is_point_column(column_name: str) -> bool:
    return column_name in {"Point", "pts"} or bool(re.fullmatch(r"R\d+", str(column_name)))


def _is_time_column(column_name: str) -> bool:
    return column_name in {
        "Sejlet tid",
        "Beregnet tid",
        "Forskel",
        "Interval",
        "Sailed Time",
        "Calculated Time",
        "Delta",
        "interval",
    }


def _looks_like_time_text(value: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", value))


def _mathify_point_columns(
    frame: pd.DataFrame,
    discard_map: dict[str, list[str]] | None,
    font_size: float,
) -> pd.DataFrame:
    printable = frame.astype(object)
    if "Deltager" not in printable.columns:
        return printable

    for row_index, row in printable.iterrows():
        competitor = str(row.get("Deltager", ""))
        canceled_labels = set((discard_map or {}).get(competitor, []))

        for column in printable.columns:
            if not _is_point_column(str(column)):
                continue

            value = row[column]
            text = str(value).strip()
            if text == "":
                continue

            canceled = column in canceled_labels
            is_pts_value = str(column) in {"Point", "pts"}
            printable.at[row_index, column] = _MathPoint(
                text,
                canceled=canceled,
                font_size=font_size,
                bold=is_pts_value,
            )

    return printable


def _mathify_time_columns(frame: pd.DataFrame, font_size: float) -> pd.DataFrame:
    printable = frame.astype(object)

    for row_index, row in printable.iterrows():
        for column in printable.columns:
            if not _is_time_column(str(column)):
                continue

            value = row[column]
            text = str(value).strip()
            if text == "" or not _looks_like_time_text(text):
                continue

            printable.at[row_index, column] = _MathPoint(text, canceled=False, font_size=font_size)

    return printable


def _scoring_explanation_lines(scoring_rule: str) -> list[str]:
    if scoring_rule == HIGH_POINT_RULE:
        return [
            "Valgt system: High-point.",
            "Både med status DNS, DNC eller DSQ gives 0 point i den sejlads.",
            "Deltagelse giver 1 point, og fuldførte både får derudover 1 point pr. båd de ligger foran.",
            "Samlet stilling sorteres faldende efter point (flest point er bedst).",
        ]

    return [
        "Valgt system: Low-point.",
        "Fuldførte både får point svarende til placering i sejladsen (1 for nr. 1, 2 for nr. 2 osv.).",
        "Både uden fuldført tid får strafpoint i forhold til antal både i feltet.",
        "Samlet stilling sorteres stigende efter point (færrest point er bedst).",
    ]


def _race_page_meta_line(race_meta: dict[str, str] | None) -> str:
    if not isinstance(race_meta, dict):
        race_meta = {}

    wind_category = str(race_meta.get("wind_category_da") or "ukendt").strip() or "ukendt"
    course_length = str(race_meta.get("course_length") or "ukendt").strip() or "ukendt"
    start_time = str(race_meta.get("start_time") or "ukendt").strip() or "ukendt"
    return f"Vindkategori: {wind_category} | Banelængde: {course_length} | Starttid: {start_time}"


def _overall_discard_meta_line(discard_after: object) -> str:
    if isinstance(discard_after, list):
        values = sorted({int(value) for value in discard_after if isinstance(value, int) and value > 0})
    else:
        values = []

    if not values:
        return "Fratrækkere efter sejladser: ingen"

    labels = ", ".join(f"R{value}" for value in values)
    return f"Fratrækkere efter sejladser: {labels}"


def build_combined_pdf(
    output_path: Path,
    sections: list[dict[str, object]],
    source_urls: list[str],
    scoring_rule: str = LOW_POINT_RULE,
) -> None:
    """Build PDF with race and overall pages for each class group section."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        leftMargin=4 * mm,
        rightMargin=4 * mm,
        topMargin=4 * mm,
        bottomMargin=4 * mm,
    )
    styles = getSampleStyleSheet()
    _shrink_styles_by_one_point(styles)
    table_font_size = float(styles["BodyText"].fontSize)
    cover_text_style = ParagraphStyle(
        "CoverText",
        parent=styles["BodyText"],
        fontSize=styles["BodyText"].fontSize + 1,
        leading=styles["BodyText"].leading + 2,
    )
    cover_heading_style = ParagraphStyle(
        "CoverHeading",
        parent=styles["Heading3"],
        fontSize=styles["Heading3"].fontSize + 0.5,
        leading=styles["Heading3"].leading + 1,
    )

    story = []
    generated_at = _danish_now().strftime("%d-%m-%Y %H:%M")
    group_theme_map: dict[str, dict[str, object]] = {}

    story.append(Paragraph("Kaløvig Onsdagsbanen 2026", styles["Title"]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("Kombinerede resultater", styles["Heading2"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(f"Genereret: {generated_at}", cover_text_style))
    story.append(Spacer(1, 2 * mm))
    story.append(
        Paragraph(
            "De kombinerede resultater udsendes af Casper Lyhne for Kaløvig Onsdagsbanen 2026.",
            cover_text_style,
        )
    )
    story.append(Paragraph(f"<b>Kontakt:</b> {_REPORT_CREDIT}", cover_text_style))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Sådan får du resultater", cover_heading_style))
    story.append(Paragraph("Tilmelding: Send mail til hummesse@gmail.com med emnet <b>resultater</b>.", cover_text_style))
    story.append(
        Paragraph(
            "Afmelding: Send mail fra adressen der skal afmeldes med emnet <b>afmeld resultater</b>.",
            cover_text_style,
        )
    )
    story.append(Paragraph("Når først du er tilmeldt vil du modtage nye resultater hver gang der er nye", cover_text_style))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Pointsystem", cover_heading_style))
    for line in _scoring_explanation_lines(scoring_rule):
        story.append(Paragraph(line, cover_text_style))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Hvordan resultaterne laves", cover_heading_style))
    story.append(
        Paragraph(
            "Når en mail modtages afføder det en kørsel af et program, som henter data fra manage 2 sail. Løbene kombineres, point beregnes, og PDF genereres og udsendes.",
            cover_text_style,
        )
    )
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Formål med de kombinerede resultater", cover_heading_style))
    story.append(
        Paragraph(
            "Formålet er at skabe en samlet stilling for stor bane og lille bane henholdsvist, "
            "så deltagene kan sammenligne sig med både i et andet løb, som har sejlet den samme bane på samme tidspunkt. Dette understøttes ikke af manage2sail p.t.",
            cover_text_style,
        )
    )
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Datakilde", cover_heading_style))
    story.append(Paragraph("Officielle Manage2Sail-data for Kaløvig Onsdagsbanen 2026.", cover_text_style))
    story.append(PageBreak())

    for section in sections:
        group_label = str(section.get("group_label", "Kombineret"))
        if group_label not in group_theme_map:
            group_theme_map[group_label] = _theme_for_group_index(len(group_theme_map))

    for section_index, section in enumerate(sections):
        group_label = str(section.get("group_label", "Kombineret"))
        theme = group_theme_map[group_label]
        combined_races = section.get("combined_races")
        combined_overall = section.get("combined_overall")
        race_page_meta_raw = section.get("race_page_meta")
        race_warnings_raw = section.get("race_warnings")

        if not isinstance(combined_races, pd.DataFrame):
            combined_races = pd.DataFrame()
        if not isinstance(combined_overall, pd.DataFrame):
            combined_overall = pd.DataFrame()
        race_warnings: dict[str, list[str]] = {}
        race_page_meta: dict[str, dict[str, str]] = {}
        if isinstance(race_warnings_raw, dict):
            for race_label, messages in race_warnings_raw.items():
                if not isinstance(race_label, str):
                    continue
                if not isinstance(messages, list):
                    continue
                normalized_messages = [str(message).strip() for message in messages if str(message).strip()]
                if normalized_messages:
                    race_warnings[race_label] = normalized_messages

        if isinstance(race_page_meta_raw, dict):
            for race_label, values in race_page_meta_raw.items():
                if not isinstance(race_label, str):
                    continue
                if not isinstance(values, dict):
                    continue
                race_page_meta[race_label] = {
                    "wind_category_da": str(values.get("wind_category_da") or "").strip(),
                    "course_length": str(values.get("course_length") or "").strip(),
                    "start_time": str(values.get("start_time") or "").strip(),
                }

        race_labels: list[str] = []
        if not combined_races.empty and "race" in combined_races.columns:
            race_labels = sorted(combined_races["race"].dropna().astype(str).unique().tolist(), key=_race_sort_key)

        for race_index, race_label in enumerate(race_labels):
            race_rows = combined_races[combined_races["race"] == race_label].copy()
            race_rows = race_rows.sort_values(["race_rank", "competitor"], ascending=[True, True]).reset_index(drop=True)

            preferred_columns = [
                "points",
                "competitor",
                "boat_name",
                "boat_type",
                "hdcp",
                "sailed_time",
                "beregnet_time",
                "delta",
                "interval",
            ]
            available_columns = [column for column in preferred_columns if column in race_rows.columns]
            printable_race = race_rows[available_columns].copy()
            if "points" in printable_race.columns:
                printable_race["points"] = printable_race["points"].map(_int_text)
            printable_race = printable_race.rename(
                columns={
                    "points": "Point",
                    "competitor": "Deltager",
                    "boat_name": "Bådnavn",
                    "boat_type": "Bådtype",
                    "hdcp": "Handicap",
                    "sailed_time": "Sejlet tid",
                    "beregnet_time": "Beregnet tid",
                    "delta": "Forskel",
                    "interval": "Interval",
                }
            )
            if "Deltager" in printable_race.columns:
                printable_race["Deltager"] = printable_race["Deltager"].map(_title_case_words)
            if "Bådtype" in printable_race.columns:
                printable_race["Bådtype"] = printable_race["Bådtype"].map(_title_case_words)
            race_center_columns = {
                column
                for column in printable_race.columns
                if column
                in {"Handicap", "Sejlet tid", "Beregnet tid", "Forskel", "Interval", "Point"}
                or re.fullmatch(r"R\d+", str(column))
            }
            printable_race = _mathify_time_columns(printable_race, font_size=table_font_size)

            story.append(
                Paragraph(
                    f"<font color='{theme['title_color']}'>Kombineret resultat: {group_label} - {race_label}</font>",
                    styles["Title"],
                )
            )
            story.append(Spacer(1, 3 * mm))
            story.append(Paragraph(f"Genereret: {generated_at}", styles["Normal"]))
            story.append(Spacer(1, 1.5 * mm))
            story.append(Paragraph(_race_page_meta_line(race_page_meta.get(str(race_label))), styles["BodyText"]))
            story.append(Spacer(1, 3 * mm))
            story.append(
                _styled_table(
                    printable_race,
                    center_columns=race_center_columns,
                    font_size=table_font_size,
                    header_bg_color=str(theme["header_bg_color"]),
                    row_bg_colors=tuple(theme["row_bg_colors"]),
                )
            )

            warnings_for_race = race_warnings.get(str(race_label), [])
            if warnings_for_race:
                story.append(Spacer(1, 2 * mm))
                for warning_text in warnings_for_race:
                    story.append(Paragraph(f"<font color='#8B0000'>{warning_text}</font>", styles["BodyText"]))

            if race_index < len(race_labels) - 1 or not combined_overall.empty:
                story.append(PageBreak())

        story.append(
            Paragraph(
                f"<font color='{theme['title_color']}'>Samlet kombineret stilling: {group_label}</font>",
                styles["Title"],
            )
        )
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(f"Genereret: {generated_at}", styles["Normal"]))
        story.append(Spacer(1, 1.5 * mm))
        story.append(Paragraph(_overall_discard_meta_line(combined_overall.attrs.get("discard_after")), styles["BodyText"]))
        story.append(Spacer(1, 4 * mm))

        overall_printable = combined_overall.copy()
        discard_map = combined_overall.attrs.get("discarded_races_by_competitor", {})

        if "race_count" in overall_printable.columns:
            overall_printable = overall_printable.drop(columns=["race_count"])

        rename_map = {
            "combined_rank": "Plac.",
            "competitor": "Deltager",
            "boat_type": "Bådtype",
            "combined_points": "Point",
        }
        overall_printable = overall_printable.rename(columns=rename_map)

        ordered_columns = ["Plac.", "Deltager"]
        if "Bådtype" in overall_printable.columns:
            ordered_columns.append("Bådtype")
        ordered_columns.extend([f"R{race_number}" for race_number in range(1, 19) if f"R{race_number}" in overall_printable.columns])
        if "Point" in overall_printable.columns:
            ordered_columns.append("Point")
        overall_printable = overall_printable[[column for column in ordered_columns if column in overall_printable.columns]]

        if "Point" in overall_printable.columns:
            overall_printable["Point"] = overall_printable["Point"].map(_int_text)

        if "Plac." in overall_printable.columns:
            overall_printable["Plac."] = overall_printable["Plac."].map(_int_text)

        for column in overall_printable.columns:
            if re.fullmatch(r"R\d+", str(column)):
                overall_printable[column] = overall_printable[column].map(_int_text)

        overall_printable = _mathify_point_columns(overall_printable, discard_map=discard_map, font_size=table_font_size)
        overall_printable = _mathify_time_columns(overall_printable, font_size=table_font_size)
        if "Deltager" in overall_printable.columns:
            overall_printable["Deltager"] = overall_printable["Deltager"].map(_title_case_words)
        if "Bådtype" in overall_printable.columns:
            overall_printable["Bådtype"] = overall_printable["Bådtype"].map(_title_case_words)

        overall_center_columns = {
            column
            for column in overall_printable.columns
            if column in {"Plac.", "Point"} or re.fullmatch(r"R\d+", str(column))
        }
        story.append(
            _styled_table(
                overall_printable,
                center_columns=overall_center_columns,
                font_size=table_font_size,
                header_bg_color=str(theme["header_bg_color"]),
                row_bg_colors=tuple(theme["row_bg_colors"]),
            )
        )

        if section_index < len(sections) - 1:
            story.append(PageBreak())

    if source_urls:
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("Kilder:", styles["Heading3"]))
        for url in source_urls:
            story.append(Paragraph(url, styles["BodyText"]))

    doc.build(story)
