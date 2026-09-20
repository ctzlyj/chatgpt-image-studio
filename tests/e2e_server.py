import tempfile
from pathlib import Path

import uvicorn

from studio.app import create_app
from studio.settings import SettingsUpdate
from .conftest import FakeProvider


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='image-studio-e2e-') as directory:
        app = create_app(Path(directory), FakeProvider)
        app.state.settings.update(SettingsUpdate(access_token='offline-test-placeholder'))
        uvicorn.run(app, host='127.0.0.1', port=8779, access_log=False, log_level='error')
