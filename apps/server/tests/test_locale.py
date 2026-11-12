from agent_factory_server.runtime.locale import (
    normalize_locale,
    pick_system_prompt_template,
)
from agent_factory_server.runtime.memory import format_memory_block


def test_normalize_locale_aliases() -> None:
    assert normalize_locale("zh-CN") == "zh"
    assert normalize_locale("EN") == "en"
    assert normalize_locale("jp") == "ja"
    assert normalize_locale("unknown") == "zh"


def test_pick_system_prompt_by_locale() -> None:
    prompts = {
        "zh": "中文 {{userId}}",
        "en": "English {{userId}}",
        "ja": "日本語 {{userId}}",
    }
    assert pick_system_prompt_template(
        system_prompt="legacy", system_prompts=prompts, locale="en"
    ) == "English {{userId}}"
    assert pick_system_prompt_template(
        system_prompt="legacy", system_prompts=prompts, locale="ja"
    ) == "日本語 {{userId}}"


def test_pick_falls_back_when_missing() -> None:
    prompts = {"en": "English only"}
    assert (
        pick_system_prompt_template(
            system_prompt="legacy zh", system_prompts=prompts, locale="ja"
        )
        == "English only"
    )
    assert (
        pick_system_prompt_template(
            system_prompt="legacy zh", system_prompts={}, locale="ja"
        )
        == "legacy zh"
    )


class _Row:
    def __init__(self, key: str, content: str) -> None:
        self.key = key
        self.content = content


def test_memory_block_localized() -> None:
    zh = format_memory_block(
        [_Row("lang", "中文")], [], max_chars=4000, locale="zh"
    )
    en = format_memory_block(
        [_Row("lang", "English")], [], max_chars=4000, locale="en"
    )
    ja = format_memory_block(
        [_Row("lang", "日本語")], [], max_chars=4000, locale="ja"
    )
    assert "# 记忆" in zh and "画像" in zh
    assert "# Memory" in en and "Profile" in en
    assert "# 記憶" in ja and "プロファイル" in ja
