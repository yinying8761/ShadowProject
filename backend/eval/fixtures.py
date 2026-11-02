"""评测用的固定素材（ticket 09）：视觉用例需要一张**可断言**的图片。

真机评测不能依赖"用户此刻的屏幕"——那是没法断言的。这里生成一张确定性的图片：白底
黑字写一个固定词，模型把它读出来，就说明它**真的看到了图**，而不是在编。
"""

from __future__ import annotations

import io
from functools import lru_cache

#: 图片里的那个词（用例断言它出现在回答里，大小写不敏感）
VISION_WORD = "SHADOWBOT"

#: 画布尺寸（够大，默认字体的位图放大后仍然可读）
_WIDTH = 900
_HEIGHT = 260


@lru_cache(maxsize=1)
def vision_word_image() -> tuple[bytes, str]:
    """白底黑字写着 `VISION_WORD` 的 PNG（确定性，多次调用同一份字节）。"""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (_WIDTH, _HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    # Pillow ≥ 9.1 支持 load_default(size=…)：内置位图字体会被放大，够模型读
    font = ImageFont.load_default(size=96)
    draw.text((40, 70), VISION_WORD, fill="black", font=font)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), "image/png"


#: 用例里的 `image_fixture` 名字 → 取素材的函数
FIXTURES = {"vision_word": vision_word_image}


def fixture(name: str) -> tuple[bytes, str]:
    """按名字取素材；名字不认识就抛 ValueError（用例写错了要当场炸）。"""
    loader = FIXTURES.get(name)
    if loader is None:
        raise ValueError(f"unknown eval fixture {name!r}; known: {sorted(FIXTURES)}")
    return loader()
