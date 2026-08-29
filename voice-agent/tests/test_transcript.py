"""Тесты сборки транскрипта из истории разговора."""

from grainvoice.transcript import Transcript


def test_skips_service_messages() -> None:
    """Служебные инструкции модели не должны попадать в расшифровку."""
    transcript = Transcript.from_context(
        [
            {"role": "system", "content": "Ты закупщик зерна"},
            {"role": "developer", "content": "Скажи дословно: Здравствуйте!"},
            {"role": "assistant", "content": "Здравствуйте! Есть зерно на продажу?"},
            {"role": "user", "content": "Есть, пшеница"},
        ]
    )

    assert len(transcript.turns) == 2
    assert transcript.turns[0].speaker == "agent"
    assert transcript.turns[1].speaker == "farmer"


def test_extracts_text_from_content_blocks() -> None:
    """Content приходит списком блоков, а не строкой — текст надо достать."""
    transcript = Transcript.from_context(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Сколько тонн?"},
                    {"type": "tool_use", "id": "x", "name": "f", "input": {}},
                ],
            }
        ]
    )

    assert transcript.to_text() == "Закупщик: Сколько тонн?"


def test_empty_replies_are_dropped() -> None:
    """Пустые реплики не засоряют транскрипт."""
    transcript = Transcript.from_context(
        [
            {"role": "user", "content": "   "},
            {"role": "user", "content": "Алло"},
        ]
    )

    assert len(transcript.turns) == 1


def test_farmer_spoke_detects_silent_call() -> None:
    """Если говорил только агент — разговора не было."""
    silent = Transcript.from_context([{"role": "assistant", "content": "Здравствуйте!"}])
    assert silent.farmer_spoke is False

    real = Transcript.from_context(
        [
            {"role": "assistant", "content": "Здравствуйте!"},
            {"role": "user", "content": "Да, слушаю"},
        ]
    )
    assert real.farmer_spoke is True


def test_to_text_labels_speakers_in_russian() -> None:
    """Расшифровка уходит в модель — роли должны читаться по-русски."""
    transcript = Transcript()
    transcript.add("agent", "Здравствуйте!")
    transcript.add("farmer", "Здравствуйте")

    assert transcript.to_text() == "Закупщик: Здравствуйте!\nФермер: Здравствуйте"
