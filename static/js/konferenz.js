/*
 * Autosave der Förderkonferenz.
 *
 * Jede Eingabe wird für sich gespeichert: entprellt nach 800 ms und zusätzlich
 * sofort bei Feldwechsel, beim Klick auf einen Link und beim Verlassen der
 * Seite. Was nicht durchkommt, wandert in eine Warteschlange im lokalen
 * Speicher und wird später erneut versucht - ein Absturz oder ein wackelndes
 * WLAN kostet keine Eingabe.
 *
 * Markup: ein Bedienelement mit data-feld="..." und data-url="..." wird
 * gespeichert. data-bearbeitet-am trägt den Stand, den dieses Gerät kennt -
 * daraus erkennt der Server, ob jemand anderes dieselbe Zeile geändert hat.
 */
(function () {
    'use strict';

    var ENTPRELLUNG = 800;
    var SCHLANGE = 'kk-konferenz-warteschlange-v1';
    var WIEDERHOLUNG = 5000;

    var status = document.getElementById('speicher-status');
    var csrf = document.querySelector('meta[name="csrf-token"]');
    var token = csrf ? csrf.getAttribute('content') : '';
    var offen = {};          // Schlüssel -> Timer
    var unterwegs = 0;
    var letzterKonflikt = null;

    function schlange() {
        try {
            return JSON.parse(window.localStorage.getItem(SCHLANGE) || '[]');
        } catch (e) {
            return [];
        }
    }

    function schlangeSetzen(liste) {
        try {
            window.localStorage.setItem(SCHLANGE, JSON.stringify(liste.slice(-200)));
        } catch (e) { /* voller oder gesperrter Speicher: dann eben nicht */ }
    }

    function anzeigen() {
        if (!status) { return; }
        var wartend = schlange().length;
        if (unterwegs > 0) {
            status.textContent = 'Speichert …';
            status.className = 'speicher-status speichert';
        } else if (wartend > 0) {
            status.textContent = 'Nicht gespeichert (' + wartend + ') – wird erneut versucht';
            status.className = 'speicher-status fehler';
        } else {
            var zeit = new Date();
            var hh = String(zeit.getHours()).padStart(2, '0');
            var mm = String(zeit.getMinutes()).padStart(2, '0');
            status.textContent = 'Gespeichert ' + hh + ':' + mm;
            status.className = 'speicher-status fertig';
        }
        if (letzterKonflikt && status) {
            status.textContent += ' · ' + letzterKonflikt;
        }
    }

    function offeneAenderungen() {
        return unterwegs > 0 || schlange().length > 0 || Object.keys(offen).length > 0;
    }

    function senden(auftrag, ausSchlange) {
        unterwegs += 1;
        anzeigen();
        return fetch(auftrag.url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
            body: JSON.stringify({feld: auftrag.feld, wert: auftrag.wert, bekannt_am: auftrag.bekannt_am,
                                  user_id: auftrag.user_id}),
            credentials: 'same-origin',
            keepalive: true
        }).then(function (antwort) {
            if (!antwort.ok) { throw new Error('Status ' + antwort.status); }
            return antwort.json();
        }).then(function (daten) {
            unterwegs -= 1;
            if (ausSchlange) {
                schlangeSetzen(schlange().filter(function (e) { return e.id !== auftrag.id; }));
            }
            if (daten && daten.bearbeitet_am) {
                document.querySelectorAll('[data-url="' + auftrag.url + '"]').forEach(function (feld) {
                    feld.setAttribute('data-bearbeitet-am', daten.bearbeitet_am);
                });
            }
            if (daten && daten.konflikt) {
                letzterKonflikt = 'Achtung: Dieselbe Zeile wurde gleichzeitig woanders geändert.';
            }
            anzeigen();
        }).catch(function () {
            unterwegs -= 1;
            if (!ausSchlange) {
                var liste = schlange();
                liste.push(auftrag);
                schlangeSetzen(liste);
            }
            anzeigen();
        });
    }

    function auftragAus(feld) {
        var wert;
        if (feld.type === 'checkbox') {
            wert = feld.checked ? '1' : '';
        } else if (feld.type === 'radio') {
            wert = feld.checked ? feld.value : '';
        } else {
            wert = feld.value;
        }
        return {
            id: String(Date.now()) + '-' + Math.random().toString(36).slice(2, 8),
            url: feld.getAttribute('data-url'),
            feld: feld.getAttribute('data-feld'),
            wert: wert,
            bekannt_am: feld.getAttribute('data-bearbeitet-am') || null,
            user_id: feld.getAttribute('data-user-id') || null
        };
    }

    function schluessel(feld) {
        return feld.getAttribute('data-url') + '|' + feld.getAttribute('data-feld');
    }

    function planen(feld) {
        var key = schluessel(feld);
        if (offen[key]) { window.clearTimeout(offen[key]); }
        offen[key] = window.setTimeout(function () {
            delete offen[key];
            senden(auftragAus(feld), false);
        }, ENTPRELLUNG);
    }

    function sofort(feld) {
        var key = schluessel(feld);
        if (offen[key]) {
            window.clearTimeout(offen[key]);
            delete offen[key];
        }
        senden(auftragAus(feld), false);
    }

    function alleSofort() {
        Object.keys(offen).forEach(function (key) {
            window.clearTimeout(offen[key]);
            delete offen[key];
        });
        document.querySelectorAll('[data-feld][data-url]').forEach(function (feld) {
            if (feld.dataset.geaendert === '1') {
                feld.dataset.geaendert = '';
                senden(auftragAus(feld), false);
            }
        });
    }

    function schlangeAbarbeiten() {
        var liste = schlange();
        if (!liste.length || unterwegs > 0) { return; }
        senden(liste[0], true);
    }

    document.addEventListener('input', function (ereignis) {
        var feld = ereignis.target.closest('[data-feld][data-url]');
        if (!feld || feld.type === 'checkbox' || feld.type === 'radio') { return; }
        feld.dataset.geaendert = '1';
        planen(feld);
    });

    document.addEventListener('change', function (ereignis) {
        var feld = ereignis.target.closest('[data-feld][data-url]');
        if (!feld) { return; }
        feld.dataset.geaendert = '';
        sofort(feld);
    });

    document.addEventListener('blur', function (ereignis) {
        var feld = ereignis.target.closest ? ereignis.target.closest('[data-feld][data-url]') : null;
        if (!feld || feld.dataset.geaendert !== '1') { return; }
        feld.dataset.geaendert = '';
        sofort(feld);
    }, true);

    // Vor dem Verlassen der Seite alles wegschicken (keepalive überlebt den Wechsel).
    document.addEventListener('click', function (ereignis) {
        var ziel = ereignis.target.closest('a[href], button[type="submit"]');
        if (ziel) { alleSofort(); }
    }, true);

    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') { alleSofort(); }
    });

    window.addEventListener('beforeunload', function (ereignis) {
        alleSofort();
        if (offeneAenderungen()) {
            ereignis.preventDefault();
            ereignis.returnValue = '';
            return '';
        }
        return undefined;
    });

    window.addEventListener('online', schlangeAbarbeiten);
    window.setInterval(schlangeAbarbeiten, WIEDERHOLUNG);
    schlangeAbarbeiten();
    anzeigen();

    // ------------------------------------------------------------------
    // Tastatur: A/B/C setzt die Stufe, Pfeile wechseln das Kind.
    // ------------------------------------------------------------------
    document.addEventListener('keydown', function (ereignis) {
        var tag = (ereignis.target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select' || ereignis.target.isContentEditable) { return; }
        if (ereignis.ctrlKey || ereignis.metaKey || ereignis.altKey) { return; }
        var taste = ereignis.key.toLowerCase();
        if (['a', 'b', 'c'].indexOf(taste) !== -1) {
            var knopf = document.querySelector('[data-stufe-taste="' + taste.toUpperCase() + '"]');
            if (knopf) {
                ereignis.preventDefault();
                knopf.click();
            }
            return;
        }
        if (taste === 'arrowright' || taste === 'arrowleft') {
            var link = document.querySelector(taste === 'arrowright' ? '[data-naechstes]' : '[data-voriges]');
            if (link) {
                ereignis.preventDefault();
                alleSofort();
                window.location.href = link.getAttribute('href');
            }
        }
    });
}());
