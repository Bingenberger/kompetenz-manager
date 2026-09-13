from flask_login import UserMixin
import uuid

from extensions import db
from time_utils import utc_now


class Schueler(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vorname = db.Column(db.String(100))
    nachname = db.Column(db.String(100))
    klasse = db.Column(db.String(20))
    geburtsdatum = db.Column(db.Date, nullable=True)
    # Klassenstufe 1 bis 4. Ein Kind hat genau einen Jahrgang; in einer
    # jahrgangsuebergreifenden Klasse ist es einer von deren Jahrgaengen.
    jahrgang = db.Column(db.Integer, nullable=True, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    archived_at = db.Column(db.DateTime, nullable=True)
    foerdergrundlage = db.relationship(
        'Foerdergrundlage',
        back_populates='schueler',
        uselist=False,
        cascade="all, delete-orphan",
    )


class Bogen(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    titel = db.Column(db.String(100))
    # Pflichtboegen stehen beim Erfassen vorn, optionale hinter "Weitere Boegen".
    # Bestehende Boegen sind optional - solange es keinen Pflichtbogen gibt,
    # bleibt die Auswahl eine einfache Liste wie bisher.
    pflicht = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    items = db.relationship('Item', backref='bogen', lazy=True)
    # Ohne Zuordnung gilt ein Bogen fuer alle Jahrgaenge - so bleiben
    # bestehende Boegen nach der Einfuehrung unveraendert nutzbar.
    jahrgang_zuordnungen = db.relationship(
        'BogenJahrgang', backref='bogen', cascade='all, delete-orphan', lazy=True,
    )

    @property
    def jahrgaenge(self):
        return sorted(zuordnung.jahrgang for zuordnung in self.jahrgang_zuordnungen)

    def gilt_fuer(self, jahrgang):
        """True, wenn der Bogen fuer diesen Jahrgang angeboten werden soll."""
        stufen = self.jahrgaenge
        return not stufen or jahrgang in stufen


class BogenJahrgang(db.Model):
    bogen_id = db.Column(db.Integer, db.ForeignKey('bogen.id'), primary_key=True)
    jahrgang = db.Column(db.Integer, primary_key=True)


class Klasse(db.Model):
    """Eine Klasse mit ihren Jahrgaengen.

    Kinder, Lehrkraft-Zuordnungen und Aufgabenbibliotheken verweisen weiterhin
    ueber den Klassennamen als Text - wie vor Einfuehrung dieser Tabelle. Sie
    ergaenzt die Namen um die Jahrgaenge, statt die Verweise umzubauen.
    """
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), nullable=False, unique=True)
    jahrgang_zuordnungen = db.relationship(
        'KlasseJahrgang', backref='klasse', cascade='all, delete-orphan', lazy=True,
    )

    @property
    def jahrgaenge(self):
        return sorted(zuordnung.jahrgang for zuordnung in self.jahrgang_zuordnungen)

    @property
    def ist_jahrgangsuebergreifend(self):
        return len(self.jahrgang_zuordnungen) > 1


class KlasseJahrgang(db.Model):
    klasse_id = db.Column(db.Integer, db.ForeignKey('klasse.id'), primary_key=True)
    jahrgang = db.Column(db.Integer, primary_key=True)


class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bogen_id = db.Column(db.Integer, db.ForeignKey('bogen.id'))
    text = db.Column(db.String(200))
    bereich = db.Column(db.String(100))


class Beobachtung(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    datum = db.Column(db.DateTime, default=utc_now)
    wert = db.Column(db.Integer)
    kommentar = db.Column(db.Text)
    foto_pfad = db.Column(db.String(200))
    anlass = db.Column(db.String(100))

    schueler_id = db.Column(db.Integer, db.ForeignKey('schueler.id'))
    item_id = db.Column(db.Integer, db.ForeignKey('item.id'))

    schueler = db.relationship(
        'Schueler',
        backref=db.backref('beobachtungen', cascade='all, delete-orphan'),
    )


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True)
    vorname = db.Column(db.String(100), nullable=True)
    nachname = db.Column(db.String(100), nullable=True)
    password_hash = db.Column(db.String(200))
    role = db.Column(db.String(20), nullable=False, default='teacher')
    # Fuer Benachrichtigungen per E-Mail. Ohne Adresse gibt es nur die Glocke.
    email = db.Column(db.String(255), nullable=True)
    # 'sofort' | 'taeglich' | 'aus' - siehe benachrichtigungen.MAIL_TAKTE
    mail_takt = db.Column(db.String(20), nullable=False, default='taeglich', server_default='taeglich')

    @property
    def full_name(self):
        parts = [p.strip() for p in [self.vorname or '', self.nachname or ''] if p and p.strip()]
        return " ".join(parts)

    @property
    def display_name(self):
        return self.full_name or self.username

    @property
    def is_admin(self):
        return (self.role or '').strip().lower() == 'admin'


class UserKlassenzuordnung(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    klasse = db.Column(db.String(20), nullable=False)
    rolle = db.Column(db.String(20), nullable=False)  # 'klassenleitung' | 'fach'

    __table_args__ = (
        db.UniqueConstraint('user_id', 'klasse', 'rolle', name='uq_user_klasse_rolle'),
    )

    user = db.relationship('User', backref='klassenzuordnungen')


class Foerderplan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    schueler_id = db.Column(db.Integer, db.ForeignKey('schueler.id'))
    creator_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    titel = db.Column(db.String(100))
    datum_erstellung = db.Column(db.Date, default=lambda: utc_now().date())
    datum_evaluation = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), default='aktiv')

    inhalte = db.relationship('Foerderinhalt', backref='plan', lazy=True, cascade="all, delete-orphan")
    schueler = db.relationship('Schueler', backref=db.backref('foerderplaene', cascade='all, delete-orphan'))
    creator = db.relationship('User', backref='erstellte_foerderplaene', foreign_keys=[creator_user_id])


class Foerderinhalt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('foerderplan.id'))

    foerderziel = db.Column(db.String(200))
    ist_zustand = db.Column(db.Text)
    soll_zustand = db.Column(db.Text)
    massnahmen = db.Column(db.Text)

    evaluation_text = db.Column(db.Text)
    status_id = db.Column(db.Integer, default=0)


class FoerderplanLog(db.Model):
    """Journal eines Foerderplans - wer wann was geaendert hat.

    Eigenes Modell mit Fremdschluessel statt einer gemeinsamen Tabelle fuer
    alles: so nimmt das Loeschen des Plans sein Journal mit.
    """
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('foerderplan.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    action = db.Column(db.String(50), nullable=False)
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    plan = db.relationship(
        'Foerderplan',
        backref=db.backref(
            'logs',
            order_by='desc(FoerderplanLog.created_at)',
            lazy=True,
            cascade='all, delete-orphan',
        ),
    )
    user = db.relationship('User')


class Foerdergrundlage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    schueler_id = db.Column(db.Integer, db.ForeignKey('schueler.id'), nullable=False, unique=True)

    besondere_staerken = db.Column(db.Text, nullable=True)
    vorrangiger_foerderbedarf = db.Column(db.Text, nullable=True)
    besonderheiten_entwicklung = db.Column(db.Text, nullable=True)
    wichtige_informationen = db.Column(db.Text, nullable=True)
    absprachen_mit_eltern = db.Column(db.Text, nullable=True)

    zuletzt_aktualisiert_am = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    schueler = db.relationship('Schueler', back_populates='foerdergrundlage')


class Elternkontakt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    datum = db.Column(db.DateTime, default=utc_now)

    schueler_id = db.Column(db.Integer, db.ForeignKey('schueler.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    eintrag_typ = db.Column(db.String(20), nullable=False)  # 'notiz' | 'protokoll'
    kontaktform = db.Column(db.String(50), nullable=True)   # Kurz-Kontakt, Telefonat, Mail, ...

    betreff = db.Column(db.String(200), nullable=True)
    mitteilung = db.Column(db.Text, nullable=True)

    # Protokollfelder (bei Typ "protokoll")
    teilnehmende = db.Column(db.Text, nullable=True)
    gespraechsanlass = db.Column(db.Text, nullable=True)
    besprochenes = db.Column(db.Text, nullable=True)
    vereinbarungen_schule = db.Column(db.Text, nullable=True)
    vereinbarungen_eltern = db.Column(db.Text, nullable=True)
    naechste_schritte = db.Column(db.Text, nullable=True)
    naechster_termin = db.Column(db.Date, nullable=True)
    # Fuer welches Termindatum schon erinnert wurde (benachrichtigungen.py).
    erinnert_fuer_termin = db.Column(db.Date, nullable=True)

    schueler = db.relationship('Schueler', backref=db.backref('elternkontakte', cascade='all, delete-orphan'))
    user = db.relationship('User', backref='elternkontakte')


class ElternkontaktLog(db.Model):
    """Journal einer Notiz oder eines Gespraechsprotokolls."""
    id = db.Column(db.Integer, primary_key=True)
    kontakt_id = db.Column(db.Integer, db.ForeignKey('elternkontakt.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    action = db.Column(db.String(50), nullable=False)
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    kontakt = db.relationship(
        'Elternkontakt',
        backref=db.backref(
            'logs',
            order_by='desc(ElternkontaktLog.created_at)',
            lazy=True,
            cascade='all, delete-orphan',
        ),
    )
    user = db.relationship('User')


class Elternberatung(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    datum = db.Column(db.Date, nullable=False, default=lambda: utc_now().date())
    anlass = db.Column(db.Text, nullable=True)
    weitere_beratungspunkte = db.Column(db.Text, nullable=True)
    vereinbarungen = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    schueler_id = db.Column(db.Integer, db.ForeignKey('schueler.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    schueler = db.relationship('Schueler', backref=db.backref('elternberatungen', cascade='all, delete-orphan'))
    user = db.relationship('User', backref='elternberatungen')


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=True)
    target_url = db.Column(db.String(255), nullable=True)
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    # Art aus benachrichtigungen.ARTEN; leer bei Altbestand.
    kind = db.Column(db.String(50), nullable=True, index=True)
    # Gesetzt, sobald der Mailversand die Benachrichtigung erledigt hat -
    # verschickt oder bewusst uebergangen. Leer heisst: noch offen.
    mailed_at = db.Column(db.DateTime, nullable=True, index=True)

    user = db.relationship('User', backref=db.backref('notifications', cascade='all, delete-orphan'))


class BenachrichtigungAbbestellt(db.Model):
    """Eine Benachrichtigungsart, die eine Lehrkraft nicht erhalten will."""
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True)
    art = db.Column(db.String(50), primary_key=True)

    user = db.relationship(
        'User', backref=db.backref('abbestellte_benachrichtigungen', cascade='all, delete-orphan'),
    )


class AuthRateLimit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    scope = db.Column(db.String(20), nullable=False)
    scope_key = db.Column(db.String(255), nullable=False)
    failure_count = db.Column(db.Integer, nullable=False, default=0)
    first_failed_at = db.Column(db.DateTime, nullable=True)
    last_failed_at = db.Column(db.DateTime, nullable=True)
    locked_until = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        db.UniqueConstraint('scope', 'scope_key', name='uq_auth_rate_limit_scope_key'),
    )


class ErziehungsEreignisKategorie(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class ErziehungsEreignisVorlage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('erziehungs_ereignis_kategorie.id'), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    category = db.relationship('ErziehungsEreignisKategorie', backref='event_templates')


class ErziehungsOrt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class ErziehungsKonsequenz(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False, unique=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class ErziehungsEreignis(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    datum = db.Column(db.Date, nullable=False, default=lambda: utc_now().date())
    beschreibung = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='offen')
    consequence_notes = db.Column(db.Text, nullable=True)
    child_statement = db.Column(db.Text, nullable=True)
    others_statement = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    student_id = db.Column(db.Integer, db.ForeignKey('schueler.id'), nullable=False, index=True)
    event_template_id = db.Column(db.Integer, db.ForeignKey('erziehungs_ereignis_vorlage.id'), nullable=False, index=True)
    ort_id = db.Column(db.Integer, db.ForeignKey('erziehungs_ort.id'), nullable=False, index=True)
    assigned_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)

    student = db.relationship(
        'Schueler',
        foreign_keys=[student_id],
        backref=db.backref('erziehungsereignisse', cascade='all, delete-orphan'),
    )
    event_template = db.relationship('ErziehungsEreignisVorlage', backref='events')
    ort = db.relationship('ErziehungsOrt', backref='events')
    assigned_user = db.relationship('User', foreign_keys=[assigned_user_id], backref='assigned_erziehungsereignisse')
    created_by_user = db.relationship('User', foreign_keys=[created_by_user_id], backref='created_erziehungsereignisse')


class ErziehungsEreignisLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('erziehungs_ereignis.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    action = db.Column(db.String(50), nullable=False)
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    event = db.relationship(
        'ErziehungsEreignis',
        backref=db.backref(
            'logs',
            order_by='desc(ErziehungsEreignisLog.created_at)',
            lazy=True,
            cascade='all, delete-orphan',
        ),
    )
    user = db.relationship('User')


class ErziehungsEreignisBetroffenesKind(db.Model):
    event_id = db.Column(db.Integer, db.ForeignKey('erziehungs_ereignis.id'), primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('schueler.id'), primary_key=True)

    event = db.relationship('ErziehungsEreignis', backref=db.backref('affected_students', cascade='all, delete-orphan'))
    student = db.relationship('Schueler', backref=db.backref('betroffen_bei_ereignissen', cascade='all, delete-orphan'))


class ErziehungsEreignisKonsequenz(db.Model):
    event_id = db.Column(db.Integer, db.ForeignKey('erziehungs_ereignis.id'), primary_key=True)
    consequence_id = db.Column(db.Integer, db.ForeignKey('erziehungs_konsequenz.id'), primary_key=True)

    event = db.relationship('ErziehungsEreignis', backref=db.backref('selected_consequences', cascade='all, delete-orphan'))
    consequence = db.relationship('ErziehungsKonsequenz')


class ErziehungsEreignisElternkontakt(db.Model):
    event_id = db.Column(db.Integer, db.ForeignKey('erziehungs_ereignis.id'), primary_key=True)
    kontakt_id = db.Column(db.Integer, db.ForeignKey('elternkontakt.id'), primary_key=True)

    event = db.relationship('ErziehungsEreignis', backref=db.backref('linked_parent_contacts', cascade='all, delete-orphan'))
    kontakt = db.relationship('Elternkontakt', backref=db.backref('ereignis_verknuepfungen', cascade='all, delete-orphan'))


class ErziehungsEreignisAnhang(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('erziehungs_ereignis.id'), nullable=False, index=True)
    file_path = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=True)
    mime_type = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    event = db.relationship('ErziehungsEreignis', backref=db.backref('attachments', cascade='all, delete-orphan'))


class SystemKonfiguration(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    schuljahr = db.Column(db.String(20), nullable=True)
    schuljahr_beginn = db.Column(db.Date, nullable=True)
    elternsprechtag_1 = db.Column(db.Date, nullable=True)
    elternsprechtag_2 = db.Column(db.Date, nullable=True)
    workplan_suggestions_weeks = db.Column(db.Integer, nullable=False, default=12)
    # Aufbewahrungsfrist in Jahren nach der Archivierung eines Kindes. Leer heisst
    # ausgeschaltet: ohne eingetragene Frist meldet die Anwendung nichts als
    # ueberfaellig. Die Frist gibt das Landesrecht vor, nicht diese Software.
    aufbewahrung_jahre = db.Column(db.Integer, nullable=True)
    # Risikostufen der Diagnostik als Prozentrang-Grenzen (einschliesslich).
    # Leer heisst: Stufe ausgeschaltet.
    diagnostik_pr_beobachten = db.Column(db.Integer, nullable=True, default=25)
    diagnostik_pr_auffaellig = db.Column(db.Integer, nullable=True, default=16)
    diagnostik_pr_deutlich = db.Column(db.Integer, nullable=True, default=10)


class Schuljahreswechsel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    altes_schuljahr = db.Column(db.String(20), nullable=True)
    neues_schuljahr = db.Column(db.String(20), nullable=False)
    schuljahr_beginn = db.Column(db.Date, nullable=False)
    wiederholer_ids = db.Column(db.Text, nullable=True)
    versetzt_anzahl = db.Column(db.Integer, nullable=False, default=0)
    archiviert_anzahl = db.Column(db.Integer, nullable=False, default=0)
    unveraendert_anzahl = db.Column(db.Integer, nullable=False, default=0)
    zuordnungen_versetzt = db.Column(db.Integer, nullable=False, default=0)
    zuordnungen_entfernt = db.Column(db.Integer, nullable=False, default=0)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    created_by = db.relationship('User')


class WorkPlan(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    student_id = db.Column(db.Integer, db.ForeignKey('schueler.id'), nullable=False, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='in_planung')
    notes_for_child = db.Column(db.Text, nullable=True)
    notes_for_teacher = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    student = db.relationship('Schueler', backref=db.backref('work_plans', cascade='all, delete-orphan'))
    creator = db.relationship('User', backref='work_plans', foreign_keys=[created_by_user_id])
    tasks = db.relationship('WorkPlanTask', back_populates='work_plan', cascade="all, delete-orphan")


class WorkPlanTask(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    work_plan_id = db.Column(db.String(36), db.ForeignKey('work_plan.id'), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    learning_area = db.Column(db.String(50), nullable=True)
    icon_name = db.Column(db.String(50), nullable=True)
    instructions = db.Column(db.Text, nullable=True)
    materials = db.Column(db.Text, nullable=True)
    child_goal = db.Column(db.Text, nullable=True)
    source_type = db.Column(db.String(20), nullable=False, default='manual')
    copied_from_task_id = db.Column(db.String(36), db.ForeignKey('work_plan_task.id'), nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    work_plan = db.relationship('WorkPlan', back_populates='tasks')
    competency_links = db.relationship('WorkPlanTaskCompetency', back_populates='task', cascade="all, delete-orphan")
    attachments = db.relationship('WorkPlanTaskAttachment', back_populates='task', cascade="all, delete-orphan")
    evaluation = db.relationship('WorkPlanTaskEvaluation', back_populates='task', uselist=False, cascade="all, delete-orphan")
    # Ohne Backref wuerde SQLAlchemy den Verweis beim Loeschen der Quelle nicht
    # aufloesen und einen Fremdschluessel auf ein entferntes Original hinterlassen.
    copied_from_task = db.relationship(
        'WorkPlanTask',
        remote_side=[id],
        uselist=False,
        backref=db.backref('copies', lazy=True),
    )


class WorkPlanTaskCompetency(db.Model):
    task_id = db.Column(db.String(36), db.ForeignKey('work_plan_task.id'), primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('item.id'), primary_key=True)

    task = db.relationship('WorkPlanTask', back_populates='competency_links')
    item = db.relationship('Item')


class WorkPlanTaskAttachment(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = db.Column(db.String(36), db.ForeignKey('work_plan_task.id'), nullable=False, index=True)
    file_path = db.Column(db.String(255), nullable=False)
    caption = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    task = db.relationship('WorkPlanTask', back_populates='attachments')


class WorkPlanTaskEvaluation(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = db.Column(db.String(36), db.ForeignKey('work_plan_task.id'), nullable=False, unique=True, index=True)
    rating = db.Column(db.String(20), nullable=False)
    comment = db.Column(db.Text, nullable=True)
    evaluated_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    re_proposal = db.Column(db.Boolean, nullable=False, default=False)

    task = db.relationship('WorkPlanTask', back_populates='evaluation')


class ClassTaskLibrary(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    class_name = db.Column(db.String(20), nullable=False, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    creator = db.relationship('User')
    templates = db.relationship('ClassTaskTemplate', back_populates='library', cascade="all, delete-orphan")


class ClassTaskTemplate(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    library_id = db.Column(db.String(36), db.ForeignKey('class_task_library.id'), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    learning_area = db.Column(db.String(50), nullable=True)
    icon_name = db.Column(db.String(50), nullable=True)
    instructions = db.Column(db.Text, nullable=True)
    materials = db.Column(db.Text, nullable=True)
    diff_hints = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    library = db.relationship('ClassTaskLibrary', back_populates='templates')
    competency_links = db.relationship('ClassTaskTemplateCompetency', back_populates='template', cascade="all, delete-orphan")


class ClassTaskTemplateCompetency(db.Model):
    template_id = db.Column(db.String(36), db.ForeignKey('class_task_template.id'), primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('item.id'), primary_key=True)

    template = db.relationship('ClassTaskTemplate', back_populates='competency_links')
    item = db.relationship('Item')


# ----------------------------------------------------------------------
# Standardisierte Diagnostik (HSP, SLS, ELFE II, ...)
#
# Die Verfahren sind kein fester Programmteil, sondern ein Katalog, den die
# Verwaltung pflegt: Verfahren -> Testform -> Kennwerte, dazu der Testplan
# (welche Testform in welchem Jahrgang zur Mitte oder am Ende des Schuljahres).
# Normtabellen rechnet die Anwendung nicht nach - eingetragen werden Rohwert
# und Normwerte aus dem Auswertungsbogen.
# ----------------------------------------------------------------------

class DiagnostikVerfahren(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    # Lernbereich, ueber den der Verlauf zusammengefasst wird - etwa "Lesen"
    # fuer das SLS in Klasse 1 und 2 und ELFE II ab Klasse 3.
    bereich = db.Column(db.String(50), nullable=False)
    beschreibung = db.Column(db.Text, nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    testformen = db.relationship(
        'DiagnostikTestform', backref='verfahren', cascade='all, delete-orphan',
        order_by='DiagnostikTestform.sort_order',
    )


class DiagnostikTestform(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    verfahren_id = db.Column(db.Integer, db.ForeignKey('diagnostik_verfahren.id'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    kennwerte = db.relationship(
        'DiagnostikKennwert', backref='testform', cascade='all, delete-orphan',
        order_by='DiagnostikKennwert.sort_order',
    )
    zeitpunkte = db.relationship(
        'DiagnostikZeitpunkt', backref='testform', cascade='all, delete-orphan',
        order_by='DiagnostikZeitpunkt.jahrgang',
    )

    @property
    def zeitpunkte_sortiert(self):
        return sorted(self.zeitpunkte, key=lambda z: (z.jahrgang, 0 if z.halbjahr == 'mitte' else 1))


class DiagnostikKennwert(db.Model):
    """Eine Messgroesse einer Testform, etwa "Graphemtreffer"."""
    id = db.Column(db.Integer, primary_key=True)
    testform_id = db.Column(db.Integer, db.ForeignKey('diagnostik_testform.id'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    # Welche Werte der Auswertungsbogen zu dieser Groesse liefert.
    rohwert = db.Column(db.Boolean, nullable=False, default=True)
    prozentrang = db.Column(db.Boolean, nullable=False, default=True)
    t_wert = db.Column(db.Boolean, nullable=False, default=False)
    lesequotient = db.Column(db.Boolean, nullable=False, default=False)
    # Leitwerte bilden Verlauf, Diagramm und den angezeigten Prozentrang.
    leitwert = db.Column(db.Boolean, nullable=False, default=False)
    # Kennwerte, deren Prozentrang in die Risikostufe eingeht - neben den
    # Leitwerten etwa die HSP-Strategien. Getrennt vom Leitwert, damit das
    # Diagramm nicht sechs Linien je HSP zeigt.
    risiko = db.Column(db.Boolean, nullable=False, default=False)

    @property
    def wertarten(self):
        return [art for art in ('rohwert', 'prozentrang', 't_wert', 'lesequotient') if getattr(self, art)]


class DiagnostikZeitpunkt(db.Model):
    """Testplan: diese Testform in diesem Jahrgang zur Mitte oder am Ende."""
    id = db.Column(db.Integer, primary_key=True)
    testform_id = db.Column(db.Integer, db.ForeignKey('diagnostik_testform.id'), nullable=False, index=True)
    jahrgang = db.Column(db.Integer, nullable=False)
    halbjahr = db.Column(db.String(10), nullable=False)  # 'mitte' | 'ende'

    __table_args__ = (
        db.UniqueConstraint('testform_id', 'jahrgang', 'halbjahr', name='uq_diagnostik_zeitpunkt'),
    )

    @property
    def label(self):
        return f"{'Mitte' if self.halbjahr == 'mitte' else 'Ende'} Klasse {self.jahrgang}"


class DiagnostikErgebnis(db.Model):
    """Eine durchgefuehrte Testung eines Kindes.

    Jahrgang, Halbjahr und Schuljahr stehen am Ergebnis selbst, nicht nur im
    Testplan: aendert sich der Plan spaeter, bleibt nachvollziehbar, wann
    getestet wurde.
    """
    id = db.Column(db.Integer, primary_key=True)
    schueler_id = db.Column(db.Integer, db.ForeignKey('schueler.id'), nullable=False, index=True)
    testform_id = db.Column(db.Integer, db.ForeignKey('diagnostik_testform.id'), nullable=False, index=True)
    schuljahr = db.Column(db.String(20), nullable=False)
    halbjahr = db.Column(db.String(10), nullable=False)
    jahrgang = db.Column(db.Integer, nullable=True)
    # Klasse zum Testzeitpunkt - nach dem Schuljahreswechsel ist aus der 2c die
    # 3c geworden, die Stufenauswertung soll aber die Klasse von damals zeigen.
    klasse = db.Column(db.String(20), nullable=True)
    datum = db.Column(db.Date, nullable=True)
    bemerkung = db.Column(db.Text, nullable=True)
    erfasst_von_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        db.UniqueConstraint('schueler_id', 'testform_id', 'schuljahr', 'halbjahr', name='uq_diagnostik_ergebnis'),
    )

    schueler = db.relationship(
        'Schueler', backref=db.backref('diagnostik_ergebnisse', cascade='all, delete-orphan'),
    )
    testform = db.relationship('DiagnostikTestform')
    erfasst_von = db.relationship('User')
    werte = db.relationship('DiagnostikWert', backref='ergebnis', cascade='all, delete-orphan')

    def wert_fuer(self, kennwert_id):
        return next((wert for wert in self.werte if wert.kennwert_id == kennwert_id), None)


class DiagnostikWert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ergebnis_id = db.Column(db.Integer, db.ForeignKey('diagnostik_ergebnis.id'), nullable=False, index=True)
    kennwert_id = db.Column(db.Integer, db.ForeignKey('diagnostik_kennwert.id'), nullable=False, index=True)
    rohwert = db.Column(db.Integer, nullable=True)
    prozentrang = db.Column(db.Integer, nullable=True)
    t_wert = db.Column(db.Integer, nullable=True)
    lesequotient = db.Column(db.Integer, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('ergebnis_id', 'kennwert_id', name='uq_diagnostik_wert'),
    )

    kennwert = db.relationship('DiagnostikKennwert')


class Foerderangaben(db.Model):
    """Unterstützung eines Kindes in einem Schuljahr.

    Je Schuljahr, weil sich Nachteilsausgleich, Förderkurs und Schwerpunkt von
    Jahr zu Jahr ändern und eine Stufenauswertung den Stand ihres Schuljahres
    zeigen soll. Der Förderplan steht nicht hier - den kennt die Anwendung.
    """
    id = db.Column(db.Integer, primary_key=True)
    schueler_id = db.Column(db.Integer, db.ForeignKey('schueler.id'), nullable=False, index=True)
    schuljahr = db.Column(db.String(20), nullable=False)
    nachteilsausgleich = db.Column(db.Boolean, nullable=False, default=False)
    foerderkurs = db.Column(db.Boolean, nullable=False, default=False)
    externe_foerderung = db.Column(db.Boolean, nullable=False, default=False)
    foerderschwerpunkt = db.Column(db.Text, nullable=True)
    anmerkungen = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    __table_args__ = (
        db.UniqueConstraint('schueler_id', 'schuljahr', name='uq_foerderangaben_schuljahr'),
    )

    schueler = db.relationship(
        'Schueler', backref=db.backref('foerderangaben', cascade='all, delete-orphan'),
    )

    @property
    def leer(self):
        return not (self.nachteilsausgleich or self.foerderkurs or self.externe_foerderung
                    or (self.foerderschwerpunkt or '').strip() or (self.anmerkungen or '').strip())
