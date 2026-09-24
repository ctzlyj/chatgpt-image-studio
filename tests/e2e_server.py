import tempfile
import os
from pathlib import Path

import uvicorn

from studio.app import create_app
from studio.settings import SettingsUpdate
from .conftest import FakeProvider
from .test_accounts import inspector
from .test_account_sources import Session
from unittest.mock import patch


if __name__ == '__main__':
    os.environ.setdefault('IMAGE_STUDIO_UPSCALER', 'lanczos')
    with tempfile.TemporaryDirectory(prefix='image-studio-e2e-') as directory:
        app = create_app(Path(directory), FakeProvider, inspector)
        app.state.settings.update(SettingsUpdate(access_token='offline-test-placeholder'))
        with patch('studio.account_sources.requests.Session', Session):
            uvicorn.run(app, host='127.0.0.1', port=8779, access_log=False, log_level='error')
