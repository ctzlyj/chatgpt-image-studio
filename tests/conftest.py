from io import BytesIO
import time

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import pytest

from studio.app import create_app
from studio.settings import SettingsUpdate


def fixture_image():
    output = BytesIO()
    image = Image.new('RGB', (512, 640), '#ece7da')
    drawing = ImageDraw.Draw(image)
    drawing.rounded_rectangle((100, 140, 410, 450), radius=30, fill='#3f6151')
    drawing.text((130, 500), 'LOCAL TEST FIXTURE', fill='#354533')
    image.save(output, format='PNG')
    return output.getvalue()


class FakeProvider:
    calls = []

    def __init__(self, settings):
        self.settings = settings
        self.calls = []

    def check(self):
        return {'ok': True, 'message': 'Test fixture connection'}

    def generate(self, prompt, references, model, progress):
        self.calls.append((prompt, references, model))
        progress('离线测试生成中')
        if 'FAIL_FIXTURE' in prompt:
            raise RuntimeError('网页测试失败')
        if 'SLOW_FIXTURE' in prompt:
            time.sleep(0.5)
        return [fixture_image()]


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, FakeProvider)
    app.state.settings.update(SettingsUpdate(access_token='offline-test-placeholder'))
    with TestClient(app, base_url='http://127.0.0.1') as connection:
        result = connection.get('/api/bootstrap').json()
        connection.headers['x-studio-session'] = result['session']
        yield connection


def wait_batch(client, batch_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get(f'/api/batches/{batch_id}').json()
        if all(task['status'] not in {'queued', 'running'} for task in result['tasks']):
            return result
        time.sleep(0.02)
    raise AssertionError('Test batch did not finish')
