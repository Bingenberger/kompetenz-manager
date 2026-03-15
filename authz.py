from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user, login_required


def admin_required(redirect_endpoint='system.index', message='Zugriff verweigert.'):
    """
    Kombiniert Login-Pflicht mit einer Admin-Prüfung über das Rollenfeld.
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped(*args, **kwargs):
            if not getattr(current_user, 'is_admin', False):
                if message:
                    flash(message)
                return redirect(url_for(redirect_endpoint))
            return view_func(*args, **kwargs)

        return wrapped

    return decorator
