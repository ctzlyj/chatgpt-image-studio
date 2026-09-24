"""ChatGPT 网页生图的真实分辨率能力，以及诚实的尺寸换算。

实测结论（2026-09-24，授权账号只读接口取证，未消耗生图额度）：

1. `/backend-api/my/recent/image_gen` 返回的最近 100 张出图，像素总量全部落在
   1.571-1.574 MP 区间，无一例外；宽高比从 2135x737 到 794x1981 自由分布。
2. 网页协议里的 `generation.gen_size`（smimage / image）与 `gen_size_v2`
   （16 / 24 / 32 / 48）是算力档位而非分辨率档位：同一个正方形请求在 16、24、
   32、48 四档下都返回 1254x1254。
3. 图库条目的 `encodings` 只有 thumbnail，`encodings.source` 为 null，不存在
   更高分辨率的原图编码。

因此 chatgpt.com 网页生图不存在 4K 通道。能控制的只有宽高比，像素总量固定。
本模块把这个事实变成可计算的数字，让界面和提示词说真话，而不是给用户一个
永远无法兑现的 “4K” 选项。
"""
from math import gcd, sqrt
import re
import unicodedata

#: 实测像素预算。1254x1254=1572516、1672x941=1573352、1008x1561=1573488、
#: 2135x737=1573495、1566x1005=1573830，取该量级作为换算基准。
NATIVE_PIXEL_BUDGET = 1_573_500

#: 单张原生出图的像素区间，用于判断一张图是否确实来自网页原生渲染。
NATIVE_PIXEL_RANGE = (1_560_000, 1_590_000)

NATIVE_NOTE = '网页生图像素总量固定约 1.57 MP，只能改宽高比，不能改总像素。'


def native_dimensions(width_units, height_units, budget=NATIVE_PIXEL_BUDGET):
    """按给定宽高比，算出该像素预算下能拿到的最大宽高。"""
    width_units = float(width_units)
    height_units = float(height_units)
    if width_units <= 0 or height_units <= 0:
        raise ValueError('宽高比必须为正数')
    if max(width_units, height_units) / min(width_units, height_units) > 8:
        raise ValueError('宽高比超出 8:1，网页生图不会稳定输出')
    scale = sqrt(budget / (width_units * height_units))
    return max(1, round(width_units * scale)), max(1, round(height_units * scale))


def parse_ratio(text):
    """把 '16:9'、'1920x1080'、'1920×1080' 解析成一对互质比例数字。"""
    normalized = unicodedata.normalize('NFKC', str(text or '')).strip().lower().replace('×', 'x')
    match = re.fullmatch(r'(\d{1,5})\s*[:x*]\s*(\d{1,5})', normalized)
    if not match:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        return None
    divisor = gcd(width, height)
    return width // divisor, height // divisor


def parse_pixels(text):
    """当输入本身就是像素尺寸时返回原始像素；纯比例返回 None。"""
    normalized = unicodedata.normalize('NFKC', str(text or '')).strip().lower().replace('×', 'x')
    match = re.fullmatch(r'(\d{2,5})\s*x\s*(\d{2,5})', normalized)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def describe(text):
    """把用户要的比例或像素尺寸，翻译成本平台真实能出的尺寸说明。"""
    ratio = parse_ratio(text)
    if not ratio:
        return None
    width, height = native_dimensions(*ratio)
    requested = parse_pixels(text)
    return {
        'ratio': f'{ratio[0]}:{ratio[1]}',
        'width': width,
        'height': height,
        'megapixels': round(width * height / 1_000_000, 2),
        'requested_width': requested[0] if requested else None,
        'requested_height': requested[1] if requested else None,
        'capped': bool(requested and requested[0] * requested[1] > NATIVE_PIXEL_RANGE[1]),
    }


def is_native(width, height):
    """判断一张图的像素量是否落在网页原生渲染区间。"""
    low, high = NATIVE_PIXEL_RANGE
    return low <= int(width) * int(height) <= high


def table(ratios):
    """给界面用的比例-尺寸对照表。"""
    rows = []
    for ratio in ratios:
        parsed = parse_ratio(ratio)
        if not parsed:
            continue
        width, height = native_dimensions(*parsed)
        rows.append({'ratio': ratio, 'width': width, 'height': height})
    return rows
