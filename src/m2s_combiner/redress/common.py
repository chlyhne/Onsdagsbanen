from __future__ import annotations

import re
import unicodedata

import numpy as np

from .constants import DH_REFERENCE_HDCP


def race_sort_key(label: str) -> int:
    return int(label[1:])


def race_num(label: str) -> int:
    try:
        return int(str(label).replace("R", ""))
    except ValueError:
        return 0


def display_name(value: str) -> str:
    compact = " ".join(str(value).strip().split())
    if not compact:
        return ""
    return compact.title()


def abbreviate_name(value: str) -> str:
    full_name = display_name(value)
    if not full_name:
        return ""
    if normalize_text(full_name) == "blue x":
        return full_name

    parts = full_name.split()
    if len(parts) < 2:
        return full_name

    initials = [f"{part.rstrip('.').strip()[0]}." for part in parts[:-1] if part.rstrip('.').strip()]
    if not initials:
        return full_name
    return " ".join([*initials, parts[-1]])


def normalize_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def slugify_filename(value: str) -> str:
    normalized = normalize_text(value)
    if not normalized:
        return "item"
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-") or "item"


def format_seconds_hms(value: float | int | None) -> str:
    if value is None:
        return ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(numeric):
        return ""
    total = int(round(numeric))
    sign = "-" if total < 0 else ""
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{sign}{hours}:{minutes:02d}:{seconds:02d}"


def format_seconds_signed_compact(value: float | int | None) -> str:
    if value is None:
        return ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(numeric):
        return ""

    rounded = int(round(numeric))
    if rounded == 0:
        return "0:00"

    sign = "+" if rounded > 0 else "-"
    total = abs(rounded)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{sign}{hours}:{minutes:02d}:{seconds:02d}"
    return f"{sign}{minutes}:{seconds:02d}"


def format_rank_error(value: float | int | None) -> str:
    if value is None:
        return ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(numeric):
        return ""
    rounded = int(round(numeric))
    if rounded > 0:
        return f"+{rounded}"
    return str(rounded)


def latex_escape_text(value: str) -> str:
    text = str(value)
    replacements = {
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in text)


def latex_table_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    if text.lstrip().startswith(r"\cellcolor"):
        return text
    return latex_escape_text(text)


def normalize_sail_number(sail_number: object, sail_country: object) -> str:
    base = f"{str(sail_country or '').strip()} {str(sail_number or '').strip()}".strip()
    return re.sub(r"[^A-Za-z0-9]+", "", base).upper()


def sail_digits(sail: str) -> str:
    return "".join(ch for ch in sail if ch.isdigit())


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    from difflib import SequenceMatcher

    return float(SequenceMatcher(None, a, b).ratio())


def is_blue_x(*values: object) -> bool:
    joined = "".join(normalize_text(value).replace(" ", "") for value in values)
    return "bluex" in joined


def class_sort_key(value: str) -> tuple[int, str]:
    match = re.search(r"(\d+)\s*$", value.strip())
    idx = int(match.group(1)) if match else 0
    return idx, value.lower()


def predict_sailed_seconds_from_corrected(
    corrected_seconds: float | int | None,
    hdcp: float | int | None,
    length_nm: float | int | None,
) -> float | None:
    if corrected_seconds is None or hdcp is None or length_nm is None:
        return None
    try:
        corrected_value = float(corrected_seconds)
        hdcp_value = float(hdcp)
        distance_nm = float(length_nm)
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(corrected_value) and np.isfinite(hdcp_value) and np.isfinite(distance_nm)):
        return None
    if distance_nm <= 0.0:
        return None
    sailed_seconds = corrected_value - (DH_REFERENCE_HDCP - hdcp_value) * distance_nm
    if not np.isfinite(sailed_seconds):
        return None
    return float(sailed_seconds)
