import asyncio
import base64
from contextlib import asynccontextmanager
from io import BytesIO
import json
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit
import uuid
import zipfile
import hashlib

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .planner import RATIOS, BatchRequest, plan
from .resolution import NATIVE_NOTE, NATIVE_PIXEL_BUDGET, describe, table
from .upscale import capability as upscale_capability, upscale_png
from .provider import WebImageProvider, public_error
from .service import StudioService
from .settings import Settings, SettingsUpdate
from .store import Store
from .accounts import AccountPool
from .account_routes import account_routes
from .provider import inspect_account
from .account_sources import AccountSources, source_routes

ROOT = Path(__file__).resolve().parents[1]


class ImageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    prompt: str = Field(min_length=1, max_length=20000)
    model: str = 'gpt-image-2.5'
    n: int = Field(default=1, ge=1, le=4)
    size: str | None = None
    upscale: int = Field(default=1, ge=1, le=4)
    response_format: str = Field(default='b64_json', pattern=r'^(url|b64_json)$')
    stream: bool = False


def create_app(data_dir=None, provider_factory=WebImageProvider, account_inspector=inspect_account):
    directory = Path(data_dir or ROOT / 'data')
    settings = Settings(directory)
    store = Store(directory)
    provider = provider_factory(settings)
    pool = AccountPool(settings, account_inspector)
    sources = AccountSources(settings, pool)
    service = StudioService(store, settings, provider, pool)
    session = secrets.token_urlsafe(36)

    @asynccontextmanager
    async def lifespan(_app):
        pool.start()
        yield
        await run_in_threadpool(sources.close)
        await run_in_threadpool(pool.close)
        await run_in_threadpool(service.close)
        store.connection.close()

    app = FastAPI(title='ChatGPT Image Studio', version='0.1.0', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.settings = settings
    app.state.service = service
    app.state.pool = pool
    app.state.sources = sources
    app.include_router(account_routes(pool))
    app.include_router(source_routes(sources))

    @app.middleware('http')
    async def local_guard(request, call_next):
        host = urlsplit('http://' + request.headers.get('host', '')).hostname
        if host not in {'127.0.0.1', 'localhost', '::1'}:
            return JSONResponse({'detail': '仅允许本机访问'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': '拒绝跨站请求'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail': '拒绝跨域请求'}, status_code=403)
        content_length = request.headers.get('content-length', '0')
        if not content_length.isdigit() or int(content_length) > 32 * 1024 * 1024:
            return JSONResponse({'detail': '请求过大，最大 32 MB'}, status_code=413)
        path = request.url.path
        authorization = request.headers.get('authorization', '')
        api_authorized = secrets.compare_digest(authorization, 'Bearer ' + settings.snapshot()['api_key'])
        if path.startswith('/v1/') and not api_authorized:
            return JSONResponse({'detail': '需要本地 API 密钥'}, status_code=401)
        if path.startswith('/api/') and path != '/api/bootstrap' and not secrets.compare_digest(request.headers.get('x-studio-session', ''), session):
            return JSONResponse({'detail': '页面会话已失效，请刷新页面'}, status_code=401)
        if path.startswith('/files/') and not api_authorized and not secrets.compare_digest(request.cookies.get('studio_session', ''), session):
            return JSONResponse({'detail': '需要本机工作台会话'}, status_code=401)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValueError)
    async def bad_request(_request, error):
        return JSONResponse({'detail': str(error)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, error):
        return JSONResponse({'detail': '请求格式不正确，请检查字段及数量限制', 'fields': ['.'.join(str(part) for part in item['loc']) for item in error.errors()]}, status_code=422)

    @app.get('/api/bootstrap')
    def bootstrap():
        response = JSONResponse({'application': 'chatgpt-image-studio', 'session': session, 'settings': settings.public(), 'version': '0.1.0'})
        response.set_cookie('studio_session', session, httponly=True, samesite='strict')
        return response

    @app.get('/api/settings')
    def get_settings():
        return settings.public()

    @app.post('/api/settings')
    def set_settings(body: SettingsUpdate):
        with pool.condition:
            if body.clear_token and any(pool.inflight.values()):
                raise ValueError('账号正在执行任务，请完成后再清除凭证')
            result = settings.update(body)
            pool.condition.notify_all()
            return result

    @app.post('/api/connection/check')
    def check_connection():
        try:
            return provider.check()
        except Exception as error:
            return JSONResponse({'detail': public_error(error)}, status_code=502)

    @app.post('/api/local-api-key')
    def local_key():
        return {'api_key': settings.snapshot()['api_key']}

    @app.post('/api/uploads')
    async def upload(files: list[UploadFile] = File(...)):
        if not 1 <= len(files) <= 12:
            raise ValueError('一次最多上传 12 张参考图')
        results = []
        total = 0
        for file in files:
            data = await file.read(15 * 1024 * 1024 + 1)
            total += len(data)
            if total > 15 * 1024 * 1024:
                raise ValueError('本次上传合计超过 15 MB')
            results.append(await run_in_threadpool(store.save_asset, data, file.filename or '参考图'))
        return {'items': results}

    @app.post('/api/plan')
    def preview(body: BatchRequest):
        return {'tasks': plan(body, store.assets()), 'note': '比例为提示词要求，网页不保证精确输出，不会裁切拉伸原图。' + NATIVE_NOTE}

    @app.get('/api/resolution')
    def resolution():
        """网页生图的真实分辨率能力，以及放大能力说明。"""
        return {
            'native_pixel_budget': NATIVE_PIXEL_BUDGET,
            'note': NATIVE_NOTE,
            'ratios': table([item for item in RATIOS if item != 'Adaptive']),
            'upscale': upscale_capability(),
        }

    @app.get('/api/assets')
    def list_assets():
        return {'items': list(store.assets().values())}

    @app.post('/api/batches')
    def submit(body: BatchRequest):
        return service.submit(body)

    @app.get('/api/batches')
    def batches():
        return {'items': store.batches()}

    @app.get('/api/batches/{batch_id}')
    def batch(batch_id: str):
        return store.batch(batch_id)

    @app.post('/api/batches/{batch_id}/cancel')
    def cancel(batch_id: str):
        return service.cancel(batch_id)

    @app.delete('/api/batches/{batch_id}')
    def remove_batch(batch_id: str):
        with store.lock:
            if any(task['status'] in {'queued', 'running'} for task in store.batch(batch_id)['tasks']):
                raise ValueError('请等待运行任务结束，或先取消未开始任务')
            store.connection.execute('DELETE FROM tasks WHERE batch_id=?', (batch_id,))
            store.connection.execute('DELETE FROM batches WHERE id=?', (batch_id,))
            store.connection.commit()
        return {'ok': True, 'message': '记录已删除，图片文件保留以避免影响其他引用'}

    @app.get('/api/batches/{batch_id}/download')
    def download_batch(batch_id: str):
        batch = store.batch(batch_id)
        output = BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('任务清单.json', json.dumps(batch, ensure_ascii=False, indent=2))
            for task in batch['tasks']:
                for index, image in enumerate(task['results']):
                    archive.writestr(f'{task["index"] + 1:02d}-{index + 1}.png', store.asset_bytes(image['id']))
        return Response(output.getvalue(), media_type='application/zip', headers={'Content-Disposition': 'attachment; filename="images.zip"'})

    @app.get('/files/{filename}')
    def image_file(filename: str, download: bool = False, upscale: int = 1):
        if not re.fullmatch(r'[a-f0-9]{32}\.png', filename) or not (store.images / filename).is_file():
            raise HTTPException(404, '图片不存在')
        if upscale <= 1:
            return FileResponse(store.images / filename, media_type='image/png', filename=filename if download else None)
        payload, marker = upscale_png((store.images / filename).read_bytes(), upscale)
        stem = filename[:-4]
        headers = {
            'X-Image-Upscale-Factor': str(marker['factor']),
            'X-Image-Upscale-Backend': marker['backend'],
            'X-Image-Native-Size': f"{marker['native_width']}x{marker['native_height']}",
        }
        if download:
            headers['Content-Disposition'] = f'attachment; filename="{stem}-upscaled{marker["factor"]}x-{marker["backend"]}.png"'
        return Response(payload, media_type='image/png', headers=headers)

    @app.get('/v1/models')
    def models():
        aliases = list(dict.fromkeys([settings.public()['display_model'], 'gpt-image-2', 'gpt-image-2.5', 'gpt-image2.5']))
        return {'object': 'list', 'data': [{'id': alias, 'object': 'model', 'owned_by': 'local-web-adapter', 'actual_model_verified': False} for alias in aliases]}

    async def api_result(batch_id, body, base_url):
        started = time.monotonic()
        while True:
            batch = store.batch(batch_id)
            if all(task['status'] not in {'queued', 'running'} for task in batch['tasks']):
                break
            if time.monotonic() - started > 900:
                raise HTTPException(504, {'error': '任务仍在后台执行，请在本地工作台核对，不要重复提交', 'batch_id': batch_id})
            await asyncio.sleep(0.3)
        data = []
        for task in batch['tasks']:
            for image in task['results']:
                item = {'revised_prompt': task['effective_prompt']}
                native = store.asset_bytes(image['id'])
                payload, marker = await run_in_threadpool(upscale_png, native, body.upscale)
                item['native_size'] = f"{marker['native_width']}x{marker['native_height']}"
                item['size'] = f"{marker['width']}x{marker['height']}"
                item['upscale'] = marker
                if body.response_format == 'b64_json':
                    item['b64_json'] = base64.b64encode(payload).decode('ascii')
                else:
                    item['url'] = base_url + image['url'] + (f'?upscale={body.upscale}' if body.upscale > 1 else '')
                data.append(item)
        failed = [task for task in batch['tasks'] if task['status'] != 'success']
        if failed:
            raise HTTPException(502, {'error': failed[0]['error'] or '任务未完成', 'batch_id': batch_id, 'completed_images': len(data)})
        return {'created': int(batch['created']), 'data': data, 'batch_id': batch_id, 'native_pixel_budget': NATIVE_PIXEL_BUDGET, 'note': NATIVE_NOTE}

    async def submit_api(body, request, references, idempotency):
        allowed = {settings.public()['display_model'], 'gpt-image-2', 'gpt-image-2.5', 'gpt-image2.5'}
        if body.model not in allowed:
            raise ValueError('不支持此模型别名，请查询 /v1/models')
        prompt = body.prompt
        if body.size and body.size != 'auto':
            if not re.fullmatch(r'\d{1,5}[x:]\d{1,5}', body.size):
                raise ValueError('size 使用 auto、宽x高或宽:高；只决定宽高比，像素总量固定')
            detail = describe(body.size)
            if not detail:
                raise ValueError('无法解析 size，请使用 auto、宽x高或宽:高')
            prompt += f"\n\n输出图片，宽高比为 {detail['ratio']}，目标输出 {detail['width']}×{detail['height']} 像素，不拉伸、不裁切。"
        client_id = hashlib.sha256(idempotency.encode()).hexdigest() if idempotency else uuid.uuid4().hex
        batch = await run_in_threadpool(service.submit, BatchRequest(client_id=client_id, prompt=prompt, count=body.n, references=references))
        base_url = str(request.base_url).rstrip('/')
        if not body.stream:
            return await api_result(batch['id'], body, base_url)
        async def events():
            operation = asyncio.create_task(api_result(batch['id'], body, base_url))
            try:
                while not operation.done():
                    snapshot = store.batch(batch['id'])
                    payload = {'type': 'image_generation.progress', 'batch_id': batch['id'], 'tasks': [{'index': task['index'], 'status': task['status'], 'stage': task['stage']} for task in snapshot['tasks']]}
                    yield f'data: {json.dumps(payload, ensure_ascii=False)}\n\n'
                    await asyncio.sleep(1)
                try:
                    result = await operation
                except HTTPException as error:
                    result = {'error': error.detail}
                yield f'data: {json.dumps(result, ensure_ascii=False)}\n\ndata: [DONE]\n\n'
            finally:
                if not operation.done():
                    operation.cancel()
        return StreamingResponse(events(), media_type='text/event-stream')

    @app.post('/v1/images/generations')
    async def api_generation(body: ImageRequest, request: Request, idempotency_key: str | None = Header(default=None)):
        return await submit_api(body, request, [], idempotency_key)

    @app.post('/v1/images/edits')
    async def api_edit(request: Request, prompt: str = Form(...), model: str = Form(default='gpt-image-2.5'), n: int = Form(default=1), size: str | None = Form(default=None), upscale: int = Form(default=1), response_format: str = Form(default='b64_json'), stream: bool = Form(default=False), image: list[UploadFile] | None = File(default=None), image_list: list[UploadFile] | None = File(default=None, alias='image[]'), idempotency_key: str | None = Header(default=None)):
        uploads = [*(image or []), *(image_list or [])]
        if not 1 <= len(uploads) <= 12:
            raise ValueError('编辑需要 1 至 12 张参考图')
        body = ImageRequest(prompt=prompt, model=model, n=n, size=size, upscale=upscale, response_format=response_format, stream=stream)
        references = []
        for file in uploads:
            asset = await run_in_threadpool(store.save_asset, await file.read(15 * 1024 * 1024 + 1), file.filename or '参考图')
            references.append(asset['id'])
        return await submit_api(body, request, references, idempotency_key)

    @app.get('/{path:path}')
    def frontend(path: str):
        directory = ROOT / 'dist'
        candidate = (directory / path).resolve()
        if candidate.is_relative_to(directory.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        if path.startswith(('api/', 'v1/', 'files/', 'assets/')):
            raise HTTPException(404, 'Not found')
        if path not in {'', 'index.html'}:
            raise HTTPException(404, 'Not found')
        if (directory / 'index.html').is_file():
            return FileResponse(directory / 'index.html')
        return JSONResponse({'detail': '请先执行 npm run build 构建工作台'}, status_code=503)

    return app
