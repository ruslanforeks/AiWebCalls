"""Разговор с агентом через микрофон компьютера.

    python -m grainvoice.local

Тот же конвейер, что и в bot.py, но транспорт — локальные микрофон и колонки
вместо WebRTC. Нужен, чтобы услышать агента до подключения телефонии:
проверяются голос, задержка и распознавание сельхозлексики.

Побочная польза: не тянет aiortc и cryptography, у которых нет готовых сборок
под Intel Mac. Поэтому запускается там, где полный конвейер не собирается.

По Ctrl+C разговор завершается и транскрипт уходит в анализ — тот же путь,
которым пойдут настоящие звонки.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from loguru import logger
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.workers.runner import WorkerRunner

from grainvoice.audio import native_output_rate, resolve_device
from grainvoice.bot import analyze_call, build_conversation_prompt
from grainvoice.certs import ensure_ca_bundle
from grainvoice.config import get_settings
from grainvoice.farm import FarmContext
from grainvoice.observability import ConversationLogger
from grainvoice.services import build_llm, build_stt, build_tts, build_vad


#: Источники, которые печатают системный промпт целиком перед каждым
#: обращением к модели. Три полные копии за двадцать секунд разговора,
#: и за ними не видно ни реплик, ни ошибок.
_NOISY_LOGGERS = (
    "pipecat.services.openai.base_llm",
    "pipecat.services.llm_service",
    "pipecat.processors.frame_processor",
    "pipecat.processors.metrics",
    "pipecat.registry",
    "pipecat.workers.base_worker",
)


def _configure_logs() -> None:
    """Оставить в логе то, по чему можно понять, что происходит.

    Полное отключение отладки — плохой выбор: вместе с шумом пропадают
    состояние распознавания, срабатывания определителя речи и ошибки
    провайдеров, то есть ровно то, по чему разбирают «бот меня не слышит».

    Поэтому глушатся конкретные источники, а не уровень целиком.
    GRAINVOICE_DEBUG=1 возвращает всё.
    """
    if os.environ.get("GRAINVOICE_DEBUG"):
        return

    def keep(record) -> bool:
        return not record["name"].startswith(_NOISY_LOGGERS)

    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG",
        filter=keep,
        format="<green>{time:HH:mm:ss}</green> | {message}",
    )


def _parse_args(argv: list[str] | None = None) -> FarmContext:
    """Разобрать карточку хозяйства из аргументов командной строки.

    Без аргументов агент выясняет всё с нуля — рабочий, но худший случай:
    он спросит то, что уже записано в базе.
    """
    parser = argparse.ArgumentParser(
        prog="grainvoice.local",
        description="Разговор с агентом через микрофон",
    )
    parser.add_argument("--company", default="", help="название хозяйства")
    parser.add_argument("--name", default="", help="имя собеседника")
    parser.add_argument("--district", default="", help="район: «Белоглинский»")
    parser.add_argument("--region", default="", help="край или область")
    parser.add_argument(
        "--crops",
        default="",
        help="культуры через запятую: «пшеница,подсолнечник»",
    )
    args = parser.parse_args(argv)

    return FarmContext(
        company=args.company,
        contact_name=args.name,
        region=args.region,
        district=args.district,
        known_crops=[c.strip() for c in args.crops.split(",") if c.strip()],
    )


async def main(farm: FarmContext) -> None:
    """Провести один разговор через микрофон и разобрать его."""
    ensure_ca_bundle()
    settings = get_settings()
    system_prompt, greeting = build_conversation_prompt(settings, farm)

    logger.info(
        "LLM={}:{} | STT={}:{} | голос={} | раскрытие AI={}",
        settings.llm_provider.value,
        settings.conversation_model,
        settings.stt_provider.value,
        settings.stt_model,
        settings.tts_voice_id or "не задан",
        settings.disclose_ai,
    )

    output_device = resolve_device(settings.audio_output_device, want_input=False)
    # Частота под устройство: пересчёт на лету слышен как хрип.
    out_rate = settings.audio_out_sample_rate or native_output_rate(output_device) or 24000
    logger.info("Частота вывода: {} Гц", out_rate)

    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            # Пусто — системное «по умолчанию». Задавать явно приходится
            # с наушниками: система меняет устройство, а PortAudio держит
            # список, снятый при запуске.
            input_device_index=resolve_device(settings.audio_input_device, want_input=True),
            output_device_index=output_device,
            # Буфер побольше: при коротком звук хрипит, потому что в одном
            # процессе работают определитель речи, вебсокет распознавания
            # и поток от модели, и вывод не успевает наполняться.
            audio_out_10ms_chunks=settings.audio_out_chunks_10ms,
            audio_out_sample_rate=out_rate,
        )
    )

    stt = build_stt(settings, farm)
    llm = build_llm(settings, system_prompt)
    tts = build_tts(settings)

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=build_vad(settings)),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            # Пишет в лог «ФЕРМЕР:» и «АГЕНТ:». Промежуточные расшифровки
            # включены: когда бот молчит, они сразу показывают, доходит ли
            # звук до распознавания.
            ConversationLogger(log_interim=True),
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )

    runner = WorkerRunner()
    await runner.add_workers(worker)

    # Приветствие уходит прямо в синтез, минуя модель: просить её повторить
    # заранее известную фразу — это четыре лишние секунды в начале каждого
    # звонка и риск, что она перескажет её своими словами.
    # append_to_context=True сохраняет фразу в истории, поэтому и модель,
    # и расшифровка видят, с чего начался разговор.
    await worker.queue_frames([TTSSpeakFrame(greeting, append_to_context=True)])

    logger.info("Говорите. Ctrl+C — завершить разговор и разобрать его.")

    try:
        await runner.run()
    except asyncio.CancelledError:
        pass
    finally:
        # Разбираем разговор тем же путём, которым пойдут настоящие звонки.
        await analyze_call(context, settings)


def run() -> int:
    """Точка входа с обработкой Ctrl+C."""
    farm = _parse_args()
    _configure_logs()
    if farm.describe():
        logger.info("Звоним: {}", farm.describe())
    try:
        asyncio.run(main(farm))
    except KeyboardInterrupt:
        logger.info("Разговор прерван")
    return 0


if __name__ == "__main__":
    sys.exit(run())
