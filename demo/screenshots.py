#!/usr/bin/env python3
"""Screenshots der Demoinstanz erzeugen - für Artikel, Anleitungen und Schulungen.

Rendert die wichtigsten Seiten der App aus der Demodatenbank und legt sie als
PNG ab. Gebraucht wird ein Chromium (oder Chrome) im Pfad.

    python3 demo/screenshots.py                    # nach demo/screenshots/
    python3 demo/screenshots.py --ziel /tmp/bilder --breite 1400

Die Bilder zeigen ausschließlich erfundene Kinder aus den Demodaten.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

CSRF = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')

# Ein Reiter der Schülerakte lässt sich nicht über die Adresse öffnen - dieses
# Stück JavaScript schaltet ihn um, ohne auf die Einblendung zu warten.
REITER_JS = """
<style>.tab-pane.fade { transition: none !important; } .tab-pane.active { opacity: 1 !important; }</style>
<script>window.addEventListener('load', function () {
  document.querySelectorAll('.tab-pane').forEach(function (p) { p.classList.remove('active', 'show'); p.style.opacity = ''; });
  document.querySelectorAll('[data-bs-toggle="tab"], .nav-link').forEach(function (b) { b.classList.remove('active'); });
  var knopf = document.querySelector('%s'); var flaeche = document.querySelector('%s');
  if (knopf) { knopf.classList.add('active'); }
  if (flaeche) { flaeche.classList.add('active', 'show'); flaeche.style.opacity = '1'; }
});</script>
"""

# Höhe des Bildausschnitts je Seite; alles andere bekommt die Vorgabe.
HOEHEN = {
    '03_akte': 1500, '04_akte_foerderung': 1500, '05_akte_diagnostik': 1500,
    '06_matrix': 1500, '08_zeugnis': 1500, '09_diagnostik': 1500,
    '16_elternberatung': 1500, '18_konferenz_vorbereitung': 1500, '20_schuluebersicht': 1500,
    '01_start': 1250, '07_bericht': 1250, '11_wizard': 1250, '19_konferenz': 1250,
    '21_hospitation': 1250,
}
HOEHE_STANDARD = 1100


def chromium():
    for name in ('chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable'):
        pfad = shutil.which(name)
        if pfad:
            return pfad
    raise SystemExit('Kein Chromium gefunden - bitte chromium installieren.')


def seiten(app):
    """(Name, Benutzer, Adresse, Reiter) je Bild - Adressen mit echten IDs.

    Bewusst nachsichtig: In der Demo wird geklickt, und dann ist die Konferenz
    gestartet oder ein Plan geschlossen. Fehlt etwas, faellt das Bild weg.
    """
    from models import Foerderkonferenz, Foerderplan, Hospitation, Schueler, WorkPlan

    def kind(vorname, nachname):
        treffer = Schueler.query.filter_by(vorname=vorname, nachname=nachname).first()
        return treffer.id if treffer else None

    with app.app_context():
        ben = kind('Ben', 'Kraus')
        jonas = kind('Jonas', 'Peters')
        mia = kind('Mia', 'Berger')
        # Die Konferenz, an der gearbeitet wird - sonst die neueste.
        konferenz = (Foerderkonferenz.query
                     .filter(Foerderkonferenz.status != 'abgeschlossen')
                     .order_by(Foerderkonferenz.id.desc()).first()
                     or Foerderkonferenz.query.order_by(Foerderkonferenz.id.desc()).first())
        konferenz = konferenz.id if konferenz else None
        plan = (Foerderplan.query.filter_by(schueler_id=ben, status='aktiv').first()
                or Foerderplan.query.filter_by(status='aktiv').first())
        plan_kind, plan = (plan.schueler_id, plan.id) if plan else (None, None)
        hospitation = Hospitation.query.order_by(Hospitation.id.asc()).first()
        hospitation = hospitation.id if hospitation else None
        arbeitsplan = (WorkPlan.query.filter_by(status='aktiv').first()
                       or WorkPlan.query.first())
        arbeitsplan = arbeitsplan.id if arbeitsplan else None

    akte = f'/schuelerakte?schueler_id={ben}'
    alle = [
        ('01_start', 'sommer', '/', None),
        ('02_erfassen', 'sommer', f'/erfassen/einzel?schueler_id={ben}', None),
        ('03_akte', 'sommer', akte, None),
        ('04_akte_foerderung', 'sommer', akte, ('#akte-foerder-tab', '#akte-foerder-pane')),
        ('05_akte_diagnostik', 'sommer', akte, ('#akte-diagnostik-tab', '#akte-diagnostik-pane')),
        ('06_matrix', 'sommer', '/report/matrix', None),
        ('07_bericht', 'sommer', f'/report/schueler?schueler_id={ben}', None),
        ('08_zeugnis', 'sommer', f'/report/zeugnismaterial?schueler_id={ben}', None),
        ('09_diagnostik', 'sommer', '/diagnostik', None),
        ('10_stufen', 'wagner', '/diagnostik/stufenauswertung', None),
        ('11_wizard', 'sommer', f'/foerderplan/neu/{jonas}', None),
        ('12_plan', 'sommer', f'/foerderplan/view/{plan}', None),
        ('13_arbeitsplan', 'sommer', f'/arbeitsplaene?plan_id={arbeitsplan}', None),
        ('14_kurse', 'sommer', '/foerderkurse', None),
        ('15_nta', 'wagner', f'/nachteilsausgleich/kind/{ben}', None),
        ('16_elternberatung', 'sommer', f'/erfassen/elternberatung?schueler_id={ben}', None),
        ('17_ereignisse', 'sommer', '/erziehung', None),
        ('18_konferenz_vorbereitung', 'sommer', f'/konferenz/{konferenz}/vorbereitung', None),
        ('19_konferenz', 'wagner', f'/konferenz/{konferenz}', None),
        ('20_schuluebersicht', 'wagner', '/diagnostik/schule', None),
        ('21_hospitation', 'wagner', f'/hospitation/{hospitation}', None),
        ('22_verwaltung', 'admin', '/admin', None),
        ('30_bogen', 'sommer', f'/erfassen/schueler?schueler_id={ben}', None),
        ('31_reihe', 'sommer', '/erfassen/reihe/start', None),
        ('32_protokoll', 'sommer', f'/erfassen/elternkontakte/protokoll?schueler_id={ben}', None),
        ('33_ereignis_neu', 'sommer', f'/erziehung/neu?schueler_id={ben}', None),
        ('34_evaluieren', 'sommer', f'/foerderplan/evaluate/{plan}', None) if plan else None,
        ('35_diagnostik_erfassen', 'sommer', '/diagnostik/erfassen?klasse=3a', None),
        ('36_konto', 'sommer', '/konto', None),
        ('37_grundlagenblatt', 'sommer', f'/foerderplan/grundlagen/{mia}', None),
        ('38_aufgaben', 'sommer', '/konferenz/aufgaben', None),
        ('39_kontakte', 'sommer', '/erfassen/elternkontakte', None),
    ]
    # Seiten, deren Datensatz fehlt, fallen weg (None in der Adresse).
    return [eintrag for eintrag in alle
            if eintrag is not None and 'None' not in eintrag[2]]


def erzeuge(db_pfad, ziel, breite=1400, passwort='demo'):
    os.environ['DATABASE_URL'] = f'sqlite:///{Path(db_pfad).resolve()}'
    from app import create_app

    app = create_app()
    ziel = Path(ziel)
    ziel.mkdir(parents=True, exist_ok=True)
    browser = chromium()
    statisch = 'file://' + str(BASE_DIR / 'static') + '/'

    angemeldet = {}

    def klient(benutzer):
        if benutzer not in angemeldet:
            c = app.test_client()
            marke = CSRF.search(c.get('/login').get_data(as_text=True)).group(1)
            c.post('/login', data={'username': benutzer, 'password': passwort, '_csrf_token': marke},
                   follow_redirects=True)
            angemeldet[benutzer] = c
        return angemeldet[benutzer]

    fertig = []
    for name, benutzer, adresse, reiter in seiten(app):
        antwort = klient(benutzer).get(adresse, follow_redirects=True)
        if antwort.status_code != 200:
            print(f'  übersprungen ({antwort.status_code}): {name} – {adresse}')
            continue
        # Bilder und Stile lokal einbinden, Animationen anhalten.
        html = (antwort.get_data(as_text=True)
                .replace('/static/', statisch)
                .replace('animation:', 'x-animation:'))
        if reiter:
            html = html.replace('</body>', (REITER_JS % reiter) + '</body>')
        quelle = ziel / f'{name}.html'
        quelle.write_text(html, encoding='utf-8')
        subprocess.run([
            browser, '--headless=new', '--no-sandbox', '--disable-gpu', '--hide-scrollbars',
            '--virtual-time-budget=5000',
            f'--window-size={breite},{HOEHEN.get(name, HOEHE_STANDARD)}',
            f'--screenshot={ziel / (name + ".png")}', str(quelle),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        quelle.unlink()
        fertig.append(name)
    return fertig


def main():
    zerleger = argparse.ArgumentParser(description='Screenshots der Demoinstanz erzeugen.')
    zerleger.add_argument('--db', default=str(BASE_DIR / 'instance' / 'demo.db'),
                          help='Demodatenbank (Vorgabe: instance/demo.db)')
    zerleger.add_argument('--ziel', default=str(BASE_DIR / 'demo' / 'screenshots'),
                          help='Ordner für die Bilder (Vorgabe: demo/screenshots)')
    zerleger.add_argument('--breite', type=int, default=1400, help='Bildbreite in Pixeln')
    argumente = zerleger.parse_args()

    if not Path(argumente.db).exists():
        raise SystemExit(f'{argumente.db} fehlt - zuerst demo/demo_daten.py ausführen.')

    fertig = erzeuge(argumente.db, argumente.ziel, argumente.breite)
    print(f'{len(fertig)} Screenshots in {argumente.ziel}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
