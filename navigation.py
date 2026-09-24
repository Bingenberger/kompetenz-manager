"""Die Bereiche der Anwendung und ihre Navigation.

Sieben Bereiche in der Kopfzeile, je Bereich eine Unter-Navigation. Welche
Seite zu welchem Bereich gehört, ergibt sich aus dem Endpunkt - so muss keine
Vorlage wissen, wo sie hängt. Die Verwaltung ist kein Bereich, sondern das
Zahnrad; ihre Seiten markieren keinen Bereich als aktiv.
"""

from flask import request, url_for
from flask_login import current_user

# (Schlüssel, Beschriftung, Symbol, Endpunkt, Unterpunkte)
# Unterpunkt: (Beschriftung, Endpunkt, Endpunkt-Präfixe, die dazugehören[, Recht])
# Das optionale Recht ist eine Eigenschaft des Benutzers (etwa ist_schulleitung).
BEREICHE = [
    ('start', 'Start', 'house', 'system.index', []),
    ('kinder', 'Kinder', 'people', 'system.schuelerakte', []),
    ('erfassen', 'Erfassen', 'pencil-square', 'erfassung.erfassen_einzel', [
        ('Schnelleintrag', 'erfassung.erfassen_einzel', ('erfassung.erfassen_einzel',)),
        ('Ganzer Bogen', 'erfassung.erfassen_schueler', ('erfassung.erfassen_schueler',)),
        ('Klassen-Durchlauf', 'erfassung.reihe_start', ('erfassung.reihe_',)),
        ('Mehrere Items', 'erfassung.multi_start', ('erfassung.multi_',)),
        ('Elternkontakte', 'erfassung.elternkontakte_start', ('erfassung.elternkontakt', 'erfassung.elternberatung')),
    ]),
    ('auswertung', 'Auswertung', 'graph-up', 'report.report_matrix', [
        ('Klassenübersicht', 'report.report_matrix', ('report.report_matrix',)),
        ('Bericht je Kind', 'report.report_schueler', ('report.report_schueler', 'report.report_beobachtung')),
        ('Zeugnismaterial', 'report.report_material_view', ('report.report_material',)),
    ]),
    ('foerderung', 'Förderung', 'heart-pulse', 'foerderplan.foerderplan_list', [
        ('Förderpläne', 'foerderplan.foerderplan_list', ('foerderplan.',)),
        ('Arbeitspläne', 'workplan.workplan_list_page', ('workplan.',)),
        ('Förderkurse', 'foerderkurs.liste', ('foerderkurs.liste', 'foerderkurs.kurs', 'foerderkurs.teilnahme', 'foerderkurs.ohne_plan')),
        ('Förderkonferenz', 'konferenz.liste', ('konferenz.',)),
        ('Hospitationen', 'hospitation.liste', ('hospitation.',), 'ist_schulleitung'),
    ]),
    ('ereignisse', 'Ereignisse', 'shield-exclamation', 'erziehung.erziehung_list', [
        ('Übersicht', 'erziehung.erziehung_list', ('erziehung.erziehung_list', 'erziehung.erziehung_view', 'erziehung.erziehung_edit')),
        ('Neues Ereignis', 'erziehung.erziehung_new', ('erziehung.erziehung_new',)),
    ]),
    ('diagnostik', 'Diagnostik', 'clipboard-data', 'diagnostik.uebersicht', [
        ('Übersicht', 'diagnostik.uebersicht', ('diagnostik.uebersicht',)),
        ('Eintragen', 'diagnostik.erfassen', ('diagnostik.erfassen', 'diagnostik.ergebnis_')),
        ('Import', 'diagnostik.importieren', ('diagnostik.importieren',)),
        ('Stufenauswertung', 'diagnostik.stufenauswertung', ('diagnostik.stufenauswertung',)),
        ('Schulübersicht', 'diagnostik.schuluebersicht', ('diagnostik.schuluebersicht',), 'ist_schulleitung'),
    ]),
]

# Endpunkte, die nicht über das Blueprint-Präfix zuzuordnen sind.
_AUSNAHMEN = {
    'system.schuelerakte': 'kinder',
    'system.schuelerakte_export_odt': 'kinder',
    'system.schuelerakte_export_pdf': 'kinder',
    'system.suche': 'kinder',
}
_BLUEPRINTS = {
    'system': 'start',
    'erfassung': 'erfassen',
    'report': 'auswertung',
    'foerderplan': 'foerderung',
    'workplan': 'foerderung',
    'konferenz': 'foerderung',
    'foerderkurs': 'foerderung',
    'hospitation': 'foerderung',
    'erziehung': 'ereignisse',
    'diagnostik': 'diagnostik',
}


def aktiver_bereich(endpoint):
    """Schlüssel des Bereichs, zu dem ein Endpunkt gehört, sonst None."""
    if not endpoint:
        return None
    if endpoint in _AUSNAHMEN:
        return _AUSNAHMEN[endpoint]
    if endpoint.startswith('diagnostik.admin_') or endpoint.startswith('admin.') or endpoint.startswith('foerderkurs.admin_'):
        return None
    return _BLUEPRINTS.get(endpoint.split('.', 1)[0])


def navigation():
    """Die fertige Navigation für die Vorlage."""
    endpoint = request.endpoint or ''
    aktiv = aktiver_bereich(endpoint)
    bereiche = []
    unterpunkte = []
    for schluessel, label, symbol, ziel, punkte in BEREICHE:
        bereiche.append({
            'key': schluessel, 'label': label, 'icon': symbol,
            'url': url_for(ziel), 'aktiv': schluessel == aktiv,
        })
        if schluessel == aktiv:
            unterpunkte = [
                {
                    'label': punkt[0], 'url': url_for(punkt[1]),
                    'aktiv': any(endpoint.startswith(p) for p in punkt[2]),
                }
                for punkt in punkte
                if len(punkt) < 4 or getattr(current_user, punkt[3], False)
            ]
    return {'bereiche': bereiche, 'unterpunkte': unterpunkte, 'aktiv': aktiv}
