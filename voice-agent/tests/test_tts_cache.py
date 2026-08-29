"""Проверки кэша озвученных фраз.

Ошибка в ключе кэша слышна только в живом звонке и выглядит как чужая
фраза посреди разговора — поэтому границы проверяются тестами.
"""

from grainvoice.tts_cache import MAX_CACHEABLE_CHARS, cache_key


def _key(text: str, **overrides) -> str:
    defaults = {
        "voice": "voice-1",
        "model": "eleven_flash_v2_5",
        "sample_rate": 24000,
        "settings_fingerprint": "0.5|0.75|0.4|1.0|False",
    }
    return cache_key(text, **{**defaults, **overrides})


class TestCacheKey:
    """Ключ должен меняться от всего, что влияет на звук."""

    def test_same_text_same_key(self) -> None:
        assert _key("Понял.") == _key("Понял.")

    def test_whitespace_ignored(self) -> None:
        """Промпт может отдать фразу с переносом строки — это та же фраза."""
        assert _key("  Понял.  ") == _key("Понял.")

    def test_line_break_inside_phrase_ignored(self) -> None:
        """Приветствие в промпте записано в две строки для читаемости,
        а произносится одинаково. Без этого заготовка и живая реплика
        расходятся ключами, и кэш молча не работает."""
        two_lines = "Здравствуйте, меня зовут Алексей.\nЗерновые есть в наличии?"
        one_line = "Здравствуйте, меня зовут Алексей. Зерновые есть в наличии?"
        assert _key(two_lines) == _key(one_line)

    def test_different_text_different_key(self) -> None:
        assert _key("Понял.") != _key("Хорошо.")

    def test_voice_changes_key(self) -> None:
        """Иначе после смены голоса заиграет старая запись прежним голосом."""
        assert _key("Понял.") != _key("Понял.", voice="voice-2")

    def test_model_changes_key(self) -> None:
        assert _key("Понял.") != _key("Понял.", model="eleven_multilingual_v2")

    def test_sample_rate_changes_key(self) -> None:
        """Запись с чужой частотой проиграется писклявой или замедленной."""
        assert _key("Понял.") != _key("Понял.", sample_rate=44100)

    def test_voice_settings_change_key(self) -> None:
        """Выразительность меняет звучание той же фразы тем же голосом."""
        assert _key("Понял.") != _key("Понял.", settings_fingerprint="0.5|0.75|0.0|1.0|False")

    def test_key_is_filesystem_safe(self) -> None:
        """Ключ становится именем файла."""
        key = _key("Фраза с «кавычками», знаками / и \\ внутри")
        assert key.isalnum()
        assert len(key) == 32


class TestCacheableLength:
    """Длинные реплики кэшировать бессмысленно: дословно они не повторяются."""

    def test_limit_is_reasonable(self) -> None:
        # Приветствие — самая длинная фраза, которую надо кэшировать
        # обязательно: оно звучит на каждом звонке.
        greeting = (
            "Здравствуйте, меня зовут Алексей, компания «Зерно-Трейд». "
            "Подскажите, зерновые есть в наличии?"
        )
        assert len(greeting) <= MAX_CACHEABLE_CHARS
