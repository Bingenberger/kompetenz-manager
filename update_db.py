from sqlalchemy import text

from app import app, db


def _sqlite_add_missing_user_name_columns():
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.connect() as conn:
        user_exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user'")
        ).first()
        if not user_exists:
            return

        columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(user)")).mappings().all()
        }

        if "vorname" not in columns:
            conn.execute(text("ALTER TABLE user ADD COLUMN vorname VARCHAR(100)"))
            print("Spalte 'user.vorname' wurde ergänzt.")

        if "nachname" not in columns:
            conn.execute(text("ALTER TABLE user ADD COLUMN nachname VARCHAR(100)"))
            print("Spalte 'user.nachname' wurde ergänzt.")

        if "role" not in columns:
            conn.execute(text("ALTER TABLE user ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'teacher'"))
            conn.execute(text("UPDATE user SET role = 'admin' WHERE lower(username) = 'admin'"))
            print("Spalte 'user.role' wurde ergänzt.")

        conn.commit()


def _postgres_add_missing_user_role_column():
    engine = db.engine
    if engine.url.get_backend_name() not in {"postgresql", "postgres"}:
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'user'"
            )
        ).first()
        if not table_exists:
            return

        column_exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'user' "
                "AND column_name = 'role'"
            )
        ).first()
        if not column_exists:
            conn.execute(text("ALTER TABLE public.\"user\" ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'teacher'"))
            conn.execute(text("UPDATE public.\"user\" SET role = 'admin' WHERE lower(username) = 'admin'"))
            print("Spalte 'user.role' wurde für PostgreSQL ergänzt.")
        conn.commit()


def _sqlite_add_missing_foerderplan_creator_column():
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='foerderplan'")
        ).first()
        if not table_exists:
            return

        columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(foerderplan)")).mappings().all()
        }
        if "creator_user_id" not in columns:
            conn.execute(text("ALTER TABLE foerderplan ADD COLUMN creator_user_id INTEGER"))
            print("Spalte 'foerderplan.creator_user_id' wurde ergänzt.")
            # Bestehende Pläne ohne Ersteller auf Admin (id=1) zurücksetzen, falls vorhanden.
            conn.execute(
                text(
                    "UPDATE foerderplan SET creator_user_id = 1 "
                    "WHERE creator_user_id IS NULL AND EXISTS (SELECT 1 FROM user WHERE id = 1)"
                )
            )
        conn.commit()


def _sqlite_add_missing_schueler_geburtsdatum_column():
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schueler'")
        ).first()
        if not table_exists:
            return

        columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(schueler)")).mappings().all()
        }
        if "geburtsdatum" not in columns:
            conn.execute(text("ALTER TABLE schueler ADD COLUMN geburtsdatum DATE"))
            print("Spalte 'schueler.geburtsdatum' wurde ergänzt.")
        conn.commit()


def _sqlite_add_missing_workplan_weeks_column():
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_konfiguration'")
        ).first()
        if not table_exists:
            return

        columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(system_konfiguration)")).mappings().all()
        }
        if "workplan_suggestions_weeks" not in columns:
            conn.execute(
                text("ALTER TABLE system_konfiguration ADD COLUMN workplan_suggestions_weeks INTEGER NOT NULL DEFAULT 12")
            )
            print("Spalte 'system_konfiguration.workplan_suggestions_weeks' wurde ergänzt.")
        conn.commit()


def _postgres_add_missing_workplan_weeks_column():
    engine = db.engine
    if engine.url.get_backend_name() not in {"postgresql", "postgres"}:
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'system_konfiguration'"
            )
        ).first()
        if not table_exists:
            return

        column_exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'system_konfiguration' "
                "AND column_name = 'workplan_suggestions_weeks'"
            )
        ).first()
        if not column_exists:
            conn.execute(
                text(
                    "ALTER TABLE public.system_konfiguration "
                    "ADD COLUMN workplan_suggestions_weeks INTEGER NOT NULL DEFAULT 12"
                )
            )
            print("Spalte 'system_konfiguration.workplan_suggestions_weeks' wurde für PostgreSQL ergänzt.")
        conn.commit()


def _sqlite_add_missing_workplan_task_columns():
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_plan_task'")
        ).first()
        if not table_exists:
            return

        columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(work_plan_task)")).mappings().all()
        }
        if "learning_area" not in columns:
            conn.execute(text("ALTER TABLE work_plan_task ADD COLUMN learning_area VARCHAR(50)"))
            print("Spalte 'work_plan_task.learning_area' wurde ergänzt.")
        if "icon_name" not in columns:
            conn.execute(text("ALTER TABLE work_plan_task ADD COLUMN icon_name VARCHAR(50)"))
            print("Spalte 'work_plan_task.icon_name' wurde ergänzt.")
        if "materials" not in columns:
            conn.execute(text("ALTER TABLE work_plan_task ADD COLUMN materials TEXT"))
            print("Spalte 'work_plan_task.materials' wurde ergänzt.")
        if "child_goal" not in columns:
            conn.execute(text("ALTER TABLE work_plan_task ADD COLUMN child_goal TEXT"))
            print("Spalte 'work_plan_task.child_goal' wurde ergänzt.")
        conn.commit()


def _postgres_add_missing_workplan_task_columns():
    engine = db.engine
    if engine.url.get_backend_name() not in {"postgresql", "postgres"}:
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'work_plan_task'"
            )
        ).first()
        if not table_exists:
            return

        existing = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'work_plan_task'"
                )
            ).all()
        }
        if "learning_area" not in existing:
            conn.execute(text("ALTER TABLE public.work_plan_task ADD COLUMN learning_area VARCHAR(50)"))
            print("Spalte 'work_plan_task.learning_area' wurde für PostgreSQL ergänzt.")
        if "icon_name" not in existing:
            conn.execute(text("ALTER TABLE public.work_plan_task ADD COLUMN icon_name VARCHAR(50)"))
            print("Spalte 'work_plan_task.icon_name' wurde für PostgreSQL ergänzt.")
        if "materials" not in existing:
            conn.execute(text("ALTER TABLE public.work_plan_task ADD COLUMN materials TEXT"))
            print("Spalte 'work_plan_task.materials' wurde für PostgreSQL ergänzt.")
        if "child_goal" not in existing:
            conn.execute(text("ALTER TABLE public.work_plan_task ADD COLUMN child_goal TEXT"))
            print("Spalte 'work_plan_task.child_goal' wurde für PostgreSQL ergänzt.")
        conn.commit()


def _sqlite_add_missing_library_template_columns():
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='class_task_template'")
        ).first()
        if not table_exists:
            return

        columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(class_task_template)")).mappings().all()
        }
        if "learning_area" not in columns:
            conn.execute(text("ALTER TABLE class_task_template ADD COLUMN learning_area VARCHAR(50)"))
            print("Spalte 'class_task_template.learning_area' wurde ergänzt.")
        if "icon_name" not in columns:
            conn.execute(text("ALTER TABLE class_task_template ADD COLUMN icon_name VARCHAR(50)"))
            print("Spalte 'class_task_template.icon_name' wurde ergänzt.")
        conn.commit()


def _postgres_add_missing_library_template_columns():
    engine = db.engine
    if engine.url.get_backend_name() not in {"postgresql", "postgres"}:
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'class_task_template'"
            )
        ).first()
        if not table_exists:
            return

        existing = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'class_task_template'"
                )
            ).all()
        }
        if "learning_area" not in existing:
            conn.execute(text("ALTER TABLE public.class_task_template ADD COLUMN learning_area VARCHAR(50)"))
            print("Spalte 'class_task_template.learning_area' wurde für PostgreSQL ergänzt.")
        if "icon_name" not in existing:
            conn.execute(text("ALTER TABLE public.class_task_template ADD COLUMN icon_name VARCHAR(50)"))
            print("Spalte 'class_task_template.icon_name' wurde für PostgreSQL ergänzt.")
        conn.commit()


def _sqlite_add_missing_erziehung_event_columns():
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erziehungs_ereignis'")
        ).first()
        if not table_exists:
            return

        columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(erziehungs_ereignis)")).mappings().all()
        }
        if "consequence_notes" not in columns:
            conn.execute(text("ALTER TABLE erziehungs_ereignis ADD COLUMN consequence_notes TEXT"))
            print("Spalte 'erziehungs_ereignis.consequence_notes' wurde ergänzt.")
        conn.commit()


def _postgres_add_missing_erziehung_event_columns():
    engine = db.engine
    if engine.url.get_backend_name() not in {"postgresql", "postgres"}:
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'erziehungs_ereignis'"
            )
        ).first()
        if not table_exists:
            return

        existing = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'erziehungs_ereignis'"
                )
            ).all()
        }
        if "consequence_notes" not in existing:
            conn.execute(text("ALTER TABLE public.erziehungs_ereignis ADD COLUMN consequence_notes TEXT"))
            print("Spalte 'erziehungs_ereignis.consequence_notes' wurde für PostgreSQL ergänzt.")
        conn.commit()


def _sqlite_add_school_year_columns():
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return
    with engine.connect() as conn:
        student_columns = {
            row["name"] for row in conn.execute(text("PRAGMA table_info(schueler)")).mappings().all()
        }
        if "is_active" not in student_columns:
            conn.execute(text("ALTER TABLE schueler ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"))
            print("Spalte 'schueler.is_active' wurde ergänzt.")
        if "archived_at" not in student_columns:
            conn.execute(text("ALTER TABLE schueler ADD COLUMN archived_at DATETIME"))
            print("Spalte 'schueler.archived_at' wurde ergänzt.")
        config_columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(system_konfiguration)")).mappings().all()
        }
        change_columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(schuljahreswechsel)")).mappings().all()
        }
        if "zuordnungen_versetzt" not in change_columns:
            conn.execute(text("ALTER TABLE schuljahreswechsel ADD COLUMN zuordnungen_versetzt INTEGER NOT NULL DEFAULT 0"))
            print("Spalte schuljahreswechsel.zuordnungen_versetzt wurde ergänzt.")
        if "zuordnungen_entfernt" not in change_columns:
            conn.execute(text("ALTER TABLE schuljahreswechsel ADD COLUMN zuordnungen_entfernt INTEGER NOT NULL DEFAULT 0"))
            print("Spalte schuljahreswechsel.zuordnungen_entfernt wurde ergänzt.")
        if "schuljahr_beginn" not in config_columns:
            conn.execute(text("ALTER TABLE system_konfiguration ADD COLUMN schuljahr_beginn DATE"))
            print("Spalte 'system_konfiguration.schuljahr_beginn' wurde ergänzt.")
        if "aufbewahrung_jahre" not in config_columns:
            conn.execute(text("ALTER TABLE system_konfiguration ADD COLUMN aufbewahrung_jahre INTEGER"))
            print("Spalte 'system_konfiguration.aufbewahrung_jahre' wurde ergänzt.")
        schueler_columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(schueler)")).mappings().all()
        }
        if "jahrgang" not in schueler_columns:
            conn.execute(text("ALTER TABLE schueler ADD COLUMN jahrgang INTEGER"))
            print("Spalte 'schueler.jahrgang' wurde ergänzt.")
        bogen_columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(bogen)")).mappings().all()
        }
        if "pflicht" not in bogen_columns:
            conn.execute(text("ALTER TABLE bogen ADD COLUMN pflicht BOOLEAN NOT NULL DEFAULT 0"))
            print("Spalte 'bogen.pflicht' wurde ergänzt.")
        if "schuljahresuebergreifend" not in bogen_columns:
            conn.execute(text("ALTER TABLE bogen ADD COLUMN schuljahresuebergreifend BOOLEAN NOT NULL DEFAULT 0"))
            print("Spalte 'bogen.schuljahresuebergreifend' wurde ergänzt.")
        conn.commit()


def _postgres_add_school_year_columns():
    engine = db.engine
    if engine.url.get_backend_name() not in {"postgresql", "postgres"}:
        return
    with engine.connect() as conn:
        student_columns = {
            row[0]
            for row in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'schueler'"
            )).all()
        }
        if "is_active" not in student_columns:
            conn.execute(text(
                "ALTER TABLE public.schueler ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE"
            ))
            print("Spalte 'schueler.is_active' wurde für PostgreSQL ergänzt.")
        if "archived_at" not in student_columns:
            conn.execute(text("ALTER TABLE public.schueler ADD COLUMN archived_at TIMESTAMP"))
            print("Spalte 'schueler.archived_at' wurde für PostgreSQL ergänzt.")
        config_columns = {
            row[0]
            for row in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'system_konfiguration'"
            )).all()
        }
        change_columns = {
            row[0]
            for row in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'schuljahreswechsel'"
            )).all()
        }
        if "zuordnungen_versetzt" not in change_columns:
            conn.execute(text("ALTER TABLE public.schuljahreswechsel ADD COLUMN zuordnungen_versetzt INTEGER NOT NULL DEFAULT 0"))
            print("Spalte schuljahreswechsel.zuordnungen_versetzt wurde für PostgreSQL ergänzt.")
        if "zuordnungen_entfernt" not in change_columns:
            conn.execute(text("ALTER TABLE public.schuljahreswechsel ADD COLUMN zuordnungen_entfernt INTEGER NOT NULL DEFAULT 0"))
            print("Spalte schuljahreswechsel.zuordnungen_entfernt wurde für PostgreSQL ergänzt.")
        if "schuljahr_beginn" not in config_columns:
            conn.execute(text(
                "ALTER TABLE public.system_konfiguration ADD COLUMN schuljahr_beginn DATE"
            ))
            print("Spalte 'system_konfiguration.schuljahr_beginn' wurde für PostgreSQL ergänzt.")
        if "aufbewahrung_jahre" not in config_columns:
            conn.execute(text(
                "ALTER TABLE public.system_konfiguration ADD COLUMN aufbewahrung_jahre INTEGER"
            ))
            print("Spalte 'system_konfiguration.aufbewahrung_jahre' wurde für PostgreSQL ergänzt.")
        schueler_columns = {
            row[0]
            for row in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'schueler'"
            )).all()
        }
        if "jahrgang" not in schueler_columns:
            conn.execute(text("ALTER TABLE public.schueler ADD COLUMN jahrgang INTEGER"))
            print("Spalte 'schueler.jahrgang' wurde für PostgreSQL ergänzt.")
        bogen_columns = {
            row[0]
            for row in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'bogen'"
            )).all()
        }
        if "pflicht" not in bogen_columns:
            conn.execute(text(
                "ALTER TABLE public.bogen ADD COLUMN pflicht BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            print("Spalte 'bogen.pflicht' wurde für PostgreSQL ergänzt.")
        if "schuljahresuebergreifend" not in bogen_columns:
            conn.execute(text(
                "ALTER TABLE public.bogen ADD COLUMN schuljahresuebergreifend BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            print("Spalte 'bogen.schuljahresuebergreifend' wurde für PostgreSQL ergänzt.")
        conn.commit()

def _add_new_columns():
    """Spalten fuer Benachrichtigungen und Diagnostik - SQLite und PostgreSQL."""
    engine = db.engine
    backend = engine.url.get_backend_name()
    if backend == "sqlite":
        def spalten(conn, tabelle):
            return {
                row["name"]
                for row in conn.execute(text(f'PRAGMA table_info("{tabelle}")')).mappings().all()
            }
        tabellen = {"user": '"user"', "notification": "notification", "elternkontakt": "elternkontakt",
                    "system_konfiguration": "system_konfiguration", "diagnostik_kennwert": "diagnostik_kennwert",
                    "diagnostik_ergebnis": "diagnostik_ergebnis"}
    elif backend in {"postgresql", "postgres"}:
        def spalten(conn, tabelle):
            return {
                row[0]
                for row in conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :tabelle"
                ), {"tabelle": tabelle}).all()
            }
        tabellen = {"user": 'public."user"', "notification": "public.notification", "elternkontakt": "public.elternkontakt",
                    "system_konfiguration": "public.system_konfiguration", "diagnostik_kennwert": "public.diagnostik_kennwert",
                    "diagnostik_ergebnis": "public.diagnostik_ergebnis"}
    else:
        return

    neue_spalten = [
        ("user", "email", "VARCHAR(255)", None),
        ("user", "mail_takt", "VARCHAR(20) NOT NULL DEFAULT 'taeglich'", None),
        ("notification", "kind", "VARCHAR(50)", None),
        # Bestehende Benachrichtigungen gelten als erledigt - sonst verschickte
        # der erste Versandlauf den ganzen Altbestand.
        ("notification", "mailed_at", "TIMESTAMP", "UPDATE {tabelle} SET mailed_at = created_at WHERE mailed_at IS NULL"),
        ("elternkontakt", "erinnert_fuer_termin", "DATE", None),
        # Risikostufen der Diagnostik; bestehende Konfigurationen bekommen die
        # Voreinstellung 25 / 16 / 10.
        ("system_konfiguration", "diagnostik_pr_beobachten", "INTEGER DEFAULT 25", None),
        ("system_konfiguration", "diagnostik_pr_auffaellig", "INTEGER DEFAULT 16", None),
        ("system_konfiguration", "diagnostik_pr_deutlich", "INTEGER DEFAULT 10", None),
        ("system_konfiguration", "diagnostik_lq_beobachten", "INTEGER DEFAULT 89", None),
        ("system_konfiguration", "diagnostik_lq_auffaellig", "INTEGER DEFAULT 79", None),
        ("system_konfiguration", "diagnostik_lq_deutlich", "INTEGER DEFAULT 69", None),
        # Was bisher die Stufe bestimmte (Leitwerte), zaehlt weiter; dazu die
        # HSP-Strategien, deren Prozentraenge die Schule einbeziehen will.
        ("diagnostik_kennwert", "risiko", "BOOLEAN NOT NULL DEFAULT FALSE",
         "UPDATE {tabelle} SET risiko = TRUE WHERE leitwert = TRUE OR name IN "
         "('Alphabetische Strategie', 'Orthografische Strategie', 'Morphematische Strategie', 'Wortübergreifende Strategie')"),
        # Klasse zum Testzeitpunkt; Bestand wird unten aus der heutigen Klasse zurückgerechnet.
        ("diagnostik_ergebnis", "klasse", "VARCHAR(20)", None),
    ]
    with engine.connect() as conn:
        vorhanden = {}
        for tabelle, spalte, typ, nacharbeit in neue_spalten:
            if tabelle not in vorhanden:
                vorhanden[tabelle] = spalten(conn, tabelle)
            if spalte in vorhanden[tabelle]:
                continue
            ziel = tabellen[tabelle]
            conn.execute(text(f"ALTER TABLE {ziel} ADD COLUMN {spalte} {typ}"))
            if nacharbeit:
                conn.execute(text(nacharbeit.format(tabelle=ziel)))
            print(f"Spalte '{tabelle}.{spalte}' wurde ergänzt.")
        conn.commit()


# Wir aktivieren den "App Context", damit wir Zugriff auf die DB-Konfiguration haben
with app.app_context():
    print("--- Starte Datenbank-Update ---")
    print("Prüfe auf neue Tabellen...")
    
    # Dieser Befehl ist sicher: Er erstellt nur Tabellen, die FEHLEN.
    # Bestehende Tabellen (User, Schueler, Beobachtungen) bleiben unberührt!
    db.create_all()
    _sqlite_add_missing_user_name_columns()
    _postgres_add_missing_user_role_column()
    _sqlite_add_missing_foerderplan_creator_column()
    _sqlite_add_missing_schueler_geburtsdatum_column()
    _sqlite_add_missing_workplan_weeks_column()
    _sqlite_add_missing_workplan_task_columns()
    _sqlite_add_missing_library_template_columns()
    _sqlite_add_missing_erziehung_event_columns()
    _postgres_add_missing_workplan_weeks_column()
    _postgres_add_missing_workplan_task_columns()
    _postgres_add_missing_library_template_columns()
    _postgres_add_missing_erziehung_event_columns()
    _sqlite_add_school_year_columns()
    _postgres_add_school_year_columns()
    _add_new_columns()

    # Jahrgaenge aus den Bestandsdaten ableiten. Idempotent: legt nur fehlende
    # Klassen an und fuellt nur leere Jahrgaenge, ueberschreibt nichts.
    from jahrgang import backfill_student_jahrgaenge, bereinige_klassennamen, sync_klassen
    bereinigt = bereinige_klassennamen()
    if bereinigt:
        print(f"Leerzeichen am Rand von Klassennamen entfernt ({bereinigt} Datensatz/Datensätze).")
    neue_klassen = sync_klassen()
    if neue_klassen:
        print(f"Klassen angelegt: {', '.join(neue_klassen)}")
    gesetzte_jahrgaenge = backfill_student_jahrgaenge()
    if gesetzte_jahrgaenge:
        print(f"Jahrgang bei {gesetzte_jahrgaenge} Kind(ern) aus der Klasse übernommen.")
    db.session.commit()

    # Diagnostik-Katalog mit HSP, SLS 1-4 und ELFE II vorbelegen - nur, wenn
    # noch keiner existiert; danach pflegt ihn die Verwaltung.
    from diagnostik import lege_vorbelegung_an, schaerfe_vorbelegung_nach
    if lege_vorbelegung_an():
        print("Diagnostik-Katalog mit HSP, SLS 1-4 und ELFE II vorbelegt.")
    from diagnostik import ergaenze_klassen_der_ergebnisse
    ergaenzt = ergaenze_klassen_der_ergebnisse()
    if ergaenzt:
        print(f"Klasse zum Testzeitpunkt bei {ergaenzt} Diagnostik-Ergebnis(sen) ergänzt.")
    nachgeschaerft = schaerfe_vorbelegung_nach()
    if nachgeschaerft:
        print(f"HSP-Vorbelegung an die Auswertungsmappen angepasst ({nachgeschaerft} Testform(en)).")
    from diagnostik import ergaenze_sls_testplan, sls_ohne_prozentrang
    if ergaenze_sls_testplan():
        print("SLS-Testplan um die Mitte von Klasse 2 ergänzt.")
    ohne_pr = sls_ohne_prozentrang()
    if ohne_pr:
        print(f"SLS: Prozentrang aus {ohne_pr} Kennwert(en) entfernt - das SLS liefert nur Rohwert und LQ.")
    db.session.commit()
    
    print("--- FERTIG! Die Datenbank wurde erweitert. ---")
    print("Ihre alten Daten sind sicher.")
