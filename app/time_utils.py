from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local_now():
    return datetime.now(BEIJING)


def local_time(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BEIJING)


def local_day_start(days_ago=0):
    value = (local_now() - timedelta(days=days_ago)).replace(hour=0, minute=0, second=0, microsecond=0)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def display_time(value, fmt='%m-%d %H:%M'):
    return local_time(value).strftime(fmt) if value else '尚未执行'
