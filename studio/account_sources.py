import copy
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlsplit

from curl_cffi import requests
from fastapi import APIRouter
from pydantic import BaseModel, Field, SecretStr

from .accounts import parse_accounts


class SourceEdit(BaseModel):
    kind: str = Field(pattern='^(cpa|sub2api)$')
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(max_length=500)
    secret: SecretStr | None = None
    email: SecretStr | None = None
    password: SecretStr | None = None
    group: str = Field(default='', max_length=100)


class SourceImport(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


def validate_source_url(value):
    parsed = urlsplit(value.strip())
    if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError('服务器地址不可包含登录信息、查询参数或片段')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}):
        raise ValueError('远程服务器必须使用 HTTPS；HTTP 仅支持本机地址')
    return value.strip().rstrip('/')


class SourceError(ValueError):
    pass


def remote_json(session, method, url, **kwargs):
    response = None
    try:
        response = session.request(method, url, timeout=25, stream=True, allow_redirects=False, **kwargs)
        if response.status_code != 200:
            raise SourceError(f'远程账号服务器请求失败（HTTP {response.status_code}）；请检查地址或管理凭证')
        content, size = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > 8 * 1024 * 1024:
                raise SourceError('远程响应超过 8 MB，请缩小筛选范围')
            content.append(chunk)
        payload = json.loads(b''.join(content))
        if isinstance(payload, dict) and 'code' in payload and 'data' in payload:
            if payload['code'] not in {0, 200}:
                raise SourceError('远程账号服务器拒绝请求，请检查管理权限')
            return payload['data']
        return payload
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError('远程服务器没有返回有效 JSON') from None
    except SourceError:
        raise
    except Exception:
        raise ValueError('无法连接远程账号服务器，请检查网络、地址和管理权限') from None
    finally:
        if response is not None:
            response.close()


class RemoteSource:
    def __init__(self, source, proxy=''):
        self.source = source
        self.session = requests.Session(verify=True, proxy=proxy or None, allow_redirects=False)
        self.headers = {'Accept': 'application/json'}

    def __enter__(self):
        source = self.source
        try:
            if source['kind'] == 'cpa':
                self.headers['Authorization'] = 'Bearer ' + source['secret']
            elif source.get('secret'):
                self.headers['x-api-key'] = source['secret']
            else:
                result = remote_json(self.session, 'POST', source['base_url'] + '/api/v1/auth/login',
                                     json={'email': source['email'], 'password': source['password']}, headers=self.headers)
                token = result.get('access_token') if isinstance(result, dict) else None
                if not isinstance(token, str) or not token:
                    raise ValueError('远程服务器未返回有效管理登录凭证')
                self.headers['Authorization'] = 'Bearer ' + token
        except Exception:
            self.session.close()
            raise
        return self

    def __exit__(self, *args):
        self.session.close()

    def get(self, path, params=None):
        return remote_json(self.session, 'GET', self.source['base_url'] + path, params=params, headers=self.headers)

    def list_items(self, groups=False):
        if self.source['kind'] == 'cpa':
            payload = self.get('/v0/management/auth-files')
            if not isinstance(payload, dict) or not isinstance(payload.get('files'), list):
                raise ValueError('CPA 列表格式不正确')
            if len(payload['files']) > 2000:
                raise ValueError('远程账号超过 2000 条，请拆分来源；未截断列表')
            return [{'id': str(item['name']), 'name': str(item['name'])[:300], 'email': str(item.get('email') or '')[:254]} for item in payload['files'] if isinstance(item, dict) and item.get('name')]
        results, seen = [], set()
        for page in range(1, 12):
            params = {'page': page, 'page_size': 200, 'platform': 'openai'}
            if not groups:
                params['type'] = 'oauth'
                if self.source.get('group'):
                    params['group'] = self.source['group']
            payload = self.get('/api/v1/admin/' + ('groups' if groups else 'accounts'), params)
            items = payload if isinstance(payload, list) else next((payload[key] for key in ('items', 'data', 'list') if isinstance(payload, dict) and isinstance(payload.get(key), list)), None)
            if items is None:
                raise ValueError('sub2api 列表格式不正确')
            for item in items:
                if not isinstance(item, dict) or item.get('id') is None:
                    continue
                if not groups and (item.get('platform', 'openai') != 'openai' or item.get('type', 'oauth') != 'oauth'):
                    continue
                remote_id = str(item['id'])
                if remote_id in seen:
                    raise ValueError('远程分页重复，已停止读取；请缩小筛选范围')
                seen.add(remote_id)
                credentials = item.get('credentials') if isinstance(item.get('credentials'), dict) else {}
                results.append({'id': remote_id, 'name': str(item.get('name') or remote_id)[:100], 'email': str(credentials.get('email') or '')[:254]})
                if len(results) > 2000:
                    raise ValueError('远程账号超过 2000 条，请按分组筛选；未截断列表')
            total = payload.get('total') if isinstance(payload, dict) else None
            if len(items) < 200 or (isinstance(total, int) and page * 200 >= total):
                return results
        raise ValueError('远程账号超过 2000 条，请按分组筛选')

    def token(self, remote_id):
        if self.source['kind'] == 'cpa':
            payload = self.get('/v0/management/auth-files/download', {'name': remote_id})
        else:
            if not remote_id or any(not (character.isalnum() or character in '_-') for character in remote_id):
                raise ValueError('远程账号编号格式不正确')
            payload = self.get('/api/v1/admin/accounts/' + quote(remote_id, safe=''))
            if not isinstance(payload, dict) or payload.get('platform', 'openai') != 'openai' or payload.get('type', 'oauth') != 'oauth':
                raise ValueError('仅支持 OpenAI OAuth 账号')
            credentials = payload.get('credentials')
            if isinstance(credentials, dict) and not any(key in credentials for key in ('access_token', 'accessToken')) and 'token' in credentials:
                payload = {'access_token': credentials['token']}
        tokens = parse_accounts(json.dumps(payload))
        if len(tokens) != 1:
            raise ValueError('远程条目必须只包含一个账号')
        return tokens[0]


class AccountSources:
    def __init__(self, settings, pool):
        self.settings, self.pool = settings, pool
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='account-import')
        self.jobs = {job['id']: job for job in settings.snapshot().get('import_jobs', [])}
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        if any(job['status'] in {'queued', 'running'} for job in self.jobs.values()):
            for job in self.jobs.values():
                if job['status'] in {'queued', 'running'}:
                    job['status'] = 'interrupted'
            self._save_jobs()

    def _save_jobs(self):
        with self.settings.lock:
            snapshot = self.settings.snapshot()
            snapshot['import_jobs'] = copy.deepcopy(list(self.jobs.values()))
            self.settings.commit(snapshot)

    def close(self):
        self.stopped.set()
        self.executor.shutdown(wait=True, cancel_futures=True)

    def public(self):
        snapshot = self.settings.snapshot()
        with self.lock:
            return {'items': [{key: item.get(key, '') for key in ('id', 'kind', 'name', 'base_url', 'group')} | {'credential_configured': bool(item.get('secret') or item.get('password'))} for item in snapshot['sources']], 'jobs': copy.deepcopy(list(self.jobs.values()))}

    def source(self, source_id):
        source = next((item for item in self.settings.snapshot()['sources'] if item['id'] == source_id), None)
        if not source:
            raise ValueError('导入服务器不存在')
        return source

    def save(self, body, source_id=None):
        base_url = validate_source_url(body.base_url)
        with self.settings.lock:
            snapshot = self.settings.snapshot()
            previous = next((item for item in snapshot['sources'] if item['id'] == source_id), None)
            if source_id and not previous:
                raise ValueError('导入服务器不存在')
            source = dict(previous or {'id': uuid.uuid4().hex, 'secret': '', 'email': '', 'password': ''})
            if previous and (previous['base_url'] != base_url or previous['kind'] != body.kind):
                source.update(secret='', email='', password='')
            source.update(kind=body.kind, name=body.name, base_url=base_url, group=body.group)
            for key in ('secret', 'email', 'password'):
                value = getattr(body, key)
                if value is not None:
                    source[key] = value.get_secret_value().strip()
            if not source['secret'] and (body.kind == 'cpa' or not source['email'] or not source['password']):
                raise ValueError('请填写管理密钥，或 sub2api 管理员邮箱与密码')
            snapshot['sources'] = [item for item in snapshot['sources'] if item['id'] != source['id']] + [source]
            if len(snapshot['sources']) > 30:
                raise ValueError('最多配置 30 个导入服务器')
            self.settings.commit(snapshot)
        return self.public()

    def delete(self, source_id):
        with self.settings.lock:
            snapshot = self.settings.snapshot()
            snapshot['sources'] = [item for item in snapshot['sources'] if item['id'] != source_id]
            self.settings.commit(snapshot)
        return self.public()

    def list_items(self, source_id, groups=False):
        source = self.source(source_id)
        with RemoteSource(source, self.settings.snapshot()['proxy']) as remote:
            return {'items': remote.list_items(groups)}

    def start_import(self, source_id, ids):
        if any(not remote_id or len(remote_id) > 300 for remote_id in ids):
            raise ValueError('远程账号编号不正确')
        source = self.source(source_id)
        ids = list(dict.fromkeys(ids))
        with self.lock:
            if any(job['source_id'] == source_id and job['status'] in {'queued', 'running'} for job in self.jobs.values()):
                raise ValueError('该服务器已有导入任务，请查看进度，不要重复提交')
            if sum(job['status'] in {'queued', 'running'} for job in self.jobs.values()) >= 4:
                raise ValueError('已有 4 个导入任务，请等待完成')
            job = {'id': uuid.uuid4().hex, 'source_id': source_id, 'status': 'queued', 'total': len(ids), 'done': 0, 'added': 0, 'duplicates': 0, 'failed': 0, 'errors': []}
            if len(self.jobs) >= 50:
                oldest = next((job_id for job_id, item in self.jobs.items() if item['status'] not in {'queued', 'running'}), None)
                if oldest:
                    self.jobs.pop(oldest)
            self.jobs[job['id']] = job
            self._save_jobs()
            self.executor.submit(self._import, job, source, ids)
            return dict(job)

    def _import(self, job, source, ids):
        try:
            with self.lock:
                job['status'] = 'running'
                self._save_jobs()
            with RemoteSource(source, self.settings.snapshot()['proxy']) as remote:
                for index, remote_id in enumerate(ids, 1):
                    if self.stopped.is_set():
                        with self.lock:
                            job['status'] = 'interrupted'
                            self._save_jobs()
                        return
                    try:
                        result = self.pool.import_tokens([remote.token(remote_id)])
                        with self.lock:
                            job['added'] += result['added']
                            job['duplicates'] += result['duplicates']
                    except Exception:
                        with self.lock:
                            job['failed'] += 1
                            job['errors'].append({'index': index, 'message': '此条导入失败，请检查远程账号或权限'})
                    finally:
                        with self.lock:
                            job['done'] += 1
                            self._save_jobs()
            with self.lock:
                job['status'] = 'completed'
                self._save_jobs()
        except Exception:
            with self.lock:
                job['status'] = 'failed'
                job['errors'].append({'index': 0, 'message': '无法连接或登录远程服务器，请检查配置'})
                self._save_jobs()


def source_routes(sources):
    router = APIRouter(prefix='/api/account-sources')

    @router.get('')
    def list_sources():
        return sources.public()

    @router.post('')
    def save_source(body: SourceEdit):
        return sources.save(body)

    @router.post('/{source_id}')
    def edit_source(source_id: str, body: SourceEdit):
        return sources.save(body, source_id)

    @router.delete('/{source_id}')
    def delete_source(source_id: str):
        return sources.delete(source_id)

    @router.get('/{source_id}/items')
    def items(source_id: str):
        return sources.list_items(source_id)

    @router.get('/{source_id}/groups')
    def groups(source_id: str):
        return sources.list_items(source_id, True)

    @router.post('/{source_id}/import')
    def import_source(source_id: str, body: SourceImport):
        return sources.start_import(source_id, body.ids)

    return router
