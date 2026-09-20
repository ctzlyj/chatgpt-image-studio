from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading
import time
import uuid

from .planner import plan
from .provider import public_error


class StudioService:
    def __init__(self, store, settings, provider):
        self.store = store
        self.settings = settings
        self.provider = provider
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='web-image')
        self.submit_lock = threading.Lock()

    def submit(self, request):
        planned = plan(request, self.store.assets())
        fingerprint = hashlib.sha256(request.model_dump_json(exclude={'client_id'}).encode()).hexdigest()
        with self.submit_lock, self.store.lock:
            existing = self.store.connection.execute('SELECT fingerprint FROM batches WHERE id=?', (request.client_id,)).fetchone()
            if existing:
                if existing['fingerprint'] != fingerprint:
                    raise ValueError('同一请求编号的内容发生变化，请使用新请求编号')
                return self.store.batch(request.client_id)
            if not self.settings.public()['configured']:
                raise ValueError('请先在连接设置中保存 ChatGPT 网页登录凭证')
            if len([task for task in self.store.tasks() if task['status'] in {'queued', 'running'}]) + len(planned) > 50:
                raise ValueError('待处理任务已达 50 个，请等待完成后再提交')
            settings = self.settings.public()
            batch = {'id': request.client_id, 'created': time.time(), 'request': request.model_dump(), 'display_model': settings['display_model'], 'upstream_model': settings['upstream_model']}
            tasks = [dict(item, id=uuid.uuid4().hex, batch_id=batch['id'], status='queued', stage='等待处理', results=[], error='', updated=time.time()) for item in planned]
            self.store.connection.execute('INSERT INTO batches VALUES (?,?,?,?)', (batch['id'], fingerprint, json.dumps(batch, ensure_ascii=False), batch['created']))
            self.store.connection.executemany('INSERT INTO tasks VALUES (?,?,?)', [(task['id'], batch['id'], json.dumps(task, ensure_ascii=False)) for task in tasks])
            self.store.connection.commit()
            for task in tasks:
                self.executor.submit(self._run, task, batch['upstream_model'])
        return self.store.batch(batch['id'])

    def _run(self, task, model):
        with self.store.lock:
            current = next(item for item in self.store.tasks(task['batch_id']) if item['id'] == task['id'])
            if current['status'] != 'queued':
                return
            self.store.update_task(task['id'], status='running', stage='连接 ChatGPT')
        try:
            last_stage = ''
            def progress(stage):
                nonlocal last_stage
                if stage != last_stage:
                    self.store.update_task(task['id'], stage=stage)
                    last_stage = stage
            images = self.provider.generate(task['effective_prompt'], [self.store.asset_bytes(asset_id) for asset_id in task['references']], model, progress)
            if not images:
                raise RuntimeError('网页没有返回图片')
            results = [self.store.save_asset(image, f'作品-{task["index"] + 1}', 'result') for image in images]
            self.store.update_task(task['id'], status='success', stage='已保存到本机', results=results)
        except Exception as error:
            self.store.update_task(task['id'], status='failed', stage='未完成', error=public_error(error))

    def cancel(self, batch_id):
        with self.store.lock:
            for task in self.store.tasks(batch_id):
                if task['status'] == 'queued':
                    self.store.update_task(task['id'], status='cancelled', stage='已取消')
        return self.store.batch(batch_id)

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
