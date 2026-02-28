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
    foerdergrundlage = db.relationship(
        'Foerdergrundlage',
        back_populates='schueler',
        uselist=False,
        cascade="all, delete-orphan",
    )


class Bogen(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    titel = db.Column(db.String(100))
    items = db.relationship('Item', backref='bogen', lazy=True)


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


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True)
    vorname = db.Column(db.String(100), nullable=True)
    nachname = db.Column(db.String(100), nullable=True)
    password_hash = db.Column(db.String(200))

    @property
    def full_name(self):
        parts = [p.strip() for p in [self.vorname or '', self.nachname or ''] if p and p.strip()]
        return " ".join(parts)

    @property
    def display_name(self):
        return self.full_name or self.username


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
    schueler = db.relationship('Schueler', backref='foerderplaene')
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

    schueler = db.relationship('Schueler', backref='elternkontakte')
    user = db.relationship('User', backref='elternkontakte')


class SystemKonfiguration(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    schuljahr = db.Column(db.String(20), nullable=True)
    elternsprechtag_1 = db.Column(db.Date, nullable=True)
    elternsprechtag_2 = db.Column(db.Date, nullable=True)
    workplan_suggestions_weeks = db.Column(db.Integer, nullable=False, default=12)


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

    student = db.relationship('Schueler', backref='work_plans')
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
    copied_from_task = db.relationship('WorkPlanTask', remote_side=[id], uselist=False)


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
