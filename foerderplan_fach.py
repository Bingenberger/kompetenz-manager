"""Faecher eines Foerderplans.

Ein Plan kann mehrere Faecher betreffen - etwa Ziele aus dem Deutsch- und dem
Mathebogen in einem Plan. Die Faecher ergeben sich aus den Boegen der
gewaehlten Kompetenzen (Bogen -> Fach) und lassen sich im Assistenten von Hand
ergaenzen. Daran haengt die Regel aus foerderkurs.py: Wer im Foerderkurs
Deutsch ist, braucht einen aktiven Foerderplan, der das Fach Deutsch umfasst.
"""

from models import db, Bogen, Fach, Foerderplan, Item, foerderplan_fach


def fach_ids_aus_items(item_ids):
    """Faecher der Boegen, aus denen die gewaehlten Kompetenzen stammen."""
    ids = {int(wert) for wert in item_ids if wert}
    if not ids:
        return set()
    treffer = (
        db.session.query(Item.bogen_id)
        .filter(Item.id.in_(ids))
        .all()
    )
    bogen_ids = {zeile[0] for zeile in treffer if zeile[0]}
    if not bogen_ids:
        return set()
    faecher = (
        db.session.query(Bogen.fach_id)
        .filter(Bogen.id.in_(bogen_ids), Bogen.fach_id.isnot(None))
        .all()
    )
    return {zeile[0] for zeile in faecher}


def setze_faecher(plan, fach_ids):
    """Faecher des Plans genau auf diese Auswahl setzen."""
    ids = {int(wert) for wert in fach_ids if wert}
    plan.faecher = Fach.query.filter(Fach.id.in_(ids)).all() if ids else []
    return plan.faecher


def aktualisiere(plan, manuelle_ids=(), item_ids=()):
    """Faecher aus der Auswahl im Assistenten und den Boegen der Ziele.

    Die Handauswahl bleibt erhalten, die Boegen ergaenzen sie - so wird ein
    Plan mit einem Ziel aus dem Deutschbogen automatisch zum Deutschplan,
    ohne dass eine bewusste Zuordnung verloren geht.
    """
    ids = {int(wert) for wert in manuelle_ids if wert}
    ids |= fach_ids_aus_items(item_ids)
    return setze_faecher(plan, ids)


def plan_query_im_fach(fach_id):
    """Basis-Query fuer Plaene, die dieses Fach umfassen."""
    return Foerderplan.query.join(
        foerderplan_fach, foerderplan_fach.c.foerderplan_id == Foerderplan.id
    ).filter(foerderplan_fach.c.fach_id == fach_id)


def uebernimm_altbestand():
    """Einmalige Migration: die alte Spalte foerderplan.fach_id uebernehmen.

    Frueher hatte ein Plan genau ein Fach. Die Werte wandern in die
    Verknuepfungstabelle, danach faellt die Spalte weg. Idempotent: ohne die
    alte Spalte passiert nichts.
    """
    from sqlalchemy import text

    spalte_da = _hat_spalte('foerderplan', 'fach_id')
    if not spalte_da:
        return 0
    zeilen = db.session.execute(text(
        "SELECT id, fach_id FROM foerderplan WHERE fach_id IS NOT NULL"
    )).all()
    uebernommen = 0
    for plan_id, fach_id in zeilen:
        vorhanden = db.session.execute(
            foerderplan_fach.select().where(
                foerderplan_fach.c.foerderplan_id == plan_id,
                foerderplan_fach.c.fach_id == fach_id,
            )
        ).first()
        if vorhanden:
            continue
        db.session.execute(foerderplan_fach.insert().values(
            foerderplan_id=plan_id, fach_id=fach_id))
        uebernommen += 1
    db.session.commit()
    with db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE foerderplan DROP COLUMN fach_id"))
    return uebernommen


def _hat_spalte(tabelle, spalte):
    from sqlalchemy import inspect

    pruefer = inspect(db.engine)
    if tabelle not in pruefer.get_table_names():
        return False
    return spalte in {eintrag['name'] for eintrag in pruefer.get_columns(tabelle)}
