import ctypes
import copy
import json
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import BaseModel, Field, SecretStr

POOL_DEFAULTS = {'concurrency': 2, 'per_account': 1, 'refresh_minutes': 60}


def account_record(token, name=''):
    return {'id': uuid.uuid4().hex, 'access_token': token, 'name': name or 'ChatGPT 账号', 'enabled': True,
            'status': 'unverified', 'email': '', 'plan': '', 'quota': None, 'restore_at': None,
            'success': 0, 'fail': 0, 'last_used': None, 'last_refresh': None, 'created': time.time(), 'error': ''}


class SettingsUpdate(BaseModel):
    access_token: SecretStr | None = None
    proxy: SecretStr | None = None
    upstream_model: str = Field(default='gpt-5-3', pattern=r'^[a-zA-Z0-9._-]{1,100}$')
    display_model: str = Field(default='gpt-image-2.5', pattern=r'^[a-zA-Z0-9._-]{1,100}$')
    clear_token: bool = False


def parse_access_token(value: str) -> str:
    if len(value) > 262144:
        raise ValueError('粘贴内容过长，请从 ChatGPT 会话信息页重新全选复制 JSON')
    credential = value.strip()
    if not credential:
        return ''
    if credential.startswith(('{', '[', '"')) or credential in {'null', 'true', 'false'}:
        try:
            session = json.loads(credential)
        except (ValueError, RecursionError):
            raise ValueError('会话 JSON 不完整或格式不正确，请从会话信息页重新全选复制') from None
        if not isinstance(session, dict):
            raise ValueError('需要完整的会话 JSON 对象，请从 ChatGPT 会话信息页重新全选复制')
        candidates = [session[key] for key in ('accessToken', 'access_token') if key in session]
        if not candidates or any(not isinstance(candidate, str) for candidate in candidates):
            raise ValueError('会话 JSON 中没有有效登录凭证，请先登录 ChatGPT，再重新复制整个 JSON')
    else:
        candidates = [credential]
    tokens = []
    for candidate in candidates:
        token = candidate.strip()
        if token[:7].lower() == 'bearer ':
            token = token[7:].strip()
        if not token or token.lower() == 'bearer' or len(token) > 20000 or any(character.isspace() for character in token):
            raise ValueError('登录凭证无效，请先登录 ChatGPT，再从会话信息页重新全选复制 JSON')
        tokens.append(token)
    if len(set(tokens)) != 1:
        raise ValueError('会话 JSON 中的登录凭证不一致，请从会话信息页重新全选复制')
    return tokens[0]


class Blob(ctypes.Structure):
    _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_char))]


def protect_windows(payload: bytes, decrypt=False) -> bytes:
    buffer = ctypes.create_string_buffer(payload)
    source = Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    operation = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError('无法读取当前系统用户的加密凭证')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


class Settings:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        if os.name != 'nt':
            directory.chmod(0o700)
        self.path = directory / 'connection.enc'
        self.lock = threading.RLock()
        self.values = {'access_token': '', 'proxy': '', 'upstream_model': 'gpt-5-3', 'display_model': 'gpt-image-2.5', 'api_key': secrets.token_urlsafe(36)}
        if self.path.exists():
            self.values.update(json.loads(self._decode(self.path.read_bytes())))
        else:
            self._save()
        if 'accounts' not in self.values:
            self.values['accounts'] = [account_record(self.values['access_token'], '原有账号')] if self.values['access_token'] else []
            self.values['pool'] = dict(POOL_DEFAULTS)
            self.values['sources'] = []
            self._save()

    def _cipher(self):
        path = self.directory / 'local.key'
        if not path.exists():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as output:
                output.write(Fernet.generate_key())
        return Fernet(path.read_bytes())

    def _decode(self, data):
        if data.startswith(b'DPAPI:'):
            if os.name != 'nt':
                raise RuntimeError('Windows 凭证不能迁移到其他系统，请重新配置')
            return protect_windows(data[6:], decrypt=True)
        return self._cipher().decrypt(data[7:])

    def _save(self):
        payload = json.dumps(self.values).encode()
        data = b'DPAPI:' + protect_windows(payload) if os.name == 'nt' else b'FERNET:' + self._cipher().encrypt(payload)
        temporary = self.path.with_suffix('.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
        temporary.replace(self.path)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.values)

    def commit(self, values):
        with self.lock:
            previous = self.values
            self.values = values
            try:
                self._save()
            except Exception:
                self.values = previous
                raise

    def public(self):
        current = self.snapshot()
        return {'configured': any(account['enabled'] for account in current['accounts']), 'account_count': len(current['accounts']), 'proxy_configured': bool(current['proxy']),
                'upstream_model': current['upstream_model'], 'display_model': current['display_model'],
                'model_verified': False, 'credential_storage': 'Windows 当前用户加密' if os.name == 'nt' else '本机私有密钥加密'}

    def update(self, request: SettingsUpdate):
        with self.lock:
            next_values = self.snapshot()
            if request.access_token is not None:
                token = parse_access_token(request.access_token.get_secret_value())
                if token:
                    existing = next((account for account in next_values['accounts'] if account['access_token'] == token), None)
                    if existing is None:
                        existing = account_record(token)
                        next_values['accounts'].append(existing)
                    next_values['access_token'] = token
            if request.clear_token:
                next_values['accounts'] = [account for account in next_values['accounts'] if account['access_token'] != next_values['access_token']]
                next_values['access_token'] = next((account['access_token'] for account in next_values['accounts'] if account['enabled']), '')
            if request.proxy is not None:
                proxy = request.proxy.get_secret_value().strip()
                parsed = urlsplit(proxy)
                if proxy and (parsed.scheme not in {'http', 'https', 'socks5', 'socks5h'} or not parsed.hostname):
                    raise ValueError('代理地址须为 HTTP、HTTPS 或 SOCKS5 地址')
                next_values['proxy'] = proxy
            next_values.update(upstream_model=request.upstream_model, display_model=request.display_model)
            self.commit(next_values)
        return self.public()
