"""Тесты загрузки версионированных промптов."""

import pytest

from grainvoice.prompts import PromptNotFoundError, load_prompt


def test_loads_conversation_prompt() -> None:
    """Разговорный промпт читается и знает свою версию."""
    prompt = load_prompt("grain_buyer.v1", category="conversation")

    assert prompt.name == "grain_buyer"
    assert prompt.version == 1
    assert prompt.ref == "grain_buyer.v1"


def test_renders_variables() -> None:
    """Плейсхолдеры заменяются переданными значениями."""
    prompt = load_prompt("grain_buyer.v1", category="conversation")
    system = prompt.render("system", agent_name="Пётр", company_name="АгроТорг")

    assert "Пётр" in system
    assert "АгроТорг" in system
    assert "{agent_name}" not in system


def test_missing_variable_does_not_crash() -> None:
    """Неполная карточка фермера — норма, звонок из-за неё падать не должен."""
    prompt = load_prompt("grain_buyer.v1", category="conversation")
    system = prompt.render("system")

    assert "неизвестно" in system
    assert "{company_name}" not in system


def test_commercial_limits_present_in_prompt() -> None:
    """Запрет называть цену — требование п.8 ТЗ, он не должен потеряться."""
    prompt = load_prompt("grain_buyer.v1", category="conversation")
    system = prompt.render("system", agent_name="Алексей", company_name="Тест")

    assert "не могу" in system.lower()
    assert "руководител" in system.lower()


def test_analysis_prompt_has_user_template() -> None:
    """В промпт анализа должен подставляться транскрипт."""
    prompt = load_prompt("call_analysis.v1", category="analysis")
    rendered = prompt.render("user_template", transcript="Фермер: Есть пшеница")

    assert "Есть пшеница" in rendered


def test_unknown_prompt_raises() -> None:
    """Опечатка в имени промпта обнаруживается на старте, а не в звонке."""
    with pytest.raises(PromptNotFoundError):
        load_prompt("no_such_prompt.v1", category="conversation")


def test_malformed_ref_raises() -> None:
    """Ссылка без версии — ошибка: версию промпта мы обязаны записывать."""
    with pytest.raises(ValueError):
        load_prompt("grain_buyer", category="conversation")
