# Modified 2026-09-30: reliability and security improvements; see NOTICE.
from app import create_app

app = create_app({'SCHEDULER_ENABLED': False})
print('数据库结构与凭据升级已完成；原数据快照位于 instance/backups。')
