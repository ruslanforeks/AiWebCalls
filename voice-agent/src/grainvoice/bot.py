"""Голосовой агент: конвейер «речь → диалог → речь».

Пять слоёв, каждый заменяется независимо (п.27 ТЗ):

    транспорт → STT → LLM → TTS → транспорт
                              ↓
                    (после звонка) анализ

Сейчас транспорт — WebRTC в браузере. Когда дойдём до телефонии, меняется
только он: Pipecat подставит SIP на то же место, остальное не тронется.
Конкретные провайдеры собираются в services.py.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner

from grainvoice.analysis import AnalysisError, CallAnalyzer
from grainvoice.certs import ensure_ca_bundle
from grainvoice.config import Settings, get_settings
from grainvoice.farm import FarmContext
from grainvoice.prices import format_price_table
from grainvoice.prompts import load_prompt
from grainvoice.services import (
    build_llm,
    build_stt,
    build_tts,
    build_turn_strategies,
    build_vad,
)
from grainvoice.transcript import Transcript

# Транспорты, на которых умеет работать агент. Лямбды — чтобы параметры
# создавались только для выбранного транспорта.
transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
}


def build_conversation_prompt(
    settings: Settings, farm: FarmContext | None = None
) -> tuple[str, str]:
    """Собрать системный промпт и первую фразу для звонка.

    Args:
        settings: Конфигурация.
        farm: Карточка хозяйства. Без неё агент выясняет всё с нуля —
            это рабочий, но худший случай: он спросит то, что уже записано
            в базе, и потратит время занятого человека.

    Returns:
        Пара «системный промпт, приветствие».
    """
    prompt = load_prompt(settings.conversation_prompt, category="conversation")
    farm = farm or FarmContext()
    values = {
        "agent_name": settings.agent_name,
        "company_name": settings.company_name,
        "farm_context": farm.describe(),
        "price_table": format_price_table(),
    }

    system = prompt.render("system", **values)
    greeting_field = "disclosure_greeting" if settings.disclose_ai else "greeting"
    greeting = prompt.render(greeting_field, **values)

    if settings.disclose_ai:
        system = f"{system}\n\n{prompt.render('disclosure_rule', **values)}"

    return system, greeting


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    """Запустить один разговор на переданном транспорте."""
    ensure_ca_bundle()
    settings = get_settings()
    system_prompt, greeting = build_conversation_prompt(settings)

    logger.info(
        "Старт звонка | LLM={}:{} | STT={} | промпт={} | раскрытие AI={}",
        settings.llm_provider.value,
        settings.conversation_model,
        settings.stt_provider.value,
        settings.conversation_prompt,
        settings.disclose_ai,
    )

    stt = build_stt(settings)
    llm = build_llm(settings, system_prompt)
    tts = build_tts(settings)

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        # VAD определяет, когда фермер закончил говорить, и позволяет ему
        # перебивать агента — без этого разговор звучит как автоответчик.
        user_params=LLMUserAggregatorParams(
            vad_analyzer=build_vad(settings),
            user_turn_strategies=build_turn_strategies(settings),
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
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
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport: BaseTransport, _client: Any) -> None:
        logger.info("Собеседник на линии")
        # Приветствие уходит прямо в синтез, минуя модель: просить её повторить
        # заранее известную фразу — это четыре лишние секунды в начале каждого
        # звонка и риск, что она перескажет её своими словами.
        # append_to_context=True сохраняет фразу в истории, поэтому и модель,
        # и расшифровка видят, с чего начался разговор.
        await worker.queue_frames([TTSSpeakFrame(greeting, append_to_context=True)])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport: BaseTransport, _client: Any) -> None:
        logger.info("Звонок завершён")
        await analyze_call(context, settings)
        await runner.cancel()

    await runner.run()


async def analyze_call(context: LLMContext, settings: Settings) -> None:
    """Разобрать состоявшийся разговор в данные для CRM.

    Ошибка анализа не должна ронять процесс: транскрипт уже собран, и звонок
    можно переразобрать позже.
    """
    transcript = Transcript.from_context(context.messages)

    if not transcript.farmer_spoke:
        logger.warning("Фермер не произнёс ни слова — анализировать нечего")
        return

    try:
        record = await CallAnalyzer(settings).analyze(transcript)
    except AnalysisError as exc:
        logger.error("Анализ не удался: {}", exc)
        return

    result = record.analysis
    logger.info(
        "Итог: {} | культура={} | объём={} | цена={} | интерес={} | уверенность={:.2f}",
        result.outcome.value,
        result.crop or "—",
        result.volume_tons or "—",
        result.expected_price or "—",
        result.interest_level.value,
        result.confidence,
    )
    logger.info("Сводка: {}", result.summary)

    # TODO(crm): отправить record в CRM и уведомление в Telegram.
    # Пока прототип печатает результат — CRM появится на следующем этапе.


async def bot(runner_args: RunnerArguments) -> None:
    """Точка входа, совместимая с раннером Pipecat."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
