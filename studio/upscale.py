"""出图后的显式放大，永远标注来源，绝不冒充原生分辨率。

网页生图的像素总量固定在约 1.57 MP（见 `studio.resolution`），想要更大的交付
尺寸只能在出图之后放大。这里的规则是硬性的：

* 原生图始终是唯一的源文件，放大结果是派生物，不覆盖、不替换原图。
* 每个放大结果都带 `backend` 和 `label`，界面、文件名和 API 响应都必须显示。
* Lanczos 重采样就叫算法放大，不叫 AI 放大。只有真的跑了超分模型，才允许
  出现 “AI 超分” 字样。

可选的真超分后端：把 Real-ESRGAN 的 ncnn 可执行文件放到仓库 `tools/` 下，或用
环境变量 `IMAGE_STUDIO_UPSCALER` 指向它，本模块会自动改用它并换成 AI 标注。
缺少该文件时不报错，回退到算法放大并在标注里写明。
"""
from io import BytesIO
from pathlib import Path
import os
import subprocess
import tempfile

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]

MAX_FACTOR = 4
MAX_OUTPUT_PIXELS = 40_000_000

ALGORITHM_BACKEND = 'lanczos'
AI_BACKEND = 'realesrgan'

LABELS = {
    ALGORITHM_BACKEND: '算法放大（Lanczos 重采样 + 轻度锐化），非原生像素',
    AI_BACKEND: 'AI 超分放大（Real-ESRGAN），非原生像素',
}


def upscaler_path():
    """找出可用的真超分可执行文件，找不到返回 None。"""
    configured = os.environ.get('IMAGE_STUDIO_UPSCALER', '').strip()
    candidates = [Path(configured)] if configured else []
    candidates += [
        ROOT / 'tools' / 'realesrgan-ncnn-vulkan.exe',
        ROOT / 'tools' / 'realesrgan-ncnn-vulkan',
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def available_backend():
    return AI_BACKEND if upscaler_path() else ALGORITHM_BACKEND


def capability():
    """给界面用的放大能力说明。"""
    backend = available_backend()
    return {
        'backend': backend,
        'label': LABELS[backend],
        'max_factor': MAX_FACTOR,
        'ai_available': backend == AI_BACKEND,
        'note': '放大结果不是原生像素，交付前请自行确认清晰度可接受。',
    }


def _normalise_factor(factor):
    try:
        value = int(factor)
    except (TypeError, ValueError) as error:
        raise ValueError('放大倍数必须是整数') from error
    if not 1 <= value <= MAX_FACTOR:
        raise ValueError(f'放大倍数支持 1 到 {MAX_FACTOR} 倍')
    return value


def _lanczos(image, width, height):
    enlarged = image.resize((width, height), Image.Resampling.LANCZOS)
    return enlarged.filter(ImageFilter.UnsharpMask(radius=1.2, percent=55, threshold=3))


def _realesrgan(executable, image, width, height):
    """跑真超分模型，失败时抛异常交给调用方回退。"""
    with tempfile.TemporaryDirectory(prefix='studio-upscale-') as workspace:
        source = Path(workspace) / 'source.png'
        target = Path(workspace) / 'target.png'
        image.save(source, format='PNG')
        subprocess.run(
            [str(executable), '-i', str(source), '-o', str(target), '-s', '4'],
            check=True,
            capture_output=True,
            timeout=600,
        )
        with Image.open(target) as enlarged:
            enlarged.load()
            if enlarged.size == (width, height):
                return enlarged.copy()
            return enlarged.resize((width, height), Image.Resampling.LANCZOS)


def upscale_png(data, factor):
    """把 PNG 字节放大 `factor` 倍，返回 (PNG 字节, 标注字典)。

    `factor` 为 1 时原样返回，并标注 `upscaled=False`。
    """
    value = _normalise_factor(factor)
    with Image.open(BytesIO(data)) as opened:
        opened.load()
        image = opened.convert('RGBA' if opened.mode in {'RGBA', 'LA', 'P'} else 'RGB')
    native_width, native_height = image.size
    if value == 1:
        return data, {
            'upscaled': False,
            'factor': 1,
            'backend': 'native',
            'label': '网页原生输出',
            'native_width': native_width,
            'native_height': native_height,
            'width': native_width,
            'height': native_height,
        }
    width, height = native_width * value, native_height * value
    if width * height > MAX_OUTPUT_PIXELS:
        raise ValueError('放大后超过 4000 万像素，请降低倍数')
    executable = upscaler_path()
    backend = ALGORITHM_BACKEND
    fallback = ''
    enlarged = None
    if executable:
        try:
            enlarged = _realesrgan(executable, image, width, height)
            backend = AI_BACKEND
        except (subprocess.SubprocessError, OSError, ValueError) as error:
            fallback = f'真超分后端不可用，已回退算法放大：{type(error).__name__}'
    if enlarged is None:
        enlarged = _lanczos(image, width, height)
    output = BytesIO()
    enlarged.save(output, format='PNG')
    meta = {
        'upscaled': True,
        'factor': value,
        'backend': backend,
        'label': LABELS[backend],
        'native_width': native_width,
        'native_height': native_height,
        'width': width,
        'height': height,
    }
    if fallback:
        meta['fallback'] = fallback
    return output.getvalue(), meta
