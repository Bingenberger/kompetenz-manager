from models import Schueler, UserKlassenzuordnung


def get_user_klassenkontext(user):
    if not user or not getattr(user, "id", None):
        return {"klassenleitung": None, "fachklassen": set()}

    zuordnungen = UserKlassenzuordnung.query.filter_by(user_id=user.id).all()
    klassenleitung = None
    fachklassen = set()

    for z in zuordnungen:
        if z.rolle == "klassenleitung" and not klassenleitung:
            klassenleitung = z.klasse
        elif z.rolle == "fach":
            fachklassen.add(z.klasse)

    if klassenleitung in fachklassen:
        fachklassen.remove(klassenleitung)

    return {
        "klassenleitung": klassenleitung,
        "fachklassen": fachklassen,
    }


def _priority_for_student(schueler, klassenleitung, fachklassen):
    if klassenleitung and schueler.klasse == klassenleitung:
        return 0
    if schueler.klasse in fachklassen:
        return 1
    return 2


def get_prioritized_students_for_user(user, include_archived=False):
    """Return students ordered by teaching relevance, then alphabetically."""
    kontext = get_user_klassenkontext(user)
    klassenleitung = kontext["klassenleitung"]
    fachklassen = kontext["fachklassen"]

    query = Schueler.query
    if not include_archived:
        query = query.filter(Schueler.is_active.is_(True))
    schueler_liste = query.all()
    schueler_liste.sort(
        key=lambda s: (
            _priority_for_student(s, klassenleitung, fachklassen),
            (s.klasse or "").lower(),
            (s.nachname or "").lower(),
            (s.vorname or "").lower(),
        )
    )
    return schueler_liste


def get_grouped_student_choices_for_user(user, include_archived=False):
    kontext = get_user_klassenkontext(user)
    klassenleitung = kontext["klassenleitung"]
    fachklassen = kontext["fachklassen"]

    alle = get_prioritized_students_for_user(user, include_archived=include_archived)

    own = []
    archived = []
    fach = []
    other = []

    for s in alle:
        if not s.is_active:
            archived.append(s)
            continue
        prio = _priority_for_student(s, klassenleitung, fachklassen)
        if prio == 0:
            own.append(s)
        elif prio == 1:
            fach.append(s)
        else:
            other.append(s)

    groups = []
    if klassenleitung and own:
        groups.append({
            "key": "own",
            "label": f"Meine Klasse ({klassenleitung})",
            "students": own,
        })
    if fach:
        fach_klassen_sorted = sorted(fachklassen, key=lambda x: x.lower())
        groups.append({
            "key": "fach",
            "label": f"Fachunterricht ({', '.join(fach_klassen_sorted)})",
            "students": fach,
        })
    if other:
        groups.append({
            "key": "other",
            "label": "Weitere Kinder",
            "students": other,
        })
    if archived:
        groups.append({
            "key": "archive",
            "label": "Archivierte Kinder",
            "students": archived,
        })

    if not groups:
        groups = [{"key": "all", "label": "Alle Kinder", "students": alle}]

    return groups


def get_distinct_klassen():
    rows = (
        Schueler.query.with_entities(Schueler.klasse)
        .filter(Schueler.klasse.isnot(None), Schueler.is_active.is_(True))
        .distinct()
        .all()
    )
    return sorted([r[0] for r in rows if (r[0] or "").strip()], key=lambda x: x.lower())


def get_tabbed_student_selection_for_user(user, selected_s_id=None, requested_tab=None, auto_select_first=False, include_archived=False):
    students = get_prioritized_students_for_user(user, include_archived=include_archived)
    groups = get_grouped_student_choices_for_user(user, include_archived=include_archived)
    kontext = get_user_klassenkontext(user)

    class_to_students = {}
    for student in students:
        if not student.is_active:
            continue
        key = (student.klasse or "").strip()
        class_to_students.setdefault(key, []).append(student)

    own_class = kontext.get("klassenleitung")
    fach_classes = sorted(kontext.get("fachklassen") or set(), key=lambda x: x.lower())

    tab_definitions = []
    if own_class:
        tab_definitions.append({
            "id": "own",
            "label": f"Meine Klasse ({own_class})",
            "students": class_to_students.get(own_class, []),
            "kind": "class",
            "class_name": own_class,
        })
    for fach_class in fach_classes:
        tab_definitions.append({
            "id": f"fach-{fach_class}",
            "label": f"Fachunterricht ({fach_class})",
            "students": class_to_students.get(fach_class, []),
            "kind": "class",
            "class_name": fach_class,
        })
    archived_students = [student for student in students if not student.is_active]
    if include_archived and archived_students:
        tab_definitions.append({
            "id": "archive",
            "label": f"Archiv ({len(archived_students)})",
            "students": archived_students,
            "kind": "archive",
            "class_name": None,
        })
    tab_definitions.append({
        "id": "dropdown",
        "label": "Auswahl (Dropdown)",
        "students": students,
        "kind": "dropdown",
        "class_name": None,
    })

    has_assigned_class_tabs = any(tab["kind"] == "class" for tab in tab_definitions)
    if not has_assigned_class_tabs:
        tab_definitions = [tab for tab in tab_definitions if tab["id"] in {"archive", "dropdown"}]

    requested_tab = (requested_tab or "").strip()
    valid_tab_ids = {tab["id"] for tab in tab_definitions}
    active_tab = requested_tab if requested_tab in valid_tab_ids else (tab_definitions[0]["id"] if tab_definitions else "dropdown")

    selected_student = None
    selected_s_id = (selected_s_id or "").strip()
    if selected_s_id:
        try:
            s_id_int = int(selected_s_id)
            selected_student = next((s for s in students if s.id == s_id_int), None)
            if not selected_student:
                selected_s_id = ""
        except (TypeError, ValueError):
            selected_s_id = ""

    if selected_student and not requested_tab:
        if not selected_student.is_active and "archive" in valid_tab_ids:
            active_tab = "archive"
        elif own_class and selected_student.klasse == own_class and "own" in valid_tab_ids:
            active_tab = "own"
        elif selected_student.klasse and f"fach-{selected_student.klasse}" in valid_tab_ids:
            active_tab = f"fach-{selected_student.klasse}"
        else:
            active_tab = "dropdown"

    active_tab_def = next((tab for tab in tab_definitions if tab["id"] == active_tab), None)
    if active_tab_def and active_tab_def["kind"] in {"class", "archive"}:
        allowed_ids = {s.id for s in active_tab_def["students"]}
        if selected_student and selected_student.id not in allowed_ids:
            selected_student = None
            selected_s_id = ""

    if auto_select_first and not selected_student:
        if active_tab_def and active_tab_def["kind"] in {"class", "archive"} and active_tab_def["students"]:
            selected_student = active_tab_def["students"][0]
            selected_s_id = str(selected_student.id)
        elif students:
            selected_student = students[0]
            selected_s_id = str(selected_student.id)

    return {
        "students": students,
        "groups": groups,
        "tab_definitions": tab_definitions,
        "active_tab": active_tab,
        "active_tab_def": active_tab_def,
        "selected_student": selected_student,
        "selected_s_id": selected_s_id,
        "has_assigned_class_tabs": has_assigned_class_tabs,
    }
