"""Запись разговора в лог человекочитаемыми строками.

Штатный лог Pipecat подробен, но по нему трудно понять главное: что агент
услышал и что ответил. Когда собеседник говорит, а бот молчит, нужно сразу
видеть, дошла ли до распознавания хоть одна фраза.
"""

from __future__ import annotations

import time
from collections import deque

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

#: Сколько последних реплик агента держать для сверки с расшифровкой.
_ECHO_MEMORY = 6

#: Сколько секунд после окончания речи агента звук ещё может быть эхом.
#: Запас на буферизацию: динамик договорил, а микрофон дослушивает хвост.
_ECHO_TAIL_SECS = 1.0

#: Минимальная доля совпадения, при которой считаем расшифровку эхом.
#: «Здравствуйте» содержится в приветствии агента целиком, но составляет
#: от него малую часть — и это настоящий ответ человека, а не эхо.
_ECHO_MIN_RATIO = 0.6


def _overlap_ratio(heard: str, said: str) -> float:
    """Какую долю реплики агента занимает услышанное.

    Голое вхождение подстроки не годится: короткое «здравствуйте» входит
    в длинное приветствие, но эхом не является — так отвечает человек.
    """
    if not heard or not said:
        return 0.0

    if heard in said or said in heard:
        return min(len(heard), len(said)) / max(len(heard), len(said))

    return 0.0


def _normalize(text: str) -> str:
    """Привести фразу к виду, в котором её можно сравнивать.

    Распознавание возвращает текст без исходной пунктуации и с другим
    регистром, поэтому сравнивать сырые строки бесполезно.
    """
    return "".join(ch.lower() for ch in text if ch.isalnum() or ch.isspace()).strip()


class ConversationLogger(FrameProcessor):
    """Пишет в лог реплики обеих сторон, пропуская кадры дальше без изменений.

    Ставится в конвейер после распознавания: там проходят и расшифровки
    собеседника, и текст, уходящий в синтез.

    Заодно ловит акустическое эхо. При работе через динамики микрофон слышит
    собственный голос агента, распознавание честно его расшифровывает, и агент
    начинает отвечать сам себе. В логе это выглядит как осмысленный, но
    бессвязный разговор, и причину видно не сразу — поэтому она называется
    вслух.
    """

    def __init__(self, *, log_interim: bool = False, **kwargs) -> None:
        """
        Args:
            log_interim: Писать ли промежуточные расшифровки. Полезно, когда
                непонятно, доходит ли звук вообще: промежуточные появляются
                раньше финальных и сразу показывают, что микрофон слышен.
        """
        super().__init__(**kwargs)
        self._log_interim = log_interim
        self._recent_agent_speech: deque[str] = deque(maxlen=_ECHO_MEMORY)
        self._echo_warned = False
        self._bot_speaking = False
        self._bot_stopped_at: float | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Залогировать реплику и передать кадр дальше."""
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            text = (frame.text or "").strip()
            if text:
                if self._looks_like_echo(text):
                    self._warn_about_echo(text)
                else:
                    logger.info("ФЕРМЕР: {}", text)
        elif isinstance(frame, InterimTranscriptionFrame) and self._log_interim:
            text = (frame.text or "").strip()
            if text:
                logger.debug("фермер (черновик): {}", text)
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self._bot_stopped_at = time.monotonic()
        elif isinstance(frame, (TTSTextFrame, TTSSpeakFrame)):
            # TTSSpeakFrame — приветствие, уходящее в синтез мимо модели;
            # TTSTextFrame — обычный ответ модели. В логе они равнозначны.
            text = (frame.text or "").strip()
            if text:
                logger.info("АГЕНТ: {}", text)
                self._recent_agent_speech.append(_normalize(text))

        await self.push_frame(frame, direction)

    def _looks_like_echo(self, text: str) -> bool:
        """Похоже ли, что микрофон поймал голос самого агента.

        Двух условий мало по отдельности, поэтому требуются оба.

        Совпадение текста без учёта времени ловит настоящие ответы: фермер
        говорит «Здравствуйте», а эта строка целиком содержится в приветствии
        агента. Время без учёта текста ловит перебивания: человек вправе
        заговорить, пока агент ещё не договорил.
        """
        if not self._within_echo_window():
            return False

        heard = _normalize(text)
        if not heard:
            return False

        return any(_overlap_ratio(heard, said) >= _ECHO_MIN_RATIO
                   for said in self._recent_agent_speech)

    def _within_echo_window(self) -> bool:
        """Мог ли звук быть эхом по времени: агент говорит или только что смолк."""
        if self._bot_speaking:
            return True

        if self._bot_stopped_at is None:
            return False

        return (time.monotonic() - self._bot_stopped_at) <= _ECHO_TAIL_SECS

    def _warn_about_echo(self, text: str) -> None:
        """Сообщить, что микрофон слышит динамик."""
        logger.warning("ЭХО (агент услышал сам себя): {}", text)

        if not self._echo_warned:
            self._echo_warned = True
            logger.warning(
                "Микрофон ловит звук из динамиков, и агент отвечает сам себе. "
                "Наденьте наушники — эхоподавления в локальном режиме нет. "
                "На телефонии этой проблемы не будет: там эхо гасит сеть."
            )
