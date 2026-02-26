from flask import abort

from extensions import db


def get_or_404_session(model, pk):
    obj = db.session.get(model, pk)
    if obj is None:
        abort(404)
    return obj
