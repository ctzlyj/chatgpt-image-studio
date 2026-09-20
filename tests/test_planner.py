import pytest

from studio.planner import BatchRequest, Canvas, calculate_canvas, plan


def request(**values):
    return BatchRequest(client_id='fixture-request', prompt='  user wording  ', **values)


def test_plain_prompt_is_not_augmented():
    tasks = plan(request(count=3), {})
    assert len(tasks) == 3
    assert all(task['effective_prompt'] == 'user wording' for task in tasks)


def test_queue_does_not_discard_any_work():
    body = request(mode='queue')
    body.prompt = ' first\n\n second\r\n third '
    assert [task['prompt'] for task in plan(body, {})] == ['first', 'second', 'third']
    body.prompt = '\n'.join(['task'] * 11)
    with pytest.raises(ValueError):
        plan(body, {})


def test_per_image_references_do_not_mix_products():
    assets = {name: {'bytes': 20} for name in ['first', 'second', 'common']}
    tasks = plan(request(mode='per-image', references=['first', 'second'], common_references=['common']), assets)
    assert [task['references'] for task in tasks] == [['first', 'common'], ['second', 'common']]


def test_reference_alone_does_not_add_fidelity_words():
    result = plan(request(references=['first']), {'first': {'bytes': 20}})[0]
    assert result['effective_prompt'] == 'user wording'


def test_derivative_keeps_fidelity_rules():
    result = plan(request(references=['first'], derivative=True), {'first': {'bytes': 20}})[0]
    assert '[用户修改要求]' in result['effective_prompt']
    assert '商品保真规则' in result['effective_prompt']
    assert '不得重绘、简化、改写或抹除' in result['effective_prompt']


@pytest.mark.parametrize('width,height,ratio', [('20', '30', '2:3'), ('10.5', '7', '3:2'), ('1', '1', '1:1')])
def test_exact_canvas(width, height, ratio):
    result = calculate_canvas(Canvas(widthCm=width, heightCm=height))
    assert result['ratio'] == ratio
    assert result['width'] % 16 == 0 and result['height'] % 16 == 0


@pytest.mark.parametrize('width,height', [('0', '2'), ('-1', '2'), ('20', '1'), ('nan', '2')])
def test_invalid_canvas_is_rejected(width, height):
    with pytest.raises(ValueError):
        calculate_canvas(Canvas(widthCm=width, heightCm=height))


def test_inferred_canvas_and_product_dimension_safety():
    body = request()
    body.prompt = '海报画布宽20厘米，高30厘米'
    assert '画布规格' in plan(body, {})[0]['effective_prompt']
    body.prompt = '商品宽20厘米，高30厘米'
    assert plan(body, {})[0]['effective_prompt'] == body.prompt
    body.prompt = '海报画布宽20厘米，高30厘米'
    body.custom_size = Canvas(widthCm='30', heightCm='20')
    with pytest.raises(ValueError):
        plan(body, {})


def test_reference_budget_and_missing_references():
    with pytest.raises(ValueError):
        plan(request(references=['unknown']), {})
    with pytest.raises(ValueError):
        plan(request(references=['large']), {'large': {'bytes': 16 * 1024 * 1024}})
