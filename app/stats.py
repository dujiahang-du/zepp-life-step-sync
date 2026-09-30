from datetime import timedelta
from app import db
from app.models import MiAccount, StepRecord
from app.time_utils import local_day_start, local_now, local_time, utcnow


def statistics(user_id, account_id=None):
    accounts = MiAccount.query.filter_by(user_id=user_id).order_by(MiAccount.id).all()
    if account_id is not None and account_id not in [a.id for a in accounts]:
        return None
    selected = account_id or (accounts[0].id if accounts else None)
    dates = [(local_now().date() - timedelta(days=i)) for i in range(6, -1, -1)]
    buckets = {d: {'steps': None, 'total': 0, 'ok': 0} for d in dates}
    records = []
    if selected:
        records = StepRecord.query.filter(StepRecord.account_id == selected,
            StepRecord.created_at >= local_day_start(6), StepRecord.created_at <= utcnow()).order_by(StepRecord.created_at).all()
    for record in records:
        if record.outcome in ('requires_auth', 'unknown', 'skipped'):
            continue
        bucket = buckets.get(local_time(record.created_at).date())
        if bucket is not None:
            bucket['total'] += 1
            bucket['ok'] += int(record.status)
            if record.status:
                bucket['steps'] = record.step_count
    steps = [buckets[d]['steps'] for d in dates]
    rates = [round(buckets[d]['ok'] / buckets[d]['total'] * 100, 1) if buckets[d]['total'] else None for d in dates]
    return {'accounts': accounts, 'selected': selected, 'dates': [d.strftime('%m-%d') for d in dates],
            'steps': steps, 'success_rate': rates, 'has_data': any(s is not None for s in steps),
            'days': [{'date': d.strftime('%m-%d'), **buckets[d], 'rate': r} for d, r in zip(dates, rates)]}


def trend_points(stats):
    maximum = max([s for s in stats['steps'] if s is not None] or [1]) or 1
    segments, current, dots = [], [], []
    for index, step in enumerate(stats['steps']):
        if step is None:
            if current:
                segments.append(' '.join(current))
                current = []
            continue
        x, y = 32 + index * 78, 172 - step / maximum * 134
        current.append(f'{x:.1f},{y:.1f}')
        dots.append({'x': x, 'y': y, 'date': stats['dates'][index], 'step': step})
    if current:
        segments.append(' '.join(current))
    return {'segments': segments, 'dots': dots, 'maximum': maximum}
