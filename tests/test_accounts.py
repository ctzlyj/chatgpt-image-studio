import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from studio.accounts import AccountEdit, AccountPool, PoolOptions, parse_accounts
from studio.settings import Settings, SettingsUpdate
from studio.upstream.helper import UpstreamHTTPError
from .conftest import wait_batch


def inspector(values):
    return {'email': 'synthetic@example.test', 'plan': 'plus', 'quota': 4, 'restore_at': '2099-01-01T00:00:00Z'}


@pytest.fixture
def pool(tmp_path):
    return AccountPool(Settings(tmp_path), inspector)


def test_legacy_encrypted_connection_migrates_once_without_rotating_key(tmp_path):
    settings = Settings(tmp_path)
    settings.update(SettingsUpdate(access_token='legacy-fixture'))
    legacy = settings.snapshot()
    for key in ('accounts', 'pool', 'sources'):
        legacy.pop(key)
    settings.commit(legacy)
    migrated = Settings(tmp_path)
    accounts = migrated.snapshot()['accounts']
    assert len(accounts) == 1
    assert accounts[0]['access_token'] == 'legacy-fixture'
    assert migrated.snapshot()['api_key'] == legacy['api_key']
    assert Settings(tmp_path).snapshot()['accounts'][0]['id'] == accounts[0]['id']
    assert b'legacy-fixture' not in migrated.path.read_bytes()


@pytest.mark.parametrize('payload', [
    'first-fixture\nBearer second-fixture',
    '["first-fixture", "second-fixture"]',
    '{"accounts":[{"access_token":"first-fixture"},{"credentials":{"accessToken":"second-fixture"}}]}',
    '[{"accessToken":"first-fixture","user":{"name":"PRIVATE_PROFILE"}},{"access_token":"second-fixture"}]',
])
def test_import_formats_extract_only_tokens(pool, payload):
    result = pool.import_tokens(parse_accounts(payload))
    assert result['added'] == 2
    assert len(pool.public()['items']) == 2
    assert pool.import_tokens(parse_accounts(payload))['added'] == 0
    assert 'PRIVATE_PROFILE' not in json.dumps(pool.settings.snapshot())
    assert 'first-fixture' not in json.dumps(pool.public())
    assert 'access_token' not in json.dumps(pool.public())


@pytest.mark.parametrize('payload', ['{"accounts":["valid-fixture",{}]}', '{', '[]', '[null]', '{"access_token":123}'])
def test_invalid_import_is_atomic(client, payload):
    before = client.app.state.settings.path.read_bytes()
    response = client.post('/api/accounts/import', json={'content': payload})
    assert response.status_code == 400
    assert client.app.state.settings.path.read_bytes() == before
    assert 'valid-fixture' not in response.text


def test_pool_crud_dedup_config_and_credential_replacement(client):
    result = client.post('/api/accounts/import', json={'content': '{"accessToken":"other-fixture"}', 'name': '备用账号'}).json()
    assert result['added'] == 1
    account_id = result['items'][-1]['id']
    assert client.post(f'/api/accounts/{account_id}', json={'name': '新名称'}).status_code == 200
    assert client.post(f'/api/accounts/{account_id}', json={'access_token': 'offline-test-placeholder'}).status_code == 400
    assert client.post('/api/accounts/action', json={'ids': [account_id], 'action': 'disable'}).status_code == 200
    disabled = client.get('/api/accounts').json()['items'][-1]
    assert not disabled['enabled']
    assert client.post('/api/accounts/options', json={'concurrency': 3, 'per_account': 2, 'refresh_minutes': 0}).status_code == 200
    assert client.get('/api/accounts').json()['options']['concurrency'] == 3
    assert client.post('/api/accounts/options', json={'concurrency': 99}).status_code == 422
    assert client.post(f'/api/accounts/{account_id}', json={'access_token': '{"accessToken":"updated-fixture"}'}).status_code == 200
    assert client.post('/api/accounts/action', json={'ids': [account_id], 'action': 'delete'}).status_code == 200
    assert len(client.get('/api/accounts').json()['items']) == 1


def test_round_robin_limits_and_disabled_invalid_accounts(pool):
    imported = pool.import_tokens(['first-fixture', 'second-fixture', 'third-fixture'])
    account_ids = [account['id'] for account in imported['items']]
    used = []
    for unused in range(6):
        account_id, settings = pool.acquire(lambda: False)
        used.append(account_id)
        assert settings['access_token'] in {'first-fixture', 'second-fixture', 'third-fixture'}
        pool.release(account_id, True)
    assert used == account_ids * 2
    pool.action([account_ids[0]], 'disable')
    account_id, _ = pool.acquire(lambda: False)
    pool.release(account_id, False, UpstreamHTTPError('synthetic error', 401))
    for unused in range(3):
        next_id, _ = pool.acquire(lambda: False)
        assert next_id not in {account_ids[0], account_id}
        pool.release(next_id, True)


def test_simultaneous_work_respects_global_and_per_account_bounds(pool):
    pool.import_tokens(['first-fixture', 'second-fixture'])
    pool.options(PoolOptions(concurrency=2, per_account=1))
    counts, observed, lock = {}, [], threading.Lock()
    def worker():
        account_id, _ = pool.acquire(lambda: False)
        with lock:
            counts[account_id] = counts.get(account_id, 0) + 1
            observed.append((sum(counts.values()), max(counts.values())))
        time.sleep(0.02)
        with lock:
            counts[account_id] -= 1
        pool.release(account_id, True)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda unused: worker(), range(12)))
    assert max(total for total, per_account in observed) == 2
    assert max(per_account for total, per_account in observed) == 1
    assert sum(pool.inflight.values()) == 0
    assert sum(account['success'] for account in pool.public()['items']) == 12


def test_cancelled_waiter_never_acquires_and_busy_account_cannot_be_edited(pool):
    account_id = pool.import_tokens(['first-fixture'])['items'][0]['id']
    lease = pool.acquire(lambda: False)
    with pytest.raises(ValueError, match='执行'):
        pool.edit(account_id, AccountEdit(name='changed'))
    with pytest.raises(ValueError, match='执行'):
        pool.action([account_id], 'delete')
    with pytest.raises(ValueError, match='生图'):
        pool.refresh([account_id])
    assert pool.acquire(lambda: True) is None
    pool.release(lease[0], True)


def test_quota_refresh_and_limit_recovery_preserve_enabled(pool):
    account_id = pool.import_tokens(['first-fixture'])['items'][0]['id']
    pool.action([account_id], 'disable')
    assert pool.refresh([account_id])['refreshed'] == 1
    assert pool.public()['items'][0]['enabled'] is False
    pool.action([account_id], 'enable')
    lease = pool.acquire(lambda: False)
    pool.release(lease[0], False, UpstreamHTTPError('synthetic error', 429))
    with pytest.raises(ValueError, match='没有可用'):
        pool.acquire(lambda: False)
    assert pool.refresh([account_id])['items'][0]['status'] == 'ready'
    for unused in range(4):
        lease = pool.acquire(lambda: False)
        pool.release(lease[0], True)
    assert pool.public()['items'][0]['quota'] == 0
    with pytest.raises(ValueError, match='没有可用'):
        pool.acquire(lambda: False)


def test_refresh_error_and_periodic_refresh_are_secret_safe(pool):
    account_id = pool.import_tokens(['first-fixture'])['items'][0]['id']
    def failed(values):
        raise UpstreamHTTPError('PRIVATE_TOKEN_FIXTURE', 401)
    pool.inspector = failed
    result = pool.refresh([account_id])
    assert result['items'][0]['status'] == 'invalid'
    assert 'PRIVATE_TOKEN_FIXTURE' not in json.dumps(result)
    pool.edit(account_id, AccountEdit(access_token='updated-fixture'))
    pool.inspector = inspector
    with patch.object(pool.stopped, 'wait', side_effect=[False, True]), patch('studio.accounts.time.time', return_value=time.time() + 7200):
        pool._monitor()
    assert pool.public()['items'][0]['status'] == 'ready'


def test_backup_is_encrypted_round_trips_and_wrong_password_is_atomic(pool, tmp_path):
    account_id = pool.import_tokens(['backup-fixture'])['items'][0]['id']
    backup = pool.backup([account_id], 'synthetic-backup-password')
    assert 'backup-fixture' not in json.dumps(backup)
    other = AccountPool(Settings(tmp_path / 'other'), inspector)
    with pytest.raises(ValueError, match='口令'):
        other.restore(backup, 'wrong-password')
    assert other.public()['items'] == []
    assert other.restore(backup, 'synthetic-backup-password')['added'] == 1
    assert other.restore(backup, 'synthetic-backup-password')['duplicates'] == 1


def test_atomic_disk_failure_leaves_original_settings(pool):
    before = pool.settings.snapshot()
    before_file = pool.settings.path.read_bytes()
    with patch.object(pool.settings, '_save', side_effect=OSError('synthetic failure')):
        with pytest.raises(OSError):
            pool.import_tokens(['new-fixture'])
    assert pool.settings.snapshot() == before
    assert pool.settings.path.read_bytes() == before_file


def test_service_records_account_identity_without_token_and_never_retries(client):
    client.post('/api/accounts/import', json={'content': 'second-fixture'})
    response = client.post('/api/batches', json={'client_id': 'pool-failure-fixture', 'prompt': 'FAIL_FIXTURE', 'count': 1})
    assert response.status_code == 200
    batch = wait_batch(client, response.json()['id'])
    assert batch['tasks'][0]['status'] == 'failed'
    assert batch['tasks'][0]['account_id']
    assert len(client.app.state.service.provider.calls) == 1
    assert 'access_token' not in json.dumps(batch)
    assert 'offline-test-placeholder' not in json.dumps(batch)
    assert sum(client.app.state.pool.inflight.values()) == 0


def test_account_management_requires_browser_session(client):
    response = client.get('/api/accounts', headers={'x-studio-session': ''})
    assert response.status_code == 401


def test_refresh_reserves_account_until_quota_is_reconciled(pool):
    account_id = pool.import_tokens(['first-fixture'])['items'][0]['id']
    entered, finish = threading.Event(), threading.Event()
    def slow_inspector(values):
        entered.set()
        assert finish.wait(timeout=3)
        return inspector(values)
    pool.inspector = slow_inspector
    with ThreadPoolExecutor(max_workers=2) as executor:
        refreshing = executor.submit(pool.refresh, [account_id])
        assert entered.wait(timeout=2)
        waiting = executor.submit(pool.acquire, lambda: False)
        time.sleep(0.04)
        assert not waiting.done()
        finish.set()
        assert refreshing.result(timeout=3)['refreshed'] == 1
        lease = waiting.result(timeout=3)
    pool.release(lease[0], True)
    assert pool.public()['items'][0]['quota'] == 3


def test_later_success_does_not_unblock_account_after_concurrent_limit(pool):
    account_id = pool.import_tokens(['first-fixture'])['items'][0]['id']
    pool.options(PoolOptions(concurrency=2, per_account=2))
    pool.acquire(lambda: False)
    pool.acquire(lambda: False)
    pool.release(account_id, False, UpstreamHTTPError('synthetic rate limit', 429))
    pool.release(account_id, True)
    assert pool.public()['items'][0]['status'] == 'limited'


def test_cancel_after_lease_does_not_count_as_failed_generation(pool):
    account_id = pool.import_tokens(['first-fixture'])['items'][0]['id']
    pool.acquire(lambda: False)
    pool.release(account_id, None)
    assert pool.public()['items'][0]['fail'] == 0
    assert sum(pool.inflight.values()) == 0


def test_manual_quota_annotation_does_not_clear_server_limit(pool):
    account_id = pool.import_tokens(['first-fixture'])['items'][0]['id']
    pool.acquire(lambda: False)
    pool.release(account_id, False, UpstreamHTTPError('synthetic limit', 429))
    pool.edit(account_id, AccountEdit(plan='plus', quota=100))
    account = pool.public()['items'][0]
    assert account['metadata_source'] == 'manual'
    assert account['status'] == 'limited'


def test_clearing_primary_connection_keeps_other_accounts_and_task_history(client):
    client.post('/api/accounts/import', json={'content': 'second-fixture'})
    response = client.post('/api/settings', json={'clear_token': True})
    assert response.status_code == 200
    assert response.json()['configured'] is True
    assert len(client.get('/api/accounts').json()['items']) == 1


@pytest.mark.parametrize('remaining,expected', [(None, None), (0, 0), (5, 5)])
def test_account_info_uses_original_endpoints_and_unknown_quota_is_not_zero(remaining, expected):
    from studio.upstream.backend import OpenAIBackendAPI
    from .test_provider import FakeResponse, FakeSession
    transport, assets = FakeSession(), FakeSession()
    def get(url, **kwargs):
        if url.endswith('/me'):
            return FakeResponse({'email': 'fixture@example.test', 'private': 'DO_NOT_KEEP'})
        return FakeResponse({'accounts': {'default': {'account': {'plan_type': 'plus', 'private': 'DO_NOT_KEEP'}}}})
    def post(url, **kwargs):
        assert url.endswith('/backend-api/conversation/init')
        return FakeResponse({'limits_progress': [] if remaining is None else [{'feature_name': 'image_gen', 'remaining': remaining, 'reset_after': '2099-01-01'}]})
    with patch('studio.upstream.backend.requests.Session', side_effect=[transport, assets]), patch.object(transport, 'get', side_effect=get), patch.object(transport, 'post', side_effect=post):
        backend = OpenAIBackendAPI('synthetic-fixture')
        try:
            details = backend.get_account_info()
        finally:
            backend.close()
    assert details['quota'] == expected
    assert details['plan'] == 'plus'
    assert 'DO_NOT_KEEP' not in json.dumps(details)
