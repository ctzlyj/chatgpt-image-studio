import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / 'skills' / 'chatgpt-web-image' / 'scripts' / 'image_studio_client.py'
SPEC = importlib.util.spec_from_file_location('image_studio_client', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_skill_client_only_accepts_loopback_urls():
    assert MODULE.validate_base_url('http://127.0.0.1:8765') == 'http://127.0.0.1:8765'
    assert MODULE.validate_base_url('http://localhost:9000/') == 'http://localhost:9000'
    for value in ['https://127.0.0.1:8765', 'http://example.test:8765', 'http://user:pass@127.0.0.1:8765']:
        with pytest.raises(MODULE.StudioError):
            MODULE.validate_base_url(value)


def test_skill_client_finds_surrounding_project():
    assert MODULE.find_project_root() == Path(__file__).parents[1].resolve()


def test_configure_never_returns_or_prints_the_session(monkeypatch):
    secret = 'synthetic-session-value-not-a-token'
    observed = {}
    client = MODULE.StudioClient()
    monkeypatch.setattr(client, 'page_headers', lambda: {'x-studio-session': 'fixture-page-session', 'Content-Type': 'application/json'})

    def request(method, path, payload=None, headers=None, timeout=30):
        observed['payload'] = json.loads(payload)
        return {'added': 1, 'duplicates': 0, 'items': [{'name': 'fixture'}]}

    monkeypatch.setattr(client, 'request', request)
    result = client.configure(json.dumps({'accessToken': secret}))
    assert observed['payload']['content'].endswith(secret + '"}')
    assert secret not in json.dumps(result)
    assert result == {'added': 1, 'duplicates': 0, 'accounts': 1}


def test_save_images_is_atomic_and_returns_absolute_paths(tmp_path):
    image = b'\x89PNG\r\n\x1a\nfixture'
    result = MODULE.save_images({'batch_id': 'batch-fixture', 'data': [{'b64_json': MODULE.base64.b64encode(image).decode()}]}, tmp_path, 'request-fixture')
    assert result['batch_id'] == 'batch-fixture'
    assert result['request_id'] == 'request-fixture'
    assert Path(result['images'][0]).is_absolute()
    assert Path(result['images'][0]).read_bytes() == image
    assert not list(tmp_path.glob('*.tmp'))


def test_status_is_redacted(monkeypatch):
    client = MODULE.StudioClient()
    monkeypatch.setattr(client, 'page_headers', lambda: {'x-studio-session': 'fixture'})
    monkeypatch.setattr(client, 'request', lambda *args, **kwargs: {'items': [{'name': '账号一', 'status': 'ready', 'quota': 4, 'enabled': True}]})
    result = client.status()
    assert result['accounts'] == 1
    assert result['ready'] == 1
    assert 'access_token' not in json.dumps(result)
