"""SQLite 增量升级；升级前保留可恢复快照。"""
import sqlite3
from datetime import datetime
from pathlib import Path
from sqlalchemy import inspect, text
from app import db
from app.security import PREFIX, encrypt, decrypt


def upgrade(app):
    inspector = inspect(db.engine)
    additions = {
        'mi_account': {'token_data': 'TEXT', 'sync_lock_until': 'DATETIME', 'sync_lock_token': 'VARCHAR(36)', 'last_scheduled_slot': 'VARCHAR(16)'},
        'step_record': {'source': "VARCHAR(16) DEFAULT 'manual'"},
    }
    pending = []
    for table, fields in additions.items():
        if inspector.has_table(table):
            existing = {c['name'] for c in inspector.get_columns(table)}
            pending.extend((table, name, kind) for name, kind in fields.items() if name not in existing)
    plain_count = 0
    if inspector.has_table('mi_account'):
        duplicates = db.session.execute(text('SELECT user_id, mi_user FROM mi_account GROUP BY user_id, mi_user HAVING COUNT(*) > 1 LIMIT 1')).first()
        if duplicates:
            raise RuntimeError('存在同一用户下重复的运动账号，请先在旧版本中合并重复账号，再运行升级。')
        for row in db.session.execute(text('SELECT mi_password FROM mi_account')):
            if row[0].startswith(PREFIX):
                decrypt(row[0])
            else:
                plain_count += 1
    if pending or plain_count:
        if db.engine.dialect.name != 'sqlite':
            raise RuntimeError('已有非 SQLite 数据库需要先执行受控迁移，请参阅 README。')
        filename = db.engine.url.database
        if filename and filename != ':memory:' and not app.testing:
            folder = Path(app.instance_path) / 'backups'
            folder.mkdir(exist_ok=True)
            dest = folder / ('before-upgrade-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.db')
            with sqlite3.connect(filename) as source, sqlite3.connect(dest) as target:
                source.backup(target)
        # 只使用程序内定义的表名与字段名，用户输入不参与 DDL。
        with db.engine.begin() as connection:
            for table, name, kind in pending:
                connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {kind}'))
            if plain_count:
                rows = connection.execute(text('SELECT id, mi_password FROM mi_account')).all()
                for ident, password in rows:
                    if not password.startswith(PREFIX):
                        connection.execute(text('UPDATE mi_account SET mi_password=:password WHERE id=:id'), {'password': encrypt(password), 'id': ident})
    db.session.remove()
    db.create_all()
    if db.engine.dialect.name == 'sqlite':
        with db.engine.begin() as connection:
            connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_owner_motion_account ON mi_account (user_id, mi_user)'))
