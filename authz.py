from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user, login_required


def admin_required(redirect_endpoint='system.index', message='Zugriff verweigert.'):
    """
    Kombiniert Login-Pflicht mit einer einfachen Admin-Prüfung über den Benutzernamen.
    Beibehaltung der bestehenden Rollenlogik ohne DB-Schema-Änderung.
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.username != 'admin':
                if message:
                    flash(message)
                return redirect(url_for(redirect_endpoint))
            return view_func(*args, **kwargs)

        return wrapped

    return decorator
