from datetime import UTC, datetime


def utc_now():
    """
    UTC-Zeit ohne `datetime.utcnow()` (deprecated).
    Bewusst als naive UTC zurückgegeben für Kompatibilität mit bestehendem Schema/Code.
    """
    return datetime.now(UTC).replace(tzinfo=None)
