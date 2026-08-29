"""Сборка голосовых сервисов по настройкам.

Вынесено из bot.py, чтобы смена провайдера была правкой одного файла,
а не хирургией по конвейеру (п.27 ТЗ).
"""

from __future__ import annotations

from typing import Any

from grainvoice.config import LLMProvider, Settings, STTProvider
from grainvoice.farm import FarmContext




def build_stt(settings: Settings, farm: FarmContext | None = None) -> Any:
    """Создать сервис распознавания речи.

    Args:
        settings: Конфигурация.
        farm: Карточка хозяйства. Из неё берутся подсказки распознаванию —
            район, название хозяйства, культуры. Без подсказки «Белоглинский»
            слышится как «минский»: название редкое, а вариантов у модели много.

    Raises:
        RuntimeError: Если ключ провайдера не задан.
    """
    farm = farm or FarmContext()
    if settings.stt_provider is STTProvider.ELEVENLABS:
        from pipecat.services.elevenlabs.stt import CommitStrategy, ElevenLabsRealtimeSTTService
        from pipecat.transcriptions.language import Language

        settings.require("elevenlabs_api_key")
        return ElevenLabsRealtimeSTTService(
            api_key=settings.elevenlabs_api_key,
            # Границу фразы определяет сам ElevenLabs, а не Pipecat.
            #
            # При MANUAL момент «фраза закончена» решает Pipecat и шлёт команду
            # на сервер. Две стороны расходятся в оценке: Pipecat закрывает
            # реплику по своей тишине, а ElevenLabs отказывается принимать
            # огрызок короче трёх десятых секунды — «Commit request ignored»,
            # после чего рвёт соединение. В худшем случае финальная расшифровка
            # не приходит вовсе: черновики идут, а результата нет.
            #
            # При VAD решение принимает одна сторона — та, у которой есть звук.
            commit_strategy=CommitStrategy.VAD,
            settings=ElevenLabsRealtimeSTTService.Settings(
                model=settings.stt_model,
                language=Language.RU,
                keyterms=farm.keyterms(),
                # Пауза, после которой фраза считается законченной. По умолчанию
                # полторы секунды — для разговора это заметное молчание.
                vad_silence_threshold_secs=settings.stt_silence_secs,
                # Фермер часто звонит из поля или машины. Без фильтра шума
                # распознавание срывается на тракторе и ветре.
                filter_background_audio=True,
            ),
        )

    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.transcriptions.language import Language

    settings.require("deepgram_api_key")
    return DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        settings=DeepgramSTTService.Settings(
            model=settings.stt_model,
            language=Language.RU,
        ),
    )


def build_llm(settings: Settings, system_prompt: str) -> Any:
    """Создать сервис, ведущий разговор.

    Args:
        settings: Конфигурация.
        system_prompt: Собранный системный промпт со сценарием.

    Raises:
        RuntimeError: Если ключ провайдера не задан.
    """
    settings.require_llm()

    # Ответы короткие: это разговор, а не монолог. Ограничение заодно
    # страхует от «простыни», которую агент начнёт зачитывать вслух.
    max_tokens = 300

    if settings.llm_provider is LLMProvider.DEEPSEEK:
        from pipecat.services.deepseek.llm import DeepSeekLLMService

        return DeepSeekLLMService(
            api_key=settings.deepseek_api_key,
            base_url=f"{settings.deepseek_base_url.rstrip('/')}/v1",
            settings=DeepSeekLLMService.Settings(
                model=settings.conversation_model,
                system_instruction=system_prompt,
                max_tokens=max_tokens,
                # Модель по умолчанию рассуждает перед ответом. В разговоре это
                # недопустимо: замеры дали 5.5 секунды до первого слова против
                # 1.2 с отключённым рассуждением, а один прогон из трёх не выдал
                # ни слова за полторы минуты — все токены ушли в размышления.
                # В звонке это глухая тишина в трубке.
                # Для разбора после звонка рассуждение оставлено: там спешить
                # некуда, а точность важнее.
                extra={"reasoning_effort": "none"},
            ),
        )

    from pipecat.services.anthropic.llm import AnthropicLLMService

    return AnthropicLLMService(
        api_key=settings.anthropic_api_key,
        settings=AnthropicLLMService.Settings(
            model=settings.conversation_model,
            system_instruction=system_prompt,
            max_tokens=max_tokens,
        ),
    )


def build_tts(settings: Settings) -> Any:
    """Создать сервис синтеза речи.

    Raises:
        RuntimeError: Если ключ или голос не заданы.
    """
    from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
    from pipecat.transcriptions.language import Language

    settings.require("elevenlabs_api_key", "tts_voice_id")
    return ElevenLabsTTSService(
        api_key=settings.elevenlabs_api_key,
        settings=ElevenLabsTTSService.Settings(
            voice=settings.tts_voice_id,
            model=settings.tts_model,
            language=Language.RU,
            # Настройки голоса берутся из его карточки в библиотеке ElevenLabs.
            #
            # Без них уходят значения по умолчанию, а там style=0.0 — нулевая
            # выразительность. Голос звучит ровной дикторской начиткой, и это
            # ровно то, чем демо на сайте отличается от нашего синтеза:
            # у Maxim в карточке style=0.4.
            #
            # Значения тянутся из окружения, потому что подбираются на слух,
            # а не рассуждением. Что стоит у голоса сейчас, показывает
            # GET /v1/voices/{voice_id}/settings.
            stability=settings.tts_stability,
            similarity_boost=settings.tts_similarity,
            style=settings.tts_style,
            speed=settings.tts_speed,
            use_speaker_boost=settings.tts_speaker_boost,
        ),
    )


def build_vad(settings: Settings) -> Any:
    """Создать определитель речи.

    За паузы в речи отвечает не он, а Smart Turn — модель, которая смотрит
    на смысл сказанного и решает, договорил человек или задумался.
    Именно она позволяет фермеру сказать «Ну... есть пшеничка... тонн
    восемьсот», не будучи перебитым.

    Поэтому stop_secs здесь оставлен маленьким. Увеличивать его вредно:
    Smart Turn откалиброван под 0.2 с, и при большем значении его расчёт
    ломается — он сваливается в запасной трёхсекундный таймаут, и агент
    молчит три секунды перед каждым ответом.
    """
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams

    return SileroVADAnalyzer(
        params=VADParams(
            # Пауза, после которой считаем, что собеседник договорил.
            stop_secs=settings.vad_stop_secs,
            # Короткий порог на старте: перебивать агента нужно сразу,
            # иначе он продолжает говорить поверх собеседника.
            start_secs=0.2,
        )
    )
