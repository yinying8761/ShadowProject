"""
Seam 1 — PromptManager.build_system_prompt() user profile rendering.

Tests that user_profile dict renders correctly into the system prompt
(key-value format with optional fields), and that the fallback to the
old flat format works when no user_profile is provided.
"""

import pytest
from core.prompt_manager import PromptManager


@pytest.fixture
def pm():
    return PromptManager()


def _render(pm: PromptManager, **overrides):
    """Minimal helper to call build_system_prompt with required fields."""
    defaults = {
        "character_name": "小樱",
        "personality": "温柔体贴",
        "role": "贴心伙伴",
        "archetype": "邻家姐姐",
        "gender": "female",
        "voice_style": "温柔亲切",
    }
    defaults.update(overrides)
    return pm.build_system_prompt(**defaults)


class TestUserProfileInjection:
    """Key-value injection when user_profile is provided."""

    def test_full_profile_renders_all_fields(self, pm):
        """All non-null profile fields appear in the prompt."""
        result = _render(pm, user_profile={
            "user_name": "小明",
            "user_gender": "男",
            "user_occupation": "大学生",
            "user_relationship": "朋友",
            "user_bio": "我喜欢 Rust 和数学。",
        })
        assert "- Name: 小明" in result
        assert "- Gender: 男" in result
        assert "- Identity: 大学生" in result
        assert "- Relationship: 朋友" in result
        assert "我喜欢 Rust 和数学。" in result
        # Old format should NOT appear
        assert "The user you're talking to is named" not in result

    def test_profile_without_bio_omits_bio_paragraph(self, pm):
        """When bio is empty, no empty paragraph is rendered."""
        result = _render(pm, user_profile={
            "user_name": "小明",
            "user_gender": None,
            "user_occupation": None,
            "user_relationship": "朋友",
            "user_bio": None,
        })
        assert "- Name: 小明" in result
        assert "Gender" not in result  # None → skipped
        assert "Identity" not in result  # None → skipped
        assert "- Relationship: 朋友" in result

    def test_profile_without_gender_and_occupation(self, pm):
        """Optional fields with falsy values are omitted."""
        result = _render(pm, user_profile={
            "user_name": "小红",
            "user_gender": "",
            "user_occupation": "",
            "user_relationship": "助手",
            "user_bio": "",
        })
        assert "- Name: 小红" in result
        assert "- Gender:" not in result
        assert "- Identity:" not in result
        assert "- Relationship: 助手" in result


class TestFallback:
    """Old flat format when user_profile is not provided."""

    def test_no_profile_falls_back_to_old_format(self, pm):
        """Without user_profile, the old user_name/relationship line is used."""
        result = _render(pm, user_name="User", relationship="friend")
        assert "The user you're talking to is named User" in result
        assert "Treat them as a close friend" in result
        assert "- Name:" not in result

    def test_custom_user_name_in_fallback(self, pm):
        """Legacy user_name parameter still works."""
        result = _render(pm, user_name="张三", relationship="朋友")
        assert "The user you're talking to is named 张三" in result
