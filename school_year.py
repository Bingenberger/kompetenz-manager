import json
import re
from datetime import date, datetime, time

from models import SystemKonfiguration


SCHOOL_YEAR_RE = re.compile(r"^(\d{4})\s*/\s*(\d{4})$")


def normalize_school_year(value):
    match = SCHOOL_YEAR_RE.match((value or '').strip())
    if not match:
        return None
    first, second = int(match.group(1)), int(match.group(2))
    if second != first + 1:
        return None
    return f'{first}/{second}'


def next_school_year(value, today=None):
    normalized = normalize_school_year(value)
    if normalized:
        first = int(normalized[:4]) + 1
        return f'{first}/{first + 1}'
    today = today or date.today()
    first = today.year if today.month >= 7 else today.year - 1
    return f'{first + 1}/{first + 2}'


def default_school_year_start(value):
    normalized = normalize_school_year(value)
    return date(int(normalized[:4]), 8, 1) if normalized else None


def active_school_year_start():
    config = SystemKonfiguration.query.first()
    return config.schuljahr_beginn if config else None


def observation_period_start(candidate=None):
    school_year_start = active_school_year_start()
    boundary = datetime.combine(school_year_start, time.min) if school_year_start else None
    if candidate and boundary:
        return max(candidate, boundary)
    return boundary or candidate

def serialize_ids(values):
    return json.dumps(sorted(set(values)))
