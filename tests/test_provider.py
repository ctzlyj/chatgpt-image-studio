import json
from unittest.mock import patch

import pytest

from studio.provider import WebImageProvider
from studio.settings import Settings, SettingsUpdate
from .conftest import fixture_image


class FakeResponse:
    status_code = 200
    text = '<html></html>'
    content = fixture_image()

    def __init__(self, payload=None, lines=None):
        self.payload = payload or {}
        self.lines = lines or []
        self.headers = {}
        self.closed = False

    def json(self):
        return self.payload

    def iter_lines(self):
        yield from self.lines

    def iter_content(self, chunk_size=65536):
        yield self.content

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, **kwargs):
        self.headers = {}
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append(('GET', url, kwargs))
        if '/conversation/' in url:
            return FakeResponse({'mapping': {'output': {'message': {'author': {'role': 'tool'}, 'metadata': {'async_task_type': 'image_gen'}, 'content': {'content_type': 'multimodal_text', 'parts': [{'asset_pointer': 'file-service://file-fixture'}]}}}}})
        if url.endswith('/download'):
            return FakeResponse({'download_url': 'https://example.test/fixture.png'})
        return FakeResponse()

    def post(self, url, **kwargs):
        self.calls.append(('POST', url, kwargs))
        if url.endswith('/chat-requirements'):
            return FakeResponse({'token': 'synthetic-requirement'})
        if url.endswith('/prepare'):
            return FakeResponse({'conduit_token': 'synthetic-conduit'})
        if url.endswith('/files'):
            return FakeResponse({'file_id': 'file-reference', 'upload_url': 'https://example.test/upload'})
        if url.endswith('/f/conversation'):
            return FakeResponse(lines=[b'data: ' + json.dumps({'conversation_id': 'conversation-fixture'}).encode(), b'data: [DONE]'])
        return FakeResponse()

    def put(self, url, **kwargs):
        self.calls.append(('PUT', url, kwargs))
        return FakeResponse()

    def close(self):
        self.closed = True


def test_actual_web_adapter_request_chain_with_fixture_transport(tmp_path):
    settings = Settings(tmp_path)
    settings.update(SettingsUpdate(access_token='offline-test-placeholder', upstream_model='fixture-web-model'))
    transport = FakeSession()
    assets = FakeSession()
    with patch('studio.upstream.backend.requests.Session', side_effect=[transport, assets]) as factory, patch('studio.upstream.backend.build_legacy_requirements_token', return_value='synthetic-proof'), patch('studio.upstream.backend.time.sleep'):
        result = WebImageProvider(settings).generate('exact user prompt', [fixture_image()], 'fixture-web-model', lambda stage: None)
    assert result == [fixture_image()]
    assert transport.closed
    assert assets.closed
    assert all(call.kwargs['allow_redirects'] is False for call in factory.call_args_list)
    assert 'Authorization' in transport.headers
    assert not assets.headers
    assert all(url.startswith('https://chatgpt.com/') for _, url, _ in transport.calls)
    assert all(not url.startswith('https://chatgpt.com/') for _, url, _ in assets.calls)
    for _, _, options in assets.calls:
        assert not any(key.lower() in {'authorization', 'cookie'} or key.lower().startswith('oai-') for key in options.get('headers', {}))
        assert options['discard_cookies'] is True
    prepare = next(payload for method, url, payload in transport.calls if url.endswith('/prepare'))
    generation = next(payload for method, url, payload in transport.calls if url.endswith('/f/conversation'))
    assert prepare['json']['model'] == generation['json']['model'] == 'fixture-web-model'
    assert generation['json']['system_hints'] == ['picture_v2']
    assert generation['json']['messages'][0]['content']['parts'][-1] == 'exact user prompt'
    assert generation['json']['messages'][0]['content']['parts'][0]['asset_pointer'] == 'file-service://file-reference'
    assert any(method == 'PUT' for method, _, _ in assets.calls)


def test_web_adapter_does_not_read_arbitrary_local_paths(tmp_path):
    from studio.upstream.backend import OpenAIBackendAPI
    private = tmp_path / 'private.txt'
    private.write_text('not image data')
    backend = object.__new__(OpenAIBackendAPI)
    with pytest.raises(Exception):
        backend._decode_image_base64(str(private))


@pytest.mark.parametrize('url', ['http://example.test/image', 'file:///image.png', 'https://name:password@example.test/image', 'https:///image'])
def test_unsafe_asset_urls_are_rejected(url):
    from studio.upstream.backend import validate_asset_url
    with pytest.raises(RuntimeError, match='不安全'):
        validate_asset_url(url)


@pytest.mark.parametrize('declared_size', ['', '11'])
def test_image_size_is_bounded_and_response_closed(declared_size):
    from studio.upstream.backend import OpenAIBackendAPI
    backend = object.__new__(OpenAIBackendAPI)
    backend.asset_session = FakeSession()
    response = FakeResponse()
    response.headers = {'content-length': declared_size}
    response.content = b'01234567890'
    with patch.object(backend.asset_session, 'get', return_value=response), patch('studio.upstream.backend.MAX_IMAGE_BYTES', 10):
        with pytest.raises(RuntimeError, match='40 MB'):
            backend.download_image_bytes(['https://example.test/image'])
    assert response.closed


def test_failed_login_check_closes_both_sessions(tmp_path):
    settings = Settings(tmp_path)
    settings.update(SettingsUpdate(access_token='offline-test-placeholder'))
    transport = FakeSession()
    assets = FakeSession()
    response = FakeResponse()
    response.status_code = 401
    with patch('studio.upstream.backend.requests.Session', side_effect=[transport, assets]), patch.object(transport, 'get', return_value=response):
        with pytest.raises(RuntimeError, match='失效'):
            WebImageProvider(settings).check()
    assert transport.closed and assets.closed


def test_real_transport_separates_credentials_and_blocks_redirects():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    from studio.upstream.backend import OpenAIBackendAPI

    observed = []

    class Capture(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append({'path': self.path, 'authorized': bool(self.headers.get('Authorization')),
                             'cookie': bool(self.headers.get('Cookie')), 'device': bool(self.headers.get('OAI-Device-Id'))})
            self.send_response(302 if self.path == '/redirect' else 200)
            if self.path == '/redirect':
                self.send_header('Location', '/unexpected-destination')
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'ok')

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Capture)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    backend = OpenAIBackendAPI(access_token='synthetic-fixture')
    backend.session.cookies.set('fixture', 'synthetic-cookie', domain='127.0.0.1')
    base_url = f'http://127.0.0.1:{server.server_port}'
    try:
        backend.session.get(base_url + '/control', timeout=3)
        backend.asset_session.get(base_url + '/asset', timeout=3, discard_cookies=True)
        assert backend.session.get(base_url + '/redirect', timeout=3).status_code == 302
        assert backend.asset_session.get(base_url + '/redirect', timeout=3).status_code == 302
    finally:
        backend.close()
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()
    assert observed[0] == {'path': '/control', 'authorized': True, 'cookie': True, 'device': True}
    assert observed[1] == {'path': '/asset', 'authorized': False, 'cookie': False, 'device': False}
    assert len(observed) == 4
