import json
from unittest.mock import patch

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

    def json(self):
        return self.payload

    def iter_lines(self):
        yield from self.lines

    def close(self):
        pass


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
    with patch('studio.upstream.backend.requests.Session', return_value=transport), patch('studio.upstream.backend.build_legacy_requirements_token', return_value='synthetic-proof'), patch('studio.upstream.backend.time.sleep'):
        result = WebImageProvider(settings).generate('exact user prompt', [fixture_image()], 'fixture-web-model', lambda stage: None)
    assert result == [fixture_image()]
    assert transport.closed
    prepare = next(payload for method, url, payload in transport.calls if url.endswith('/prepare'))
    generation = next(payload for method, url, payload in transport.calls if url.endswith('/f/conversation'))
    assert prepare['json']['model'] == generation['json']['model'] == 'fixture-web-model'
    assert generation['json']['system_hints'] == ['picture_v2']
    assert generation['json']['messages'][0]['content']['parts'][-1] == 'exact user prompt'
    assert generation['json']['messages'][0]['content']['parts'][0]['asset_pointer'] == 'file-service://file-reference'
    assert any(method == 'PUT' for method, _, _ in transport.calls)


def test_web_adapter_does_not_read_arbitrary_local_paths(tmp_path):
    from studio.upstream.backend import OpenAIBackendAPI
    private = tmp_path / 'private.txt'
    private.write_text('not image data')
    backend = object.__new__(OpenAIBackendAPI)
    import pytest
    with pytest.raises(Exception):
        backend._decode_image_base64(str(private))
