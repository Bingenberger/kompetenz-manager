import os
import secrets

from app import app, db, User, Schueler, Bogen, Item
from werkzeug.security import generate_password_hash

# App-Kontext herstellen
with app.app_context():
    print("--- Starte Datenbank-Reparatur ---")
    
    # 1. ADMIN USER PRÜFEN
    # Wir suchen den Admin
    admin = User.query.filter_by(username='admin').first()
    
    if not admin:
        print("Erstelle Admin-User...")
        neues_passwort = os.environ.get("REPAIR_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
        pw_hash = generate_password_hash(neues_passwort)
        admin = User(username='admin', password_hash=pw_hash)
        db.session.add(admin)
    else:
        neues_passwort = os.environ.get("REPAIR_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
        print("Admin existiert bereits. Setze Passwort zurück auf ein temporäres Passwort...")
        admin.password_hash = generate_password_hash(neues_passwort)

    # ALLES SPEICHERN
    db.session.commit()
    print(f"Temporäres Admin-Passwort: {neues_passwort}")
    print("--- FERTIG! Alle Daten wurden gespeichert. ---")
