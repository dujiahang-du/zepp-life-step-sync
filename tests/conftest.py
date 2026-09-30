import pytest
from app import create_app, db
from app.models import User


class FakeMotion:
    calls = []
    result = ('Zepp 已接受模拟提交。', True)

    def __init__(self, user, password, tokens=None):
        self.user, self.password = user, password
        self.tokens = tokens or {'app_token': 'fixture-token', 'user_id': 'fixture-user'}

    def validate_credentials(self):
        self.calls.append(('validate', self.user))
        return self.tokens

    def sync_step(self, steps):
        self.calls.append(('sync', steps))
        return self.result

    def close(self):
        pass


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('真实外部网络在回归测试中被禁止。')
    monkeypatch.setattr('requests.sessions.Session.request', forbidden)
    FakeMotion.calls = []
    FakeMotion.result = ('Zepp 已接受模拟提交。', True)


@pytest.fixture
def app(tmp_path):
    instance = tmp_path / 'instance'
    app = create_app({'TESTING': True, 'INSTANCE_PATH': str(instance),
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:', 'MOTION_CLIENT_FACTORY': FakeMotion})
    yield app
    app.extensions['motion_executor'].shutdown(wait=True)
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


def csrf(client):
    client.get('/login')
    with client.session_transaction() as session:
        return session['csrf_token']


@pytest.fixture
def signed_in(app, client):
    with app.app_context():
        user = User(username='test_user', email='test@example.invalid')
        user.set_password('test-password-123')
        db.session.add(user)
        db.session.commit()
        ident = user.id
    with client.session_transaction() as session:
        session['_user_id'] = str(ident)
        session['_fresh'] = True
        session['csrf_token'] = 'fixture-csrf'
    return client


def add_account(client, **changes):
    values = {'mi_user': 'motion@example.invalid', 'mi_password': 'fixture-password',
              'min_step': '18000', 'max_step': '25000', 'sync_start_hour': '8',
              'sync_end_hour': '22', 'is_active': 'on', 'confirm_login': 'on', 'csrf_token': 'fixture-csrf'}
    values.update(changes)
    return client.post('/account/add', data=values)
