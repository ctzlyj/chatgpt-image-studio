import json

import pytest

from studio.settings import Settings, SettingsUpdate


@pytest.mark.parametrize('field', ['accessToken', 'access_token'])
@pytest.mark.parametrize('pretty', [False, True])
def test_whole_session_json_extracts_only_token(tmp_path, field, pretty):
    settings = Settings(tmp_path)
    payload = {field: 'synthetic-session-fixture', 'user': {'name': 'PRIVATE_FIXTURE_NAME', 'email': 'private-fixture@example.test'}, 'expires': '2099-01-01T00:00:00Z'}
    result = settings.update(SettingsUpdate(access_token=json.dumps(payload, indent=2 if pretty else None)))
    persisted = json.loads(settings._decode(settings.path.read_bytes()))
    assert result['configured'] is True
    assert Settings(tmp_path).snapshot()['access_token'] == payload[field]
    assert persisted['access_token'] == payload[field]
    assert 'PRIVATE_FIXTURE_NAME' not in json.dumps(persisted)
    assert 'private-fixture@example.test' not in json.dumps(persisted)
    assert 'expires' not in persisted and 'user' not in persisted
    assert payload[field] not in json.dumps(result)
    assert payload[field].encode() not in settings.path.read_bytes()


@pytest.mark.parametrize('value', [' synthetic-token-fixture ', 'Bearer synthetic-token-fixture', 'bearer synthetic-token-fixture'])
def test_bare_token_and_bearer_remain_supported(tmp_path, value):
    settings = Settings(tmp_path)
    settings.update(SettingsUpdate(access_token=value))
    assert settings.snapshot()['access_token'] == 'synthetic-token-fixture'
    settings.update(SettingsUpdate(access_token='   '))
    assert settings.snapshot()['access_token'] == 'synthetic-token-fixture'


@pytest.mark.parametrize('value', [
    '{"user":{"name":"PRIVATE_FIXTURE_NAME"}}',
    '{"accessToken":"synthetic-session-fixture",',
    '{"accessToken":null}',
    '{"accessToken":123}',
    '{"accessToken":""}',
    '{"accessToken":"Bearer "}',
    '{"accessToken":"contains whitespace"}',
    '{"accessToken":"first-fixture","access_token":"other-fixture"}',
    '[{"accessToken":"synthetic-session-fixture"}]',
    'null',
    '{' + ' ' * 262145 + '}',
    json.dumps({'accessToken': 'fixture' * 3000}),
], ids=['missing-token', 'malformed-json', 'null-token', 'numeric-token', 'empty-token', 'empty-bearer', 'token-whitespace', 'conflicting-tokens', 'array', 'null-json', 'oversized-json', 'oversized-token'])
def test_invalid_session_keeps_previous_connection_and_uses_safe_error(client, value):
    before = client.app.state.settings.snapshot()
    before_file = client.app.state.settings.path.read_bytes()
    response = client.post('/api/settings', json={'access_token': value, 'upstream_model': 'changed-model'})
    assert response.status_code == 400
    assert client.app.state.settings.snapshot() == before
    assert client.app.state.settings.path.read_bytes() == before_file
    assert 'PRIVATE_FIXTURE_NAME' not in response.text
    assert 'synthetic-session-fixture' not in response.text
    assert '请只填写' not in response.text
