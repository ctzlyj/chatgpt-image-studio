import base64
import json
import re

from .upstream.backend import OpenAIBackendAPI


class AccountSettings:
    def __init__(self, values):
        self.values = values

    def snapshot(self):
        return dict(self.values)


def inspect_account(values):
    backend = OpenAIBackendAPI(values['access_token'], values['proxy'], values['upstream_model'])
    try:
        return backend.get_account_info()
    finally:
        backend.close()


class WebImageProvider:
    def __init__(self, settings):
        self.settings = settings

    def for_account(self, values):
        return WebImageProvider(AccountSettings(values))

    def connect(self):
        settings = self.settings.snapshot()
        if not settings['access_token']:
            raise ValueError('请先在连接设置中保存 ChatGPT 网页登录凭证')
        return OpenAIBackendAPI(settings['access_token'], settings['proxy'], settings['upstream_model'])

    def check(self):
        backend = self.connect()
        try:
            backend._get_me()
            return {'ok': True, 'message': '网页登录有效；此检查不生成图片，也不能证明实际图像模型版本'}
        finally:
            backend.close()

    def generate(self, prompt, references, model, progress):
        backend = self.connect()
        backend.upstream_model = model
        try:
            progress('准备网页会话')
            encoded = [base64.b64encode(payload).decode('ascii') for payload in references]
            conversation_id = ''
            blocked = False
            progress('已提交网页请求，请勿重复提交')
            for payload in backend._stream_picture_conversation(prompt, model, encoded):
                if payload == '[DONE]':
                    break
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                matches = re.findall(r'"conversation_id"\s*:\s*"([a-zA-Z0-9_-]+)"', payload)
                if matches:
                    conversation_id = matches[-1]
                if event.get('type') in {'error', 'conversation_error'} or event.get('error'):
                    raise RuntimeError('网页返回生图错误，请检查账号额度及网页可用性；不要立即重复提交')
                if event.get('type') == 'moderation':
                    blocked = blocked or (event.get('moderation_response') or {}).get('blocked') is True
                progress('网页正在生成图片')
            if blocked:
                raise RuntimeError('网页明确返回审核拦截，本次已停止；请在网页核对或反馈误判，不自动重复提交')
            if not conversation_id:
                raise RuntimeError('未收到网页会话回执，结果不明；请先到网页核对，不自动重试')
            progress('等待图片文件')
            urls = backend.resolve_conversation_image_urls(conversation_id, [], [])
            if not urls:
                raise RuntimeError('网页任务没有返回可下载图片，结果可能仍在处理中，请先在网页核对')
            progress('保存图片到本机')
            return backend.download_image_bytes(urls)
        finally:
            backend.close()


def public_error(error):
    message = str(error)
    if message.startswith(('号池', '网页', '未收到网页', '请先在连接', '参考图', '本地图片')) and not re.search(r'Bearer|eyJ|access_token|https?://', message, re.I):
        return message[:220]
    if any(word in message.lower() for word in ['arkose', 'turnstile', 'proof', 'requirements']):
        return '网页要求额外验证，或网页协议已变化。请先在 ChatGPT 完成验证，再检查适配器；不会绕过登录权限'
    return '网页连接或图片处理失败。请检查代理及网页登录状态；结果不明时先核对网页，不自动重复提交'
