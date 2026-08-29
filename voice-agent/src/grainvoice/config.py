"""Конфигурация голосового агента.

Все секреты приходят из окружения (.env), в коде их нет — см. п.20 ТЗ.
Провайдеры выбираются переменными, чтобы менять их без правок кода (п.27 ТЗ).
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = PROJECT_ROOT / "prompts"


class LLMProvider(str, Enum):
    """Кто ведёт разговор и разбирает транскрипт."""

    DEEPSEEK = "deepseek"
    ANTHROPIC = "anthropic"


class STTProvider(str, Enum):
    """Кто распознаёт речь."""

    ELEVENLABS = "elevenlabs"
    DEEPGRAM = "deepgram"


class Settings(BaseSettings):
    """Настройки, читаемые из переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Выбор провайдеров ---
    llm_provider: LLMProvider = Field(default=LLMProvider.DEEPSEEK, alias="LLM_PROVIDER")
    stt_provider: STTProvider = Field(default=STTProvider.ELEVENLABS, alias="STT_PROVIDER")

    # --- Ключи ---
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    elevenlabs_api_key: str = Field(default="", alias="ELEVENLABS_API_KEY")
    deepgram_api_key: str = Field(default="", alias="DEEPGRAM_API_KEY")

    deepseek_base_url: str = Field(
        default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL"
    )

    # --- Модели ---
    # В разговоре решает задержка: пауза дольше полутора секунд, и собеседник
    # переспрашивает «алло?». Берём быструю модель.
    conversation_model: str = Field(default="deepseek-v4-flash", alias="CONVERSATION_MODEL")

    # После звонка спешить некуда, но на замерах flash извлекает те же данные,
    # что и pro, вдвое быстрее: разговор фермера — это не та задача, где нужны
    # рассуждения. Если на живых звонках качество просядет — deepseek-v4-pro.
    analysis_model: str = Field(default="deepseek-v4-flash", alias="ANALYSIS_MODEL")

    # --- Голос ---
    stt_model: str = Field(default="scribe_v2", alias="STT_MODEL")
    tts_voice_id: str = Field(default="", alias="TTS_VOICE_ID")
    tts_model: str = Field(default="eleven_flash_v2_5", alias="TTS_MODEL")

    # Настройки голоса. Значения по умолчанию взяты из карточки Maxim
    # в библиотеке ElevenLabs — там style=0.4, и именно выразительность
    # отличает демо на сайте от ровной дикторской начитки.
    #
    # Если поменяете голос, посмотрите его настройки и перенесите сюда:
    #   curl -H "xi-api-key: $KEY" \
    #     https://api.elevenlabs.io/v1/voices/{voice_id}/settings
    tts_stability: float = Field(default=0.5, alias="TTS_STABILITY")
    tts_similarity: float = Field(default=0.75, alias="TTS_SIMILARITY")
    tts_style: float = Field(default=0.4, alias="TTS_STYLE")
    tts_speed: float = Field(default=1.0, alias="TTS_SPEED")
    tts_speaker_boost: bool = Field(default=False, alias="TTS_SPEAKER_BOOST")

    # Кэш повторяющихся фраз. Выключать стоит только при отладке самого
    # синтеза — например, когда подбираете выразительность и хотите слышать
    # каждый раз свежую озвучку.
    tts_cache_enabled: bool = Field(default=True, alias="TTS_CACHE")

    # --- Аудиоустройства ---
    # Устройства для локального разговора: номер («3») или часть названия
    # («AirPods»). Пусто — системное «по умолчанию», но с наушниками оно
    # ненадёжно: система переключает устройство, а PortAudio держит список,
    # снятый при запуске. Название устойчивее номера — нумерация меняется
    # при подключении и отключении гарнитуры.
    # Что доступно, показывает `python -m grainvoice.doctor`.
    audio_input_device: str = Field(default="", alias="AUDIO_INPUT_DEVICE")
    audio_output_device: str = Field(default="", alias="AUDIO_OUTPUT_DEVICE")

    # Частота вывода звука. Ноль — подобрать под устройство: встроенный
    # выход работает на 44100, AirPods на 48000, и если просить у синтеза
    # не то, что ждёт устройство, разницу пересчитывает система на лету.
    # На загруженной машине этот пересчёт слышно как хрип.
    audio_out_sample_rate: int = Field(default=0, alias="AUDIO_OUT_SAMPLE_RATE")

    # Размер буфера вывода в блоках по 10 мс. Значение Pipecat по умолчанию —
    # 4, то есть 40 мс. На загруженной машине буфер не успевает наполняться,
    # и звук хрипит: в одном процессе работают определитель речи, вебсокет
    # распознавания и поток от модели. Сто миллисекунд дают запас,
    # платой идёт та же сотня миллисекунд задержки.
    audio_out_chunks_10ms: int = Field(default=10, alias="AUDIO_OUT_CHUNKS_10MS")

    # Пауза тишины, после которой ElevenLabs считает фразу законченной
    # и отдаёт финальную расшифровку. По умолчанию у них полторы секунды —
    # в разговоре это заметное молчание.
    #
    # Полсекунды — нижняя граница, за которой начинает резать неспешную речь.
    # Это единственная часть задержки, которой мы управляем: до модели
    # примерно 1.3 с сетевого хода, и он от длины промпта не зависит —
    # проверено, короткий и длинный промпт дают одно и то же.
    stt_silence_secs: float = Field(default=0.5, alias="STT_SILENCE_SECS")

    # Сколько ждать продолжения, когда Smart Turn решил, что фраза не окончена.
    # По умолчанию у Pipecat три секунды — и это главный источник ощущения
    # «долго думает»: вердикт «не договорил» выносится почти на каждой
    # неуверенной реплике, а три секунды тишины читаются как обрыв связи.
    turn_wait_secs: float = Field(default=1.5, alias="TURN_WAIT_SECS")

    # Пауза, после которой определитель речи считает, что собеседник смолк.
    #
    # Трогать это значение не нужно, и вот почему. Кажется, что фермера
    # с его паузами на размышление спасёт длинная пауза — но за паузы
    # отвечает не VAD, а Smart Turn: он смотрит на смысл сказанного
    # и отвечает, договорил человек или нет.
    #
    # Smart Turn откалиброван под 0.2 с. При большем значении его расчёт
    # ломается: «STT wait timeout collapsed to 0s», и он сваливается
    # в запасной трёхсекундный таймаут. Пауза в 0.8 с давала не бережность,
    # а три секунды молчания перед каждым ответом.
    vad_stop_secs: float = Field(default=0.2, alias="VAD_STOP_SECS")

    # --- Поведение агента ---
    agent_name: str = Field(default="Алексей", alias="AGENT_NAME")
    company_name: str = Field(default="Зерно-Трейд", alias="COMPANY_NAME")

    # Представляться ли автоматическим помощником.
    #
    # Выключено — сознательный выбор заказчика, сделанный после того, как
    # на живом прогоне агент на вопрос «Ты робот, что ли?» ответил «Нет».
    # Промпт этого не требует: модель выбрала так сама, потому что ей велено
    # вести себя как человек и не сказано, что делать при разоблачении.
    #
    # Риск при этом остаётся и он не технический: прямая ложь на прямой
    # вопрос — то, за что цепляется закон «О рекламе», и первое, что спросит
    # юридический отдел компании-клиента. Включается одной переменной,
    # переписывать код не нужно.
    disclose_ai: bool = Field(default=False, alias="DISCLOSE_AI")

    # --- Версии промптов ---
    conversation_prompt: str = Field(default="grain_buyer.v1", alias="CONVERSATION_PROMPT")
    analysis_prompt: str = Field(default="call_analysis.v1", alias="ANALYSIS_PROMPT")

    @property
    def llm_api_key(self) -> str:
        """Ключ выбранного LLM-провайдера."""
        if self.llm_provider is LLMProvider.DEEPSEEK:
            return self.deepseek_api_key
        return self.anthropic_api_key

    @property
    def stt_api_key(self) -> str:
        """Ключ выбранного STT-провайдера."""
        if self.stt_provider is STTProvider.ELEVENLABS:
            return self.elevenlabs_api_key
        return self.deepgram_api_key

    def require(self, *names: str) -> None:
        """Проверить, что нужные ключи заданы, и упасть с понятным сообщением.

        Вызывается на старте, чтобы агент не падал посреди звонка.

        Raises:
            RuntimeError: Если какая-то из переменных пуста.
        """
        missing = [n for n in names if not getattr(self, n, "")]
        if missing:
            env_names = [
                self.model_fields[n].alias or n.upper()
                for n in missing
                if n in self.model_fields
            ]
            raise RuntimeError(
                "Не заданы переменные окружения: "
                + ", ".join(env_names or missing)
                + ".\nСкопируйте .env.example в .env и заполните ключи."
            )

    def require_llm(self) -> None:
        """Проверить ключ выбранного LLM-провайдера."""
        if not self.llm_api_key:
            key = (
                "DEEPSEEK_API_KEY"
                if self.llm_provider is LLMProvider.DEEPSEEK
                else "ANTHROPIC_API_KEY"
            )
            raise RuntimeError(
                f"Выбран провайдер {self.llm_provider.value}, но {key} не задан."
            )


@lru_cache
def get_settings() -> Settings:
    """Настройки читаются один раз за процесс."""
    return Settings()
