import tempfile
from pathlib import Path
from fastapi.testclient import TestClient

from greenhouse.app import create_app
from greenhouse.config import Config


def test_auth_and_endpoints():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config(password='the-test-password', db_path=str(Path(tmp) / 'd.sqlite'))
        app = create_app(cfg)
        with TestClient(app) as client:
            assert client.get('/api/state').status_code == 401
            kw = {'auth': ('operator', cfg.password)}
            data = client.get('/api/state', **kw).json()
            assert data['mode'] == 'MANUAL'
            assert data['quality'] == 'OK'
            assert client.post('/api/relay/8', json={'on':True}, **kw).status_code == 200
            assert client.get('/api/state', **kw).json()['io']['relays'][7]
            assert client.post('/api/mode', json={'mode':'AUTO'}, **kw).status_code == 200
            assert client.post('/api/relay/1', json={'on':True}, **kw).status_code == 403
            assert 'AI_1_raw' in client.get('/api/export.csv', **kw).text
