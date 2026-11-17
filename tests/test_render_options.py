"""渲染清晰度的解析：数字像素比优先于档位，且夹在硬边界内。"""

from __future__ import annotations

from t2i_core.render import (
    MAX_CANVAS_PIXELS,
    MAX_DEVICE_SCALE_FACTOR,
    MIN_DEVICE_SCALE_FACTOR,
    ScreenshotOptions,
    fit_scale_to_height,
    resolve_scale,
)


def test_missing_scale_keeps_css_pixels():
    """不传任何清晰度参数时保持旧行为：与 CSS 像素一比一。"""
    assert resolve_scale(ScreenshotOptions()) == ("normal", 1.0)


def test_level_maps_to_factor():
    assert resolve_scale(ScreenshotOptions(device_scale_factor_level="ultra")) == (
        "ultra",
        1.8,
    )


def test_numeric_factor_wins_over_level():
    key, factor = resolve_scale(
        ScreenshotOptions(device_scale_factor=2.0, device_scale_factor_level="high")
    )
    assert key == "2.00"
    assert factor == 2.0


def test_numeric_factor_is_bounded():
    assert resolve_scale(ScreenshotOptions(device_scale_factor=0.1)) == (
        f"{MIN_DEVICE_SCALE_FACTOR:.2f}",
        MIN_DEVICE_SCALE_FACTOR,
    )
    assert resolve_scale(ScreenshotOptions(device_scale_factor=99)) == (
        f"{MAX_DEVICE_SCALE_FACTOR:.2f}",
        MAX_DEVICE_SCALE_FACTOR,
    )


def test_pool_key_is_quantized_with_factor():
    """池键与上下文实际像素比必须一致，否则相近数值会共用一个上下文。"""
    key, factor = resolve_scale(ScreenshotOptions(device_scale_factor=2.004))
    assert key == f"{factor:.2f}"
    assert factor == 2.0


def test_long_page_scale_is_capped_to_bitmap_limit():
    """超长页面要把像素比压下来：Chromium 单张位图超过 32767px 会静默产出空文件。"""
    # 短页面不动
    assert fit_scale_to_height(720, 2.0) == 2.0
    # 20000px 高、2 倍会到 40000px，压到 32000/20000 = 1.6
    assert fit_scale_to_height(20000, 2.0) == 1.6
    # 已经超过上限的内容只能用 1 倍，不能再低
    assert fit_scale_to_height(40000, 2.0) == 1.0
    # 压完的结果必须真的在上限内
    for height in (720, 4000, 9000, 16000, 20000, 32000):
        fitted = fit_scale_to_height(height, 2.0)
        assert height * fitted <= MAX_CANVAS_PIXELS
    # 量不到高度时不做降级
    assert fit_scale_to_height(0, 2.0) == 2.0
