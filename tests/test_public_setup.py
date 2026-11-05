# File: tests/test_public_setup.py
# Summary: Verify deploy-domain path safety and compatibility of the generated administrator seed with the real API.

import json
from pathlib import Path
import runpy

from fastapi.testclient import TestClient
import pytest

from service import storage as store
from service.app import app

ROOT = Path(__file__).resolve().parent.parent
require_domain = runpy.run_path(str(ROOT / 'ops/domain.py'))['require_domain']
create_seed = runpy.run_path(str(ROOT / 'ops/init-admin.py'))['create_seed']


@pytest.mark.parametrize('domain', ['', 'meet.example.com', '../invalid/path', 'bad..example'])
def test_deployment_rejects_missing_example_or_unsafe_domain(monkeypatch, domain):
    monkeypatch.setenv('JITSI_DOMAIN', domain)
    with pytest.raises(ValueError):
        require_domain()


def test_deployment_normalizes_a_valid_domain(monkeypatch):
    monkeypatch.setenv('JITSI_DOMAIN', 'Meet.Test.Example.')
    assert require_domain() == 'meet.test.example'


def test_generated_seed_authenticates_without_storing_plaintext(tmp_path, monkeypatch):
    password = 'test-only-initial-password'
    seed = tmp_path / 'admin-seed.json'
    create_seed(seed, password)
    assert password not in seed.read_text()
    with pytest.raises(FileExistsError):
        create_seed(seed, 'different-test-password')
    monkeypatch.setattr(store, 'DATA', tmp_path / 'state')
    monkeypatch.setattr(store, 'RECORDINGS', tmp_path / 'recordings')
    monkeypatch.setattr(store, 'ORIGIN', 'https://testserver')
    store.init()
    store.save_settings({**store.DEFAULTS, **json.loads(seed.read_text())})
    with TestClient(app, base_url=store.ORIGIN) as client:
        client.headers['Origin'] = store.ORIGIN
        assert client.post('/ai/api/admin/login', json={'username': 'ubuntu', 'password': password}).status_code == 200
        assert client.get('/ai/api/admin/settings').status_code == 200
        assert client.post('/ai/api/admin/login', json={'username': 'ubuntu', 'password': 'incorrect'}).status_code == 401
