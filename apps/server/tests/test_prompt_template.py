from agent_factory_server.runtime.prompt_template import (
    normalize_prompt_vars,
    render_prompt_template,
)


def test_render_replaces_known_vars() -> None:
    template = "你好 {{userId}}，欢迎 {{name}}。"
    assert (
        render_prompt_template(template, {"userId": "u_1", "name": "Alice"})
        == "你好 u_1，欢迎 Alice。"
    )


def test_render_missing_var_becomes_empty() -> None:
    assert render_prompt_template("id={{userId}}", {}) == "id="
    assert render_prompt_template("id={{userId}}", {"other": "x"}) == "id="


def test_render_allows_whitespace_inside_braces() -> None:
    assert render_prompt_template("{{ userId }}", {"userId": "abc"}) == "abc"


def test_normalize_prompt_vars_coerces_to_str() -> None:
    assert normalize_prompt_vars({"userId": 123, "flag": True, "empty": None}) == {
        "userId": "123",
        "flag": "True",
        "empty": "",
    }


def test_normalize_skips_blank_keys() -> None:
    assert normalize_prompt_vars({"  ": "x", "userId": "u"}) == {"userId": "u"}
