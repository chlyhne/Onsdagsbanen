"""Stable application-service exports. Implementations live in services/."""
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from omvendt_respit.course import read_marks
from .domain import RATINGS, PENALTIES, correction, daily, number, overall, rounded, gph, rating_value
ROOT=Path(__file__).resolve().parents[2]
TZ=ZoneInfo('Europe/Copenhagen')
from .services.common import instant, geometry, extra_handicap_value, rounding_conflicts
from .services.series import create_series, update_series, series_reference
from .services.boats import save_boat, recalculate_boat
from .services.participation import boats, rows, replace_roster, create_race
from .services.timing import editable, invalidate_redress, changed, capture, usable_capture, finish, undo, publish
from .services.results import result_view
from .services.corrections import set_reference, day_settings
