"""看屏幕走哪条路（ticket 02 / spec S3）：挂图 / 转述 / 明确报错 —— 纯决策。

`plan_vision_route(can_see, image_api)` 不碰屏幕、不碰网络、不碰 DB。`can_see` 由调用方
从 `runtime_config.vision_should_attempt()` 取（"未知也试一次"的规则只在能力表那一处）。
"""

import pytest

from services.vision_route import FALLBACK, MOUNT, UNAVAILABLE, VisionRoute, plan_vision_route


class TestPlan:
    @pytest.mark.parametrize("image_api", ["openai", "anthropic"])
    def test_a_seeing_model_mounts_the_images(self, image_api):
        route = plan_vision_route(True, image_api)

        assert route == VisionRoute(MOUNT)

    def test_a_model_that_cannot_see_falls_back_to_the_specialist(self):
        route = plan_vision_route(False, "openai")

        assert route.use_fallback is True
        assert route.mount_images is False
        assert route.unavailable_reason is None

    def test_a_blind_model_falls_back_even_with_no_known_shape(self):
        """看不了就是看不了：形状未知不影响"走转述"这个决定。"""
        assert plan_vision_route(False, None).use_fallback is True

    def test_an_unknown_shape_is_unavailable_not_a_silent_text_request(self):
        """能看但不知道怎么把图发出去 → 明确报错（调用方会把它发给用户）。"""
        route = plan_vision_route(True, None)

        assert route.mount_images is False
        assert route.use_fallback is False
        assert route.unavailable_reason == "image_api_unknown"

    @pytest.mark.parametrize("can_see", [True, False])
    @pytest.mark.parametrize("image_api", ["openai", None])
    def test_exactly_one_route_is_chosen(self, can_see, image_api):
        """`kind` 是单一字段，所以互斥是结构上的，不是靠调用方自觉。"""
        route = plan_vision_route(can_see, image_api)
        assert route.kind in (MOUNT, FALLBACK, UNAVAILABLE)
        assert sum([route.mount_images, route.use_fallback, route.unavailable_reason is not None]) == 1
