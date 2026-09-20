import json
import hashlib
import sqlite3
import threading
import time
import uuid
from io import BytesIO

from PIL import Image, UnidentifiedImageError


class Store:
    def __init__(self, directory):
        self.directory = directory
        self.images = directory / 'images'
        self.images.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(directory / 'studio.db', check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, payload TEXT NOT NULL);
        ''')
        with self.lock:
            for row in self.connection.execute('SELECT id,payload FROM tasks').fetchall():
                task = json.loads(row['payload'])
                if task['status'] in {'queued', 'running'}:
                    task.update(status='interrupted', stage='服务已重启', error='未完成任务已中断；请先核对网页结果，再决定是否重试')
                    self.connection.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(task), row['id']))
            self.connection.commit()

    def save_asset(self, data, name='image', kind='reference'):
        limit = 15 if kind == 'reference' else 40
        if len(data) > limit * 1024 * 1024:
            raise ValueError(f'图片超过 {limit} MB 限制')
        try:
            with Image.open(BytesIO(data)) as image:
                if image.format not in {'PNG', 'JPEG', 'WEBP'} or image.width * image.height > 40_000_000:
                    raise ValueError('仅支持 PNG、JPEG、WebP 图片，最多 4000 万像素')
                image.load()
                output = BytesIO()
                image.convert('RGBA' if image.mode in {'RGBA', 'LA', 'P'} else 'RGB').save(output, format='PNG')
                width, height = image.size
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
            raise ValueError('无法读取图片，请上传有效 PNG、JPEG 或 WebP 文件') from error
        asset_id = uuid.uuid4().hex
        payload = output.getvalue()
        if len(payload) > 40 * 1024 * 1024:
            raise ValueError('图片解码后体积过大')
        digest = hashlib.sha256(payload).hexdigest()
        if kind == 'reference':
            for existing in self.assets().values():
                if existing.get('sha256') == digest:
                    return existing
        (self.images / f'{asset_id}.png').write_bytes(payload)
        item = {'id': asset_id, 'name': str(name)[:160], 'width': width, 'height': height, 'bytes': len(payload), 'url': f'/files/{asset_id}.png', 'kind': kind, 'sha256': digest}
        with self.lock:
            self.connection.execute('INSERT INTO assets VALUES (?,?)', (asset_id, json.dumps(item)))
            self.connection.commit()
        return item

    def assets(self):
        with self.lock:
            return {row['id']: json.loads(row['payload']) for row in self.connection.execute('SELECT * FROM assets').fetchall()}

    def asset_bytes(self, asset_id):
        if asset_id not in self.assets():
            raise ValueError('参考图不存在')
        return (self.images / f'{asset_id}.png').read_bytes()

    def tasks(self, batch_id=None):
        with self.lock:
            rows = self.connection.execute('SELECT payload FROM tasks' + (' WHERE batch_id=?' if batch_id else ''), (batch_id,) if batch_id else ()).fetchall()
            return [json.loads(row['payload']) for row in rows]

    def update_task(self, task_id, **values):
        with self.lock:
            row = self.connection.execute('SELECT payload FROM tasks WHERE id=?', (task_id,)).fetchone()
            item = json.loads(row['payload'])
            item.update(values, updated=time.time())
            self.connection.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(item, ensure_ascii=False), task_id))
            self.connection.commit()
            return item

    def batches(self):
        with self.lock:
            rows = self.connection.execute('SELECT * FROM batches ORDER BY created DESC LIMIT 100').fetchall()
            return [dict(json.loads(row['payload']), tasks=self.tasks(row['id'])) for row in rows]

    def batch(self, batch_id):
        with self.lock:
            row = self.connection.execute('SELECT payload FROM batches WHERE id=?', (batch_id,)).fetchone()
            if not row:
                raise ValueError('批次不存在')
            return dict(json.loads(row['payload']), tasks=self.tasks(batch_id))
