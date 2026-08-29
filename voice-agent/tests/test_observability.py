"""Проверки записи разговора и распознавания эха."""

import time

from grainvoice.observability import ConversationLogger, _normalize


def _logger(said: list[str], *, bot_speaking: bool = True) -> ConversationLogger:
    """Логгер, у которого агент только что произнёс заданные реплики."""
    logger = ConversationLogger()
    for phrase in said:
        logger._recent_agent_speech.append(_normalize(phrase))
    logger._bot_speaking = bot_speaking
    if not bot_speaking:
        logger._bot_stopped_at = time.monotonic()
    return logger


class TestEchoDetection:
    """Отличаем эхо от настоящей реплики собеседника.

    Ошибка в любую сторону дорога: пропущенное эхо превращает разговор
    в диалог агента с самим собой, а ложное срабатывание записывает
    настоящий ответ фермера как эхо.
    """

    def test_exact_repeat_is_echo(self) -> None:
        logger = _logger(["Это Алексей из «Зерно-Трейд»."])
        assert logger._looks_like_echo("Это Алексей из Зерно-Трейд")

    def test_punctuation_and_case_ignored(self) -> None:
        """Распознавание возвращает текст без исходной пунктуации."""
        logger = _logger(["Подскажите, есть ли у вас сейчас зерно на продажу?"])
        assert logger._looks_like_echo("подскажите есть ли у вас сейчас зерно на продажу")

    def test_human_greeting_is_not_echo(self) -> None:
        """Главный случай: «Здравствуйте» входит в приветствие агента целиком.

        Так фермер отвечает на звонок, и записывать это как эхо нельзя.
        """
        logger = _logger(["Здравствуйте! Меня зовут Алексей, компания Зерно-Трейд."])
        assert not logger._looks_like_echo("Здравствуйте")

    def test_real_answer_is_not_echo(self) -> None:
        logger = _logger(["Подскажите, есть ли у вас зерно на продажу?"])
        assert not logger._looks_like_echo("Есть пшеничка, тонн восемьсот")

    def test_silence_window_closes(self) -> None:
        """Когда агент давно замолчал, эхо взяться неоткуда."""
        logger = _logger(["Это Алексей из Зерно-Трейд"], bot_speaking=False)
        logger._bot_stopped_at = time.monotonic() - 30
        assert not logger._looks_like_echo("Это Алексей из Зерно-Трейд")

    def test_tail_after_bot_stops_still_counts(self) -> None:
        """Динамик договорил, а микрофон дослушивает хвост."""
        logger = _logger(["Это Алексей из Зерно-Трейд"], bot_speaking=False)
        assert logger._looks_like_echo("Это Алексей из Зерно-Трейд")

    def test_short_replies_never_echo(self) -> None:
        logger = _logger(["Да, конечно, записал."])
        assert not logger._looks_like_echo("Да")

    def test_nothing_said_yet(self) -> None:
        assert not ConversationLogger()._looks_like_echo("Здравствуйте, есть зерно")
