import hmac
import secrets

from flask import abort, request, session
from markupsafe import Markup


def get_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


def rotate_csrf_token():
    session['_csrf_token'] = secrets.token_urlsafe(32)


def clear_csrf_token():
    session.pop('_csrf_token', None)


def register_csrf(app):
    @app.context_processor
    def inject_csrf_helpers():
        def csrf_input():
            token = get_csrf_token()
            return Markup(f'<input type="hidden" name="_csrf_token" value="{token}">')

        return {'csrf_input': csrf_input}

    @app.before_request
    def validate_csrf_for_post():
        if request.method != 'POST':
            return

        sent_token = request.form.get('_csrf_token', '')
        session_token = session.get('_csrf_token', '')

        if not sent_token or not session_token or not hmac.compare_digest(sent_token, session_token):
            abort(400, description='Ungueltiger CSRF-Token')
