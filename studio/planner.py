from decimal import Decimal
from math import ceil, floor, gcd, sqrt
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .resolution import NATIVE_PIXEL_BUDGET, native_dimensions, parse_ratio

RATIOS = ['Adaptive', '1:1', '16:9', '21:9', '4:3', '3:2', '5:4', '2:1', '3:4', '2:3', '4:5', '9:16']
FIDELITY_RULES = [
    '来源图是商品外观的唯一依据，必须保留商品颜色、版型、轮廓、材质、图案、结构、细节、配件、比例和整体设计。',
    '商品本体已有的文字、书法、印章、标签、图案、花纹、边框和包装说明必须原样保留，不得重绘、简化、改写或抹除。',
    '不得凭空添加无关 Logo、水印、二维码、乱码、无关品牌元素或误导性标签。',
]


class Canvas(BaseModel):
    widthCm: str = Field(max_length=32)
    heightCm: str = Field(max_length=32)


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_id: str = Field(min_length=8, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    prompt: str = Field(min_length=1, max_length=20000)
    mode: Literal['count', 'queue', 'per-image'] = 'count'
    count: int = Field(default=1, ge=1, le=10)
    references: list[str] = Field(default_factory=list, max_length=12)
    common_references: list[str] = Field(default_factory=list, max_length=3)
    ratio: str = 'Adaptive'
    custom_size: Canvas | None = None
    # 已废弃：网页生图的像素总量固定在约 1.57 MP（见 studio/resolution.py 的实测
    # 证据），1K/2K/4K 档位无法兑现。字段保留仅为兼容旧客户端，取值不再影响结果。
    image_size: Literal['1K', '2K', '4K'] | None = None
    # 出图后的显式放大倍数，1 表示只要原生输出。放大结果一律标注为非原生像素。
    upscale: int = Field(default=1, ge=1, le=4)
    derivative: bool = False


def calculate_canvas(canvas: Canvas, image_size=None):
    """把厘米画布换算成该平台真实能输出的像素尺寸。

    `image_size` 仅为兼容旧调用而保留，不参与计算：网页生图的像素预算是固定的。
    """
    integers = []
    for value in [canvas.widthCm, canvas.heightCm]:
        normalized = unicodedata.normalize('NFKC', value).strip()
        if not re.fullmatch(r'\d{1,9}(?:\.\d{1,6})?', normalized) or Decimal(normalized) <= 0:
            raise ValueError('宽高须为大于 0 的厘米数，最多 6 位小数')
        integers.append(int(Decimal(normalized) * 1_000_000))
    divisor = gcd(*integers)
    numerator, denominator = [value // divisor for value in integers]
    if max(numerator, denominator) > 3 * min(numerator, denominator):
        raise ValueError('自定义宽高比须在 1:3 至 3:1 之间，不会自动换成其他比例')
    unit_width, unit_height = numerator * 16, denominator * 16
    unit_pixels = unit_width * unit_height
    minimum = max(1, ceil(sqrt(655360 / unit_pixels)))
    maximum = min(floor(3840 / max(unit_width, unit_height)), floor(sqrt(8294400 / unit_pixels)))
    if minimum > maximum:
        raise ValueError('此尺寸无法精确换算为像素，请调整宽高')
    target = NATIVE_PIXEL_BUDGET
    multiplier = max(minimum, min(maximum, floor(sqrt(target / unit_pixels))))
    return {'width': unit_width * multiplier, 'height': unit_height * multiplier, 'ratio': f'{numerator}:{denominator}'}


def infer_canvas(prompt: str, explicit: Canvas | None):
    text = unicodedata.normalize('NFKC', prompt)
    product = re.search(r'商品|产品|包装|瓶身|桌子|柜子', text)
    context = re.search(r'画布|海报尺寸|海报宽|出图尺寸|成品尺寸|设计尺寸', text)
    if product and not context:
        return explicit
    if product:
        text = text[context.start():]
    number = r'([+-]?(?:\d+(?:\.\d*)?|\.\d+))'
    unit = r'(厘米|公分|cm|毫米|mm|米|m|英寸|inch|像素|px)?'
    widths = re.findall(r'(?:宽度|宽)\s*[:=]?\s*' + number + r'\s*' + unit, text, re.I)
    heights = re.findall(r'(?:高度|高)\s*[:=]?\s*' + number + r'\s*' + unit, text, re.I)
    pairs = re.findall(number + r'\s*' + unit + r'\s*[x×*]\s*' + number + r'\s*' + unit, text, re.I) if re.search(r'海报|画布|出图|成品尺寸|设计尺寸', text) else []
    if len(widths) > 1 or len(heights) > 1 or len(pairs) > 1:
        raise ValueError('检测到多组画布尺寸，请分开生成')
    candidates = []
    if widths and heights:
        candidates.append((widths[0][0], widths[0][1], heights[0][0], heights[0][1]))
    candidates.extend(pairs)
    result = explicit
    for width, width_unit, height, height_unit in candidates:
        if any(value.lower() not in {'', '厘米', '公分', 'cm'} for value in [width_unit, height_unit]):
            raise ValueError('自定义画布使用厘米，请先换算单位')
        detected = Canvas(widthCm=width, heightCm=height)
        if result and calculate_canvas(result)['ratio'] != calculate_canvas(detected)['ratio']:
            raise ValueError('提示词尺寸与自定义宽高不一致，请先统一')
        result = result or detected
    if result:
        for width, height in re.findall(r'(?:画布比例|宽高比|海报比例)\s*[:=]?\s*(\d+)\s*:\s*(\d+)', text):
            if calculate_canvas(result)['ratio'] != calculate_canvas(Canvas(widthCm=width, heightCm=height))['ratio']:
                raise ValueError('提示词比例与自定义宽高不一致')
    return result


def plan(request: BatchRequest, assets: dict):
    if request.ratio not in RATIOS:
        raise ValueError('不支持的画布比例')
    if request.common_references and request.mode != 'per-image':
        raise ValueError('通用参考图只用于逐图修改，请检查参考图设置')
    if sum(assets.get(item, {}).get('bytes', 0) for item in request.common_references) > 6 * 1024 * 1024:
        raise ValueError('通用参考图合计最多 6 MB')
    for asset_id in request.references + request.common_references:
        if asset_id not in assets:
            raise ValueError('参考图不存在，请重新上传')
    if request.mode == 'per-image':
        if not 1 <= len(request.references) <= 10:
            raise ValueError('逐图修改须上传 1 至 10 张来源图')
        prompts = [request.prompt.strip()] * len(request.references)
    elif request.mode == 'queue':
        prompts = [line.strip() for line in request.prompt.splitlines() if line.strip()]
    else:
        prompts = [request.prompt.strip()] * request.count
    if not prompts or len(prompts) > 10 or any(not prompt for prompt in prompts):
        raise ValueError('请填写有效提示词，每批最多 10 个任务，不会截断任务')
    if request.derivative and not request.references:
        raise ValueError('继续修改需要来源图')
    tasks = []
    for index, prompt in enumerate(prompts):
        references = [request.references[index]] if request.mode == 'per-image' else list(request.references)
        references = list(dict.fromkeys(references + request.common_references))
        if len(references) > 12 or sum(assets[item]['bytes'] for item in references) > 15 * 1024 * 1024:
            raise ValueError('每个任务的参考图最多 12 张、合计 15 MB')
        effective = prompt
        if request.derivative:
            effective = '[用户修改要求]\n' + prompt + '\n\n[商品保真规则]\n' + '\n'.join(FIDELITY_RULES)
        canvas = infer_canvas(prompt, request.custom_size)
        if canvas:
            dimensions = calculate_canvas(canvas)
            effective += f"\n\n画布规格：宽{canvas.widthCm}厘米、高{canvas.heightCm}厘米，整张图片宽:高={dimensions['ratio']}，输出{dimensions['width']}×{dimensions['height']}像素。按此画布直接构图，不拉伸、不压扁、不裁切或添加边框凑比例；保留用户全部指定文案，不把画布尺寸当成商品尺寸或新增画面文案。"
        elif request.ratio != 'Adaptive':
            parsed = parse_ratio(request.ratio)
            if parsed:
                width, height = native_dimensions(*parsed)
                effective += f'\n\n输出图片，宽高比为 {request.ratio}，目标输出 {width}×{height} 像素，不拉伸、不裁切。'
            else:
                effective += f'\n\n输出图片，宽高比为 {request.ratio}。'
        tasks.append({'index': index, 'prompt': prompt, 'effective_prompt': effective, 'references': references})
    return tasks
