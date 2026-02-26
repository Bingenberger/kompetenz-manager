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


def get_prioritized_students_for_user(user):
    """Return students ordered by teaching relevance, then alphabetically."""
    kontext = get_user_klassenkontext(user)
    klassenleitung = kontext["klassenleitung"]
    fachklassen = kontext["fachklassen"]

    schueler_liste = Schueler.query.all()
    schueler_liste.sort(
        key=lambda s: (
            _priority_for_student(s, klassenleitung, fachklassen),
            (s.klasse or "").lower(),
            (s.nachname or "").lower(),
            (s.vorname or "").lower(),
        )
    )
    return schueler_liste


def get_grouped_student_choices_for_user(user):
    kontext = get_user_klassenkontext(user)
    klassenleitung = kontext["klassenleitung"]
    fachklassen = kontext["fachklassen"]

    alle = get_prioritized_students_for_user(user)

    own = []
    fach = []
    other = []

    for s in alle:
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

    if not groups:
        groups = [{"key": "all", "label": "Alle Kinder", "students": alle}]

    return groups


def get_distinct_klassen():
    rows = (
        Schueler.query.with_entities(Schueler.klasse)
        .filter(Schueler.klasse.isnot(None))
        .distinct()
        .all()
    )
    return sorted([r[0] for r in rows if (r[0] or "").strip()], key=lambda x: x.lower())
