import uuid


def new_uuid():
    return str(uuid.uuid4())


def ensure_ok(response, context):
    if 200 <= response.status_code < 300:
        return
    messages = {401: '网页登录已失效，请更新登录凭证', 403: '网页拒绝访问，请在 ChatGPT 完成登录或验证', 429: '网页账号额度不足或限流，请稍后再试'}
    raise RuntimeError(messages.get(response.status_code, f'网页请求失败（HTTP {response.status_code}，{context}）'))


def iter_sse_payloads(response):
    for raw_line in response.iter_lines():
        line = raw_line.decode('utf-8', errors='replace') if isinstance(raw_line, bytes) else str(raw_line)
        if line.startswith('data:'):
            yield line[5:].strip()


class QuietLogger:
    def debug(self, *args, **kwargs):
        pass

    info = debug
    warning = debug


logger = QuietLogger()
