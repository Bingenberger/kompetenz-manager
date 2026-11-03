"""One nonblocking export slot shared by web workers and local jobs."""
import contextvars,fcntl,functools,os
from contextlib import contextmanager
from werkzeug.exceptions import TooManyRequests
_held=contextvars.ContextVar('km_export_held',default=False)
@contextmanager
def export_slot():
    if _held.get():yield;return
    fd=os.open('/tmp/km-export.lock',os.O_CREAT|os.O_RDWR,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise TooManyRequests('Dokumentexport belegt; später erneut versuchen.')
        token=_held.set(True)
        try:yield
        finally:_held.reset(token)
    finally:os.close(fd)
def bounded_export(function):
    @functools.wraps(function)
    def call(*args,**kwargs):
        with export_slot():return function(*args,**kwargs)
    return call
