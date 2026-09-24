"""交付放大的回归测试：放大结果必须被如实标注，绝不冒充原生像素。

这些断言守住三条底线：原生图不被替换、Lanczos 不许自称 AI、界面与 API
都能拿到 “原生尺寸 / 交付尺寸 / 放大倍数” 三个事实。
"""
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from studio import upscale as upscale_module
from studio.upscale import (
    ALGORITHM_BACKEND,
    AI_BACKEND,
    MAX_FACTOR,
    available_backend,
    capability,
    upscale_png,
)
from .conftest import fixture_image, wait_batch


def png_size(data):
    with Image.open(BytesIO(data)) as image:
        return image.size


def large_png(width, height):
    output = BytesIO()
    Image.new('RGB', (width, height), '#d8d2c4').save(output, format='PNG')
    return output.getvalue()


def test_factor_one_returns_the_untouched_native_bytes():
    native = fixture_image()
    payload, marker = upscale_png(native, 1)
    assert payload == native
    assert marker['upscaled'] is False
    assert marker['backend'] == 'native'
    assert (marker['width'], marker['height']) == (512, 640)


def test_two_times_doubles_both_edges_and_keeps_the_native_size_on_record():
    payload, marker = upscale_png(fixture_image(), 2)
    assert png_size(payload) == (1024, 1280)
    assert marker['upscaled'] is True
    assert (marker['native_width'], marker['native_height']) == (512, 640)
    assert (marker['width'], marker['height']) == (1024, 1280)


def test_algorithm_upscaling_is_never_advertised_as_ai(monkeypatch):
    monkeypatch.setattr(upscale_module, 'upscaler_path', lambda: None)
    assert available_backend() == ALGORITHM_BACKEND
    _, marker = upscale_png(fixture_image(), 2)
    assert marker['backend'] == ALGORITHM_BACKEND
    assert 'Lanczos' in marker['label']
    assert '非原生像素' in marker['label']
    assert 'AI' not in marker['label']


def test_capability_tells_the_interface_what_is_actually_installed(monkeypatch):
    monkeypatch.setattr(upscale_module, 'upscaler_path', lambda: None)
    detail = capability()
    assert detail['ai_available'] is False
    assert detail['backend'] == ALGORITHM_BACKEND
    assert detail['max_factor'] == MAX_FACTOR
    assert '非原生像素' in detail['label']


def test_out_of_range_factors_are_refused():
    for bad in [0, -1, MAX_FACTOR + 1, 'two', None]:
        with pytest.raises(ValueError):
            upscale_png(fixture_image(), bad)


def test_the_environment_switch_can_force_the_algorithm_backend(monkeypatch):
    monkeypatch.setenv('IMAGE_STUDIO_UPSCALER', 'off')
    assert upscale_module.upscaler_path() is None
    monkeypatch.setenv('IMAGE_STUDIO_UPSCALER', 'lanczos')
    assert upscale_module.upscaler_path() is None


def test_the_real_backend_is_invoked_with_the_photo_model_and_models_dir(monkeypatch, tmp_path):
    executable = tmp_path / 'realesrgan-ncnn-vulkan.exe'
    executable.write_bytes(b'fixture')
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        source = Path(command[command.index('-i') + 1])
        target = Path(command[command.index('-o') + 1])
        with Image.open(source) as source_image:
            source_image.resize((source_image.width * 2, source_image.height * 2), Image.Resampling.LANCZOS).save(target, format='PNG')
        return None

    monkeypatch.setattr(upscale_module, 'upscaler_path', lambda: executable)
    monkeypatch.setattr(upscale_module.subprocess, 'run', fake_run)
    payload, marker = upscale_png(fixture_image(), 2)
    assert marker['backend'] == AI_BACKEND
    assert 'AI 超分' in marker['label']
    assert png_size(payload) == (1024, 1280)
    command = commands[0]
    assert command[command.index('-n') + 1] == 'realesrgan-x4plus'
    assert Path(command[command.index('-m') + 1]) == executable.parent / 'models'


def test_oversized_output_is_refused_before_allocating_it():
    with pytest.raises(ValueError) as error:
        upscale_png(large_png(3200, 3200), 4)
    assert '4000 万像素' in str(error.value)


def test_resolution_endpoint_publishes_the_measured_facts(client):
    detail = client.get('/api/resolution').json()
    assert 1_560_000 < detail['native_pixel_budget'] < 1_590_000
    ratios = {row['ratio']: row for row in detail['ratios']}
    assert (ratios['1:1']['width'], ratios['1:1']['height']) == (1254, 1254)
    assert (ratios['16:9']['width'], ratios['16:9']['height']) == (1673, 941)
    assert detail['upscale']['max_factor'] == MAX_FACTOR
    assert '非原生像素' in detail['upscale']['label']


def test_file_endpoint_labels_the_enlarged_download(client):
    batch = client.post('/api/batches', json={'client_id': 'upscale-file-001', 'prompt': 'a mug'}).json()
    result = wait_batch(client, batch['id'])
    url = result['tasks'][0]['results'][0]['url']

    native = client.get(url)
    assert png_size(native.content) == (512, 640)
    assert 'X-Image-Upscale-Factor' not in native.headers

    enlarged = client.get(url, params={'upscale': 2, 'download': 'true'})
    assert png_size(enlarged.content) == (1024, 1280)
    assert enlarged.headers['X-Image-Upscale-Factor'] == '2'
    assert enlarged.headers['X-Image-Native-Size'] == '512x640'
    assert f'upscaled2x-{enlarged.headers["X-Image-Upscale-Backend"]}.png' in enlarged.headers['content-disposition']

    assert client.get(url, params={'upscale': 2}).content != native.content
    assert client.get(url).content == native.content


def api_headers(client):
    return {'Authorization': 'Bearer ' + client.post('/api/local-api-key').json()['api_key']}


def test_openai_response_reports_native_and_delivered_size(client):
    headers = api_headers(client)
    response = client.post('/v1/images/generations', headers=headers, json={'prompt': 'fixture upscale', 'upscale': 2})
    assert response.status_code == 200
    body = response.json()
    assert 1_560_000 < body['native_pixel_budget'] < 1_590_000
    item = body['data'][0]
    assert item['native_size'] == '512x640'
    assert item['size'] == '1024x1280'
    assert item['upscale']['factor'] == 2
    assert '非原生像素' in item['upscale']['label']


def test_url_format_points_at_the_labelled_enlarged_file(client):
    headers = api_headers(client)
    body = client.post(
        '/v1/images/generations',
        headers=headers,
        json={'prompt': 'fixture upscale url', 'upscale': 2, 'response_format': 'url'},
    ).json()
    assert body['data'][0]['url'].endswith('?upscale=2')


def test_default_request_stays_native_and_out_of_range_upscale_is_rejected(client):
    headers = api_headers(client)
    body = client.post('/v1/images/generations', headers=headers, json={'prompt': 'fixture native'}).json()
    item = body['data'][0]
    assert item['size'] == item['native_size'] == '512x640'
    assert item['upscale']['upscaled'] is False
    assert client.post('/v1/images/generations', headers=headers, json={'prompt': 'x', 'upscale': 9}).status_code == 422


def test_edit_endpoint_accepts_upscale_and_labels_the_result(client):
    headers = api_headers(client)
    response = client.post(
        '/v1/images/edits',
        headers=headers,
        data={'prompt': 'edit upscale fixture', 'upscale': '2'},
        files=[('image[]', ('ref.png', fixture_image(), 'image/png'))],
    )
    assert response.status_code == 200
    item = response.json()['data'][0]
    assert item['native_size'] == '512x640'
    assert item['size'] == '1024x1280'
    assert item['upscale']['factor'] == 2
