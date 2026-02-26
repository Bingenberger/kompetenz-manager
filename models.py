from flask_login import UserMixin

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
