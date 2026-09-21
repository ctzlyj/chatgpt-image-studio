import json
import time
from unittest.mock import patch

import pytest

from studio.account_sources import RemoteSource, SourceEdit, remote_json, validate_source_url


class Response:
    def __init__(self, value=None, status=200):
        self.status_code = status
        self.value = value if value is not None else {}
        self.closed = False

    def iter_content(self, chunk_size):
        yield json.dumps(self.value).encode()

    def close(self):
        self.closed = True


class Session:
    def __init__(self, **kwargs):
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith('/auth/login'):
            return Response({'code': 0, 'data': {'access_token': 'synthetic-admin-session'}})
        if url.endswith('/auth-files'):
            return Response({'files': [{'name': 'fixture.json', 'email': 'fixture@example.test', 'access_token': 'DO_NOT_RETURN'}]})
        if url.endswith('/auth-files/download'):
            return Response({'access_token': 'synthetic-import-fixture'})
        if url.endswith('/admin/groups'):
            return Response({'code': 0, 'data': {'items': [{'id': 5, 'name': 'fixture-group'}], 'total': 1}})
        if url.endswith('/admin/accounts'):
            return Response({'code': 0, 'data': {'items': [{'id': 12, 'name': 'fixture', 'platform': 'openai', 'type': 'oauth', 'credentials': {'access_token': 'DO_NOT_RETURN', 'email': 'fixture@example.test'}}, {'id': 13, 'platform': 'other', 'type': 'oauth'}], 'total': 2}})
        return Response({'code': 0, 'data': {'id': 12, 'platform': 'openai', 'type': 'oauth', 'credentials': {'access_token': 'synthetic-import-fixture'}}})

    def close(self):
        self.closed = True


@pytest.mark.parametrize('url', ['https://example.test', 'https://example.test/prefix', 'http://127.0.0.1:8080'])
def test_source_url_supports_https_and_local_server(url):
    assert validate_source_url(url) == url


@pytest.mark.parametrize('url', ['http://example.test', 'https://user:pass@example.test', 'https://example.test?token=private', 'https://example.test#fragment', 'file:///fixture'])
def test_source_url_rejects_credential_leaks(url):
    with pytest.raises(ValueError):
        validate_source_url(url)


@pytest.mark.parametrize('kind', ['cpa', 'sub2api'])
def test_remote_management_protocol_never_returns_tokens(kind):
    source = {'kind': kind, 'base_url': 'https://example.test', 'secret': 'synthetic-management-key', 'group': '5'}
    with patch('studio.account_sources.requests.Session', Session):
        with RemoteSource(source) as remote:
            items = remote.list_items()
            assert len(items) == 1
            assert 'DO_NOT_RETURN' not in json.dumps(items)
            assert remote.token(items[0]['id']) == 'synthetic-import-fixture'
            for method, url, options in remote.session.calls:
                assert options['allow_redirects'] is False
                assert url.startswith('https://example.test/')
                assert 'synthetic-management-key' in options['headers'].values() or options['headers'].get('Authorization') == 'Bearer synthetic-management-key'
            if kind == 'sub2api':
                assert remote.session.calls[0][2]['params']['group'] == '5'
                assert remote.list_items(groups=True)[0]['id'] == '5'
        assert remote.session.closed


def test_sub2api_password_login_is_scoped_to_source_and_secret_safe():
    source = {'kind': 'sub2api', 'base_url': 'https://example.test', 'secret': '', 'email': 'fixture@example.test', 'password': 'synthetic-password'}
    with patch('studio.account_sources.requests.Session', Session):
        with RemoteSource(source) as remote:
            items = remote.list_items()
            assert len(items) == 1
            assert remote.session.calls[0][0] == 'POST'
            assert remote.session.calls[1][2]['headers']['Authorization'] == 'Bearer synthetic-admin-session'


@pytest.mark.parametrize('status', [302, 401, 403, 500])
def test_remote_failures_and_redirects_do_not_echo_body(status):
    session = Session()
    response = Response({'private': 'DO_NOT_RETURN'}, status)
    with patch.object(session, 'request', return_value=response) as request:
        with pytest.raises(ValueError) as failure:
            remote_json(session, 'GET', 'https://example.test')
        assert 'DO_NOT_RETURN' not in str(failure.value)
        assert request.call_count == 1
        assert request.call_args.kwargs['allow_redirects'] is False
    assert response.closed


def test_remote_exceptions_never_echo_request_credentials():
    with patch.object(Session, 'request', side_effect=ValueError('PRIVATE_KEY_FIXTURE')):
        with pytest.raises(ValueError) as failure:
            remote_json(Session(), 'GET', 'https://example.test')
        assert 'PRIVATE_KEY_FIXTURE' not in str(failure.value)


def test_source_crud_requires_fresh_secret_when_destination_changes(client):
    body = {'kind': 'cpa', 'name': 'fixture server', 'base_url': 'https://example.test', 'secret': 'synthetic-management-key'}
    response = client.post('/api/account-sources', json=body)
    assert response.status_code == 200
    assert 'synthetic-management-key' not in response.text
    source_id = response.json()['items'][0]['id']
    assert b'synthetic-management-key' not in client.app.state.settings.path.read_bytes()
    changed = dict(body, base_url='https://other.test', secret=None)
    assert client.post(f'/api/account-sources/{source_id}', json=changed).status_code == 400
    assert client.post(f'/api/account-sources/{source_id}', json=dict(body, name='renamed', secret=None)).status_code == 200
    assert client.delete(f'/api/account-sources/{source_id}').status_code == 200


def test_background_import_deduplicates_and_isolates_item_failures(client):
    sources = client.app.state.sources
    source_id = sources.save(SourceEdit(kind='cpa', name='fixture', base_url='https://example.test', secret='synthetic-management-key'))['items'][0]['id']
    def token(remote, remote_id):
        if remote_id == 'bad':
            raise RuntimeError('PRIVATE_REMOTE_RESPONSE')
        return 'synthetic-import-fixture'
    with patch('studio.account_sources.requests.Session', Session), patch.object(RemoteSource, 'token', token):
        job = sources.start_import(source_id, ['good', 'bad', 'duplicate'])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = next(item for item in sources.public()['jobs'] if item['id'] == job['id'])
            if current['status'] == 'completed':
                break
            time.sleep(0.01)
    assert current['status'] == 'completed'
    assert current['done'] == 3 and current['added'] == 1 and current['failed'] == 1 and current['duplicates'] == 1
    assert 'PRIVATE_REMOTE_RESPONSE' not in json.dumps(current)
    assert len(client.app.state.pool.public()['items']) == 2


def test_interrupted_import_receipt_survives_restart_without_replay(client):
    from studio.account_sources import AccountSources
    settings = client.app.state.settings
    snapshot = settings.snapshot()
    snapshot['import_jobs'] = [{'id': 'synthetic-job', 'source_id': 'synthetic-source', 'status': 'running', 'total': 5, 'done': 2, 'added': 2, 'duplicates': 0, 'failed': 0, 'errors': []}]
    settings.commit(snapshot)
    sources = AccountSources(settings, client.app.state.pool)
    try:
        job = sources.public()['jobs'][0]
        assert job['status'] == 'interrupted'
        assert job['done'] == 2
        assert settings.snapshot()['import_jobs'][0]['status'] == 'interrupted'
    finally:
        sources.close()


def test_sub2api_repeated_pagination_is_rejected():
    response = Response({'items': [{'id': number, 'platform': 'openai', 'type': 'oauth'} for number in range(200)], 'total': 400})
    with patch('studio.account_sources.requests.Session', Session), patch.object(Session, 'request', return_value=response):
        with RemoteSource({'kind': 'sub2api', 'base_url': 'https://example.test', 'secret': 'synthetic-management-key'}) as remote:
            with pytest.raises(ValueError, match='重复'):
                remote.list_items()
