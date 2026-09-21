import base64
import copy
import json
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from pydantic import BaseModel, Field, SecretStr

from .settings import POOL_DEFAULTS, account_record, parse_access_token


class PoolOptions(BaseModel):
    concurrency: int = Field(default=2, ge=1, le=8)
    per_account: int = Field(default=1, ge=1, le=3)
    refresh_minutes: int = Field(default=60, ge=0, le=1440)


class AccountEdit(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None
    access_token: SecretStr | None = None
    plan: str | None = Field(default=None, max_length=80)
    quota: int | None = Field(default=None, ge=0, le=1000000)


def parse_accounts(text, limit=500):
    if len(text) > 4 * 1024 * 1024:
        raise ValueError('导入内容超过 4 MB，请分批导入')
    text = text.strip().lstrip('\ufeff')
    if not text:
        raise ValueError('请粘贴会话 JSON 或选择账号文件')
    try:
        payload = json.loads(text) if text.startswith(('{', '[')) else text.splitlines()
    except (ValueError, RecursionError):
        raise ValueError('导入 JSON 格式不正确，未保存任何账号') from None
    if isinstance(payload, dict):
        if 'accounts' in payload:
            payload = payload['accounts']
        else:
            payload = [payload]
    if not isinstance(payload, list) or not 1 <= len(payload) <= limit:
        raise ValueError(f'每批导入 1 至 {limit} 个账号')
    tokens = []
    for item in payload:
        if isinstance(item, str):
            if not item.strip():
                continue
            token = parse_access_token(item)
        elif isinstance(item, dict):
            credentials = item.get('credentials') if isinstance(item.get('credentials'), dict) else item
            token = parse_access_token(json.dumps({key: credentials[key] for key in ('accessToken', 'access_token') if key in credentials}))
        else:
            raise ValueError('账号格式不正确，未保存任何账号')
        if token and token not in tokens:
            tokens.append(token)
    if not tokens:
        raise ValueError('没有可导入的登录凭证')
    return tokens


class AccountPool:
    def __init__(self, settings, inspector):
        self.settings = settings
        self.inspector = inspector
        self.condition = threading.Condition(settings.lock)
        self.inflight = {}
        self.refreshing = set()
        self.last_selected = ''
        self.stopped = threading.Event()
        self.refresh_lock = threading.Lock()
        self.monitor = None

    def start(self):
        self.monitor = threading.Thread(target=self._monitor, name='account-refresh', daemon=True)
        self.monitor.start()

    def close(self):
        self.stopped.set()
        with self.condition:
            self.condition.notify_all()
        if self.monitor:
            self.monitor.join(timeout=65)

    def _monitor(self):
        while not self.stopped.wait(30):
            snapshot = self.settings.snapshot()
            minutes = snapshot.get('pool', POOL_DEFAULTS)['refresh_minutes']
            if not minutes:
                continue
            now = time.time()
            due = [account['id'] for account in snapshot['accounts'] if account['enabled'] and account['status'] != 'invalid'
                   and not self.inflight.get(account['id']) and now - (account['last_refresh'] or account['created']) >= minutes * 60]
            if due:
                try:
                    self.refresh(due)
                except Exception:
                    pass

    def public(self):
        with self.condition:
            snapshot = self.settings.snapshot()
            items = [{key: value for key, value in account.items() if key != 'access_token'} | {'inflight': self.inflight.get(account['id'], 0)} for account in snapshot['accounts']]
            return {'items': items, 'options': snapshot['pool'], 'settings': self.settings.public()}

    def import_tokens(self, tokens, name=''):
        with self.condition:
            snapshot = self.settings.snapshot()
            existing = {account['access_token'] for account in snapshot['accounts']}
            added = 0
            for token in tokens:
                if token not in existing:
                    snapshot['accounts'].append(account_record(token, name))
                    existing.add(token)
                    added += 1
            if len(snapshot['accounts']) > 2000:
                raise ValueError('本地号池最多保存 2000 个账号')
            if not snapshot['access_token'] and snapshot['accounts']:
                snapshot['access_token'] = snapshot['accounts'][0]['access_token']
            self.settings.commit(snapshot)
            self.condition.notify_all()
            return {'added': added, 'duplicates': len(tokens) - added, **self.public()}

    def _selected(self, snapshot, ids):
        selected = [account for account in snapshot['accounts'] if account['id'] in ids]
        if len(selected) != len(set(ids)) or not ids:
            raise ValueError('请选择仍然存在的账号')
        return selected

    def edit(self, account_id, update):
        with self.condition:
            snapshot = self.settings.snapshot()
            account = self._selected(snapshot, [account_id])[0]
            if self.inflight.get(account_id):
                raise ValueError('账号正在执行任务，请完成后再修改')
            if update.name is not None:
                account['name'] = update.name.strip() or 'ChatGPT 账号'
            if update.enabled is not None:
                account['enabled'] = update.enabled
            if update.plan is not None:
                account['plan'] = update.plan
                account['metadata_source'] = 'manual'
            if 'quota' in update.model_fields_set:
                account['quota'] = update.quota
                account['metadata_source'] = 'manual'
            if update.access_token is not None:
                token = parse_access_token(update.access_token.get_secret_value())
                if not token:
                    raise ValueError('更新凭证不能为空')
                if any(item['id'] != account_id and item['access_token'] == token for item in snapshot['accounts']):
                    raise ValueError('该凭证已在另一个账号中，请勿重复添加')
                if snapshot['access_token'] == account['access_token']:
                    snapshot['access_token'] = token
                account.update(access_token=token, status='unverified', quota=None, restore_at=None, email='', plan='', error='', last_refresh=None)
            self.settings.commit(snapshot)
            self.condition.notify_all()
            return self.public()

    def action(self, ids, action):
        with self.condition:
            snapshot = self.settings.snapshot()
            selected = self._selected(snapshot, ids)
            if any(self.inflight.get(account['id']) for account in selected):
                raise ValueError('选中账号仍在执行任务，请完成后再操作')
            if action == 'delete':
                snapshot['accounts'] = [account for account in snapshot['accounts'] if account['id'] not in ids]
            else:
                for account in selected:
                    account['enabled'] = action == 'enable'
            if not any(account['access_token'] == snapshot['access_token'] and account['enabled'] for account in snapshot['accounts']):
                snapshot['access_token'] = next((account['access_token'] for account in snapshot['accounts'] if account['enabled']), '')
            self.settings.commit(snapshot)
            self.condition.notify_all()
            return self.public()

    def options(self, options):
        with self.condition:
            snapshot = self.settings.snapshot()
            snapshot['pool'] = options.model_dump()
            self.settings.commit(snapshot)
            self.condition.notify_all()
            return self.public()

    def acquire(self, cancelled):
        while not self.stopped.is_set():
            if cancelled():
                return None
            with self.condition:
                snapshot = self.settings.snapshot()
                candidates = [account for account in snapshot['accounts'] if account['enabled'] and account['status'] not in {'invalid', 'limited', 'verification'} and account['quota'] != 0]
                if not candidates:
                    raise ValueError('号池没有可用账号，请导入凭证或刷新账号状态；不会自动重试已提交任务')
                candidates = [account for account in candidates if account['id'] not in self.refreshing]
                options = snapshot['pool']
                if sum(self.inflight.values()) < options['concurrency']:
                    ids = [account['id'] for account in snapshot['accounts']]
                    offset = (ids.index(self.last_selected) + 1) % len(ids) if self.last_selected in ids else 0
                    candidates.sort(key=lambda account: (ids.index(account['id']) - offset) % len(ids))
                    for account in candidates:
                        if self.inflight.get(account['id'], 0) < options['per_account'] and (account['quota'] is None or self.inflight.get(account['id'], 0) < account['quota']):
                            self.inflight[account['id']] = self.inflight.get(account['id'], 0) + 1
                            self.last_selected = account['id']
                            snapshot['access_token'] = account['access_token']
                            return account['id'], snapshot
                self.condition.wait(timeout=0.2)
        return None

    def release(self, account_id, success, error=None):
        with self.condition:
            self.inflight[account_id] = max(0, self.inflight.get(account_id, 1) - 1)
            try:
                snapshot = self.settings.snapshot()
                account = next((item for item in snapshot['accounts'] if item['id'] == account_id), None)
                if account and success is not None:
                    account['last_used'] = time.time()
                    account['success' if success else 'fail'] += 1
                    if success:
                        if account['quota'] is not None:
                            account['quota'] = max(0, account['quota'] - 1)
                        if account['status'] not in {'invalid', 'verification', 'limited'}:
                            account['status'] = 'limited' if account['quota'] == 0 else 'ready'
                        account['error'] = ''
                    else:
                        status = getattr(error, 'status_code', None)
                        if status in {401, 403, 429}:
                            account['status'] = {401: 'invalid', 403: 'verification', 429: 'limited'}[status]
                        account['error'] = '网页登录失效，请更新凭证' if status == 401 else '请求未完成；请核对任务回执后处理'
                    self.settings.commit(snapshot)
            finally:
                self.condition.notify_all()

    def refresh(self, ids):
        with self.refresh_lock:
            with self.condition:
                snapshot = self.settings.snapshot()
                selected = copy.deepcopy(self._selected(snapshot, ids))
                if any(self.inflight.get(account['id']) for account in selected):
                    raise ValueError('选中账号正在生图，请完成后再刷新，避免覆盖实时额度')
                self.refreshing.update(ids)
            try:
                return self._refresh_selected(snapshot, selected)
            finally:
                with self.condition:
                    self.refreshing.difference_update(ids)
                    self.condition.notify_all()

    def _refresh_selected(self, snapshot, selected):
        def inspect(account):
            config = dict(snapshot, access_token=account['access_token'])
            try:
                return account, self.inspector(config), None
            except Exception as error:
                return account, None, getattr(error, 'status_code', 0)
        refreshed, errors = 0, []
        with ThreadPoolExecutor(max_workers=3) as executor:
            for previous, details, failure in executor.map(inspect, selected):
                with self.condition:
                    current = self.settings.snapshot()
                    account = next((item for item in current['accounts'] if item['id'] == previous['id'] and item['access_token'] == previous['access_token']), None)
                    if not account:
                        continue
                    account['last_refresh'] = time.time()
                    if details is not None:
                        account.update({key: details[key] for key in ('email', 'plan', 'quota', 'restore_at')})
                        account['status'] = 'limited' if account['quota'] == 0 else 'ready'
                        account['error'] = ''
                        account['metadata_source'] = 'upstream'
                        refreshed += 1
                    else:
                        account['status'] = {401: 'invalid', 403: 'verification', 429: 'limited'}.get(failure, account['status'])
                        account['error'] = '账号刷新失败，请检查登录、代理或网页可用性'
                        errors.append({'id': account['id'], 'message': account['error']})
                    self.settings.commit(current)
                    self.condition.notify_all()
        return {'refreshed': refreshed, 'errors': errors, **self.public()}

    def backup(self, ids, password):
        if len(password) < 12:
            raise ValueError('备份口令至少 12 个字符')
        selected = self._selected(self.settings.snapshot(), ids)
        salt = secrets.token_bytes(16)
        key = Scrypt(salt=salt, length=32, n=16384, r=8, p=1).derive(password.encode())
        payload = json.dumps({'accounts': [{'access_token': item['access_token']} for item in selected]}).encode()
        if len(payload) > 4 * 1024 * 1024:
            raise ValueError('所选凭证超过 4 MB，请分批备份')
        return {'format': 'studio-pool-v1', 'salt': base64.b64encode(salt).decode(), 'data': Fernet(base64.urlsafe_b64encode(key)).encrypt(payload).decode()}

    def restore(self, payload, password):
        try:
            if payload.get('format') != 'studio-pool-v1' or len(payload.get('data', '')) > 8 * 1024 * 1024:
                raise ValueError()
            salt = base64.b64decode(payload['salt'], validate=True)
            if len(salt) != 16:
                raise ValueError()
            key = Scrypt(salt=salt, length=32, n=16384, r=8, p=1).derive(password.encode())
            text = Fernet(base64.urlsafe_b64encode(key)).decrypt(payload['data'].encode()).decode()
            tokens = parse_accounts(text, limit=2000)
        except (KeyError, ValueError, TypeError, InvalidToken, UnicodeError):
            raise ValueError('备份格式或口令不正确，未导入账号') from None
        return self.import_tokens(tokens)
