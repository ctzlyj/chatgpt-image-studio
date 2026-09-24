"""网页生图分辨率事实的回归测试。

这些断言锁住 2026-09-24 从授权账号图库测得的结论：像素总量固定约 1.57 MP，
只有宽高比可控。如果平台以后真的放开更大分辨率，这里会红，提醒我们重新取证，
而不是让界面继续给出无法兑现的承诺。
"""
import pytest

from studio.planner import BatchRequest, Canvas, calculate_canvas, plan
from studio.resolution import (
    NATIVE_PIXEL_BUDGET,
    NATIVE_PIXEL_RANGE,
    describe,
    is_native,
    native_dimensions,
    parse_pixels,
    parse_ratio,
    table,
)


def test_measured_dimensions_match_the_account_evidence():
    """实测样本：1254x1254、1672x941、1008x1561、1448x1086 都应被算出来。"""
    assert native_dimensions(1, 1) == (1254, 1254)
    assert native_dimensions(16, 9) == (1673, 941)
    assert native_dimensions(4, 3) == (1448, 1086)
    assert native_dimensions(9, 16) == (941, 1673)


def test_every_ratio_lands_inside_the_measured_pixel_range():
    for row in table(['1:1', '16:9', '21:9', '4:3', '3:2', '5:4', '2:1', '3:4', '2:3', '4:5', '9:16']):
        assert is_native(row['width'], row['height']), row


def test_pixel_budget_is_not_silently_raised():
    low, high = NATIVE_PIXEL_RANGE
    assert low < NATIVE_PIXEL_BUDGET < high


def test_extreme_ratios_are_rejected_rather_than_guessed():
    with pytest.raises(ValueError):
        native_dimensions(20, 1)
    with pytest.raises(ValueError):
        native_dimensions(0, 5)


def test_ratio_and_pixel_inputs_are_told_apart():
    assert parse_ratio('16:9') == (16, 9)
    assert parse_ratio('1920x1080') == (16, 9)
    assert parse_ratio('1920×1080') == (16, 9)
    assert parse_ratio('nonsense') is None
    assert parse_pixels('3840x2160') == (3840, 2160)
    assert parse_pixels('16:9') is None


def test_a_4k_request_is_reported_as_capped_not_fulfilled():
    detail = describe('3840x2160')
    assert detail['ratio'] == '16:9'
    assert (detail['width'], detail['height']) == (1673, 941)
    assert detail['requested_width'] == 3840
    assert detail['capped'] is True
    assert detail['megapixels'] < 1.6


def test_plain_ratio_request_is_not_flagged_as_capped():
    detail = describe('16:9')
    assert detail['capped'] is False
    assert detail['requested_width'] is None


def test_ratio_prompt_states_the_real_output_size():
    tasks = plan(BatchRequest(client_id='fixture-ratio', prompt='a mug', ratio='16:9'), {})
    assert '宽高比为 16:9' in tasks[0]['effective_prompt']
    assert '1673×941' in tasks[0]['effective_prompt']


def test_centimetre_canvas_targets_the_native_budget_only():
    """厘米画布不再声称 2K/4K，换算结果必须落在原生像素区间。"""
    dimensions = calculate_canvas(Canvas(widthCm='20', heightCm='30'))
    assert dimensions['ratio'] == '2:3'
    assert is_native(dimensions['width'], dimensions['height'])
    assert dimensions['width'] % 16 == 0 and dimensions['height'] % 16 == 0


def test_legacy_image_size_field_no_longer_changes_the_result():
    baseline = calculate_canvas(Canvas(widthCm='20', heightCm='20'))
    for legacy in ['1K', '2K', '4K', None]:
        assert calculate_canvas(Canvas(widthCm='20', heightCm='20'), legacy) == baseline


def test_legacy_clients_sending_image_size_are_still_accepted():
    body = BatchRequest(client_id='fixture-legacy', prompt='a mug', image_size='4K', ratio='1:1')
    tasks = plan(body, {})
    assert '1254×1254' in tasks[0]['effective_prompt']
