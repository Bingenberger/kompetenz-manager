import json
import re
from datetime import date, datetime, time

from models import SystemKonfiguration


SCHOOL_YEAR_RE = re.compile(r"^(\d{4})\s*/\s*(\d{4})$")
CLASS_RE = re.compile(r"^([1-4])(.*)$")


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


def class_grade(class_name):
    match = CLASS_RE.match((class_name or "").strip())
    return int(match.group(1)) if match else None


def target_classes_after_transition(students):
    targets = set()
    for student in students:
        value = (student.klasse or "").strip()
        grade = class_grade(value)
        if grade:
            targets.add(value)
        promoted = promoted_class_name(value)
        if promoted:
            targets.add(promoted)
    return sorted(targets, key=lambda value: (class_grade(value) or 99, value.lower()))


def promoted_class_name(class_name):
    value = (class_name or '').strip()
    match = CLASS_RE.match(value)
    if not match:
        return None
    grade = int(match.group(1))
    if grade >= 4:
        return None
    return f'{grade + 1}{match.group(2)}'


def student_transition_action(student, repeater_ids, individual_targets=None):
    if student.id in repeater_ids:
        target = (individual_targets or {}).get(student.id)
        return 'individual', target
    value = (student.klasse or '').strip()
    match = CLASS_RE.match(value)
    if not match:
        return 'unchanged', student.klasse
    if int(match.group(1)) == 4:
        return 'archive', student.klasse
    return 'promote', promoted_class_name(value)


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
