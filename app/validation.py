import re


def normalize_account(value):
    value = (value or '').strip()
    if re.fullmatch(r'(?:\+86)?1\d{10}', value):
        return value if value.startswith('+86') else '+86' + value
    if len(value) <= 120 and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        return value
    raise ValueError('请填写 Zepp Life 的手机号或邮箱，不是本网站用户名或小米账号。')


def account_settings(form):
    errors, values = {}, {}
    for field, label, lower, upper in (
        ('min_step', '最小步数', 0, 98800), ('max_step', '最大步数', 1, 98800),
        ('sync_start_hour', '开始时间', 0, 23), ('sync_end_hour', '结束时间', 0, 23),
    ):
        try:
            value = int(form.get(field, ''))
            if not lower <= value <= upper:
                raise ValueError()
            values[field] = value
        except (ValueError, TypeError):
            errors[field] = f'{label}请输入 {lower}–{upper} 之间的整数。'
    if not errors:
        if values['min_step'] > values['max_step']:
            errors['max_step'] = '最大步数不能小于最小步数。'
        if values['sync_start_hour'] > values['sync_end_hour']:
            errors['sync_end_hour'] = '结束时间不能早于开始时间，暂不支持跨午夜计划。'
    return values, errors
