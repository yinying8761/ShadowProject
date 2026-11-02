"""视觉用例的固定素材（ticket 09）：确定性、可断言。

真机评测不能依赖"用户此刻的屏幕"，所以视觉用例用一张**生成**的图：白底黑字写一个
固定词。模型把它读对，才说明它真的看到了图；读不对就是没看到（而不是在编）。
"""

import pytest

from eval.fixtures import FIXTURES, VISION_WORD, fixture, vision_word_image


class TestVisionFixture:
    def test_it_is_a_png(self):
        data, mime = vision_word_image()

        assert mime == "image/png"
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(data) > 1000        # 不是一张空图

    def test_it_is_deterministic(self):
        assert vision_word_image() == vision_word_image()

    def test_the_expected_word_is_the_asserted_one(self):
        """图里画的词就是用例要断言的那个词（大小写不敏感比较）。"""
        assert VISION_WORD == "SHADOWBOT"
        assert VISION_WORD.isascii()

    def test_the_fixture_registry_resolves_by_name(self):
        assert fixture("vision_word") == vision_word_image()
        assert "vision_word" in FIXTURES

    def test_an_unknown_fixture_name_is_an_error(self):
        with pytest.raises(ValueError):
            fixture("no_such_fixture")
