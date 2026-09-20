import base64
from io import BytesIO
import json
import zipfile

from studio.settings import Settings, SettingsUpdate
from studio.store import Store
from .conftest import fixture_image, wait_batch


def submit(client, **values):
    body = {'client_id': 'test-request-001', 'prompt': 'A ceramic cup', **values}
    response = client.post('/api/batches', json=body)
    assert response.status_code == 200
    return response.json()


def test_batch_count_download_history_and_delete(client):
    batch = submit(client, count=2)
    result = wait_batch(client, batch['id'])
    assert [task['status'] for task in result['tasks']] == ['success', 'success']
    assert len(client.get('/api/batches').json()['items']) == 1
    image = result['tasks'][0]['results'][0]
    assert client.get(image['url']).content.startswith(b'\x89PNG')
    archive = client.get(f'/api/batches/{batch["id"]}/download')
    with zipfile.ZipFile(BytesIO(archive.content)) as contents:
        assert len(contents.namelist()) == 3
    assert client.delete(f'/api/batches/{batch["id"]}').status_code == 200
    assert client.get('/api/batches').json()['items'] == []


def test_batch_is_idempotent_and_rejects_changed_content(client):
    first = submit(client)
    second = submit(client)
    assert first['tasks'][0]['id'] == second['tasks'][0]['id']
    response = client.post('/api/batches', json={'client_id': first['id'], 'prompt': 'changed'})
    assert response.status_code == 400
    wait_batch(client, first['id'])
    assert len(client.app.state.service.provider.calls) == 1


def test_partial_failure_does_not_abort_other_items(client):
    batch = submit(client, mode='queue', prompt='first\nFAIL_FIXTURE\nlast')
    result = wait_batch(client, batch['id'])
    assert [task['status'] for task in result['tasks']] == ['success', 'failed', 'success']


def test_upload_reference_and_derivative(client):
    upload = client.post('/api/uploads', files=[('files', ('reference.png', fixture_image(), 'image/png'))])
    asset = upload.json()['items'][0]
    assert client.post('/api/uploads', files=[('files', ('again.png', fixture_image(), 'image/png'))]).json()['items'][0]['id'] == asset['id']
    batch = submit(client, references=[asset['id']], derivative=True)
    wait_batch(client, batch['id'])
    prompt, references, model = client.app.state.service.provider.calls[0]
    assert '商品保真规则' in prompt
    assert references[0].startswith(b'\x89PNG')
    assert model == 'gpt-5-3'
    assert client.post('/api/uploads', files=[('files', ('bad.png', b'not an image', 'image/png'))]).status_code == 400


def test_cancel_never_claims_running_upstream_cancelled(client):
    batch = submit(client, prompt='SLOW_FIXTURE', count=3)
    response = client.post(f'/api/batches/{batch["id"]}/cancel').json()
    assert any(task['status'] == 'cancelled' for task in response['tasks'])
    assert all(task['status'] in {'running', 'success', 'cancelled'} for task in response['tasks'])


def test_restart_marks_unknown_work_and_does_not_replay(tmp_path):
    store = Store(tmp_path)
    with store.lock:
        store.connection.execute('INSERT INTO tasks VALUES (?,?,?)', ('fixture-id', 'fixture-batch', json.dumps({'status': 'running'})))
        store.connection.commit()
    second = Store(tmp_path)
    task = second.tasks()[0]
    assert task['status'] == 'interrupted'
    assert '核对网页' in task['error']


def test_credentials_encrypted_and_never_returned(client, tmp_path):
    fake_value = 'not-a-live-credential-fixture'
    settings = Settings(tmp_path)
    settings.update(SettingsUpdate(access_token=fake_value))
    assert fake_value.encode() not in (tmp_path / 'connection.enc').read_bytes()
    assert Settings(tmp_path).snapshot()['access_token'] == fake_value
    assert fake_value not in json.dumps(settings.public())
    response = client.post('/api/settings', json={'access_token': fake_value, 'upstream_model': 'bad / value'})
    assert response.status_code == 422
    assert fake_value not in response.text


def test_auth_and_csrf_boundaries(client):
    assert client.get('/api/batches', headers={'x-studio-session': ''}).status_code == 401
    assert client.post('/api/settings', json={}, headers={'origin': 'https://malicious.example'}).status_code == 403
    assert client.get('/api/bootstrap', headers={'sec-fetch-site': 'cross-site'}).status_code == 403
    assert client.get('/api/bootstrap', headers={'host': 'malicious.example'}).status_code == 403
    assert client.get('/v1/models').status_code == 401
    assert client.get('/files/../connection.enc').status_code != 200
    assert client.get('/unknown-file.json').headers['x-content-type-options'] == 'nosniff'


def api_headers(client):
    key = client.post('/api/local-api-key').json()['api_key']
    return {'Authorization': 'Bearer ' + key}


def test_openai_generation_and_aliases(client):
    headers = api_headers(client)
    models = client.get('/v1/models', headers=headers).json()['data']
    assert 'gpt-image-2.5' in [model['id'] for model in models]
    assert all(not model['actual_model_verified'] for model in models)
    response = client.post('/v1/images/generations', headers=headers, json={'prompt': 'fixture', 'n': 2})
    assert response.status_code == 200
    assert len(response.json()['data']) == 2
    assert base64.b64decode(response.json()['data'][0]['b64_json']).startswith(b'\x89PNG')


def test_openai_edit_stream_and_idempotency(client):
    headers = {**api_headers(client), 'Idempotency-Key': 'edit-fixture'}
    for _ in range(2):
        response = client.post('/v1/images/edits', headers=headers, data={'prompt': 'edit fixture', 'response_format': 'url', 'stream': 'true'}, files=[('image[]', ('ref.png', fixture_image(), 'image/png'))])
        assert response.status_code == 200
        assert 'data: [DONE]' in response.text
        assert 'image_generation.progress' in response.text
    assert len(client.app.state.service.provider.calls) == 1
