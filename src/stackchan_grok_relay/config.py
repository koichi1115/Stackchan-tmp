from dataclasses import dataclass
import ipaddress
import math
import os
from urllib.parse import urlparse

from .speech import MOCK_ENGINE, STT_ENGINES, TTS_ENGINES
from .wake import DEFAULT_WAKE_WORDS


class ConfigError(ValueError):
    pass


REPLY_ENGINES = ("webhook", "grok_api")
DEFAULT_PROMPT_FILE = "prompt.txt"
DEFAULT_XAI_MODEL = "grok-4.3"
DEFAULT_XAI_API_URL = "https://api.x.ai/v1/responses"


PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
)


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    webhook_url: str
    webhook_sender_key: str
    timeout_seconds: float
    max_reply_length: int
    stt_engine: str
    stt_url: str
    tts_engine: str
    tts_url: str
    tts_speaker_id: int
    speech_timeout_seconds: float
    max_audio_bytes: int
    mock_transcript: str
    # s4: ウェイクワード・VAD・受信箱
    stt_prompt: str = ""
    wake_words: tuple[str, ...] = DEFAULT_WAKE_WORDS
    wake_ack_text: str = "はい？"
    wake_window_seconds: float = 8.0
    vad_threshold: float = 600.0
    vad_silence_ms: float = 500.0
    vad_max_seconds: float = 12.0
    inbox_url: str = ""
    inbox_poll_key: str = ""
    inbox_poll_seconds: float = 5.0
    inbox_message_ttl_seconds: float = 21_600.0
    # 返答の生成元。webhook（routine、非同期のため会話には向かない）か grok_api（xAI API 直呼び）
    reply_engine: str = "webhook"
    xai_api_key: str = ""
    xai_model: str = DEFAULT_XAI_MODEL
    xai_api_url: str = DEFAULT_XAI_API_URL
    system_prompt: str = ""
    tts_speed_scale: float = 1.2
    tts_volume_scale: float = 1.0
    xai_tools: tuple[str, ...] = ("web_search",)
    assistant_location: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        host = os.getenv("RELAY_HOST", "127.0.0.1")
        _validate_private_host(host)

        port = _read_int("RELAY_PORT", 8787, 1, 65535)
        reply_engine = os.getenv("REPLY_ENGINE", "webhook").strip() or "webhook"
        if reply_engine not in REPLY_ENGINES:
            raise ConfigError(f"REPLY_ENGINE には {', '.join(REPLY_ENGINES)} のいずれかを指定してください。")

        webhook_url = os.getenv("GROK_WEBHOOK_URL", "")
        webhook_sender_key = os.getenv("GROK_WEBHOOK_SENDER_KEY", "")
        xai_api_key = os.getenv("XAI_API_KEY", "").strip()
        xai_api_url = os.getenv("XAI_API_URL", DEFAULT_XAI_API_URL).strip() or DEFAULT_XAI_API_URL
        if reply_engine == "webhook":
            parsed_url = urlparse(webhook_url)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
                raise ConfigError("GROK_WEBHOOK_URL に有効な HTTP URL を指定してください。")
            if not webhook_sender_key:
                raise ConfigError("GROK_WEBHOOK_SENDER_KEY を指定してください。")
        else:
            if not xai_api_key:
                raise ConfigError("REPLY_ENGINE=grok_api のときは XAI_API_KEY を指定してください。")
            parsed_api = urlparse(xai_api_url)
            if parsed_api.scheme != "https" or not parsed_api.hostname:
                raise ConfigError("XAI_API_URL には https の URL を指定してください。")

        stt_engine = _read_engine("STT_ENGINE", STT_ENGINES)
        tts_engine = _read_engine("TTS_ENGINE", TTS_ENGINES)
        wake_words = _read_words("WAKE_WORDS", DEFAULT_WAKE_WORDS)

        return cls(
            host=host,
            port=port,
            webhook_url=webhook_url,
            webhook_sender_key=webhook_sender_key,
            timeout_seconds=_read_float("GROK_TIMEOUT_SECONDS", 5.0, 0.1, 60.0),
            max_reply_length=_read_int("MAX_REPLY_LENGTH", 120, 1, 500),
            stt_engine=stt_engine,
            stt_url=_read_engine_url("STT_URL", stt_engine),
            tts_engine=tts_engine,
            tts_url=_read_engine_url("TTS_URL", tts_engine),
            tts_speaker_id=_read_int("TTS_SPEAKER_ID", 1, 0, 100_000),
            speech_timeout_seconds=_read_float("SPEECH_TIMEOUT_SECONDS", 20.0, 0.1, 120.0),
            max_audio_bytes=_read_int("MAX_AUDIO_BYTES", 1_000_000, 1_024, 8_000_000),
            mock_transcript=os.getenv("MOCK_TRANSCRIPT", "こんにちは"),
            stt_prompt=_read_stt_prompt(wake_words),
            wake_words=wake_words,
            wake_ack_text=os.getenv("WAKE_ACK_TEXT", "はい？").strip() or "はい？",
            wake_window_seconds=_read_float("WAKE_WINDOW_SECONDS", 8.0, 1.0, 60.0),
            vad_threshold=_read_float("VAD_THRESHOLD", 600.0, 1.0, 32_767.0),
            vad_silence_ms=_read_float("VAD_SILENCE_MS", 500.0, 100.0, 5_000.0),
            vad_max_seconds=_read_float("VAD_MAX_SECONDS", 12.0, 1.0, 60.0),
            inbox_url=_read_inbox_url(),
            inbox_poll_key=os.getenv("INBOX_POLL_KEY", ""),
            inbox_poll_seconds=_read_float("INBOX_POLL_SECONDS", 5.0, 1.0, 3_600.0),
            inbox_message_ttl_seconds=_read_float(
                "INBOX_MESSAGE_TTL_SECONDS", 21_600.0, 60.0, 30 * 24 * 3_600.0
            ),
            reply_engine=reply_engine,
            xai_api_key=xai_api_key,
            xai_model=os.getenv("XAI_MODEL", DEFAULT_XAI_MODEL).strip() or DEFAULT_XAI_MODEL,
            xai_api_url=xai_api_url,
            system_prompt=_read_system_prompt(),
            tts_speed_scale=_read_float("TTS_SPEED_SCALE", 1.2, 0.5, 2.0),
            tts_volume_scale=_read_float("TTS_VOLUME_SCALE", 1.0, 0.1, 3.0),
            xai_tools=_read_tools(),
            assistant_location=os.getenv("ASSISTANT_LOCATION", "").strip(),
        )


def _validate_private_host(host: str) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ConfigError("RELAY_HOST にはプライベート IPv4 アドレスを指定してください。") from error
    if address.version != 4 or not (
        address.is_loopback or any(address in network for network in PRIVATE_NETWORKS)
    ):
        raise ConfigError("RELAY_HOST に公開アドレスは指定できません。")


XAI_TOOL_NAMES = ("web_search", "x_search", "code_interpreter")


def _read_tools() -> tuple[str, ...]:
    """XAI_TOOLS はカンマ区切り。既定は web_search。空文字を明示すればツール無し。"""
    raw = os.getenv("XAI_TOOLS")
    if raw is None:
        return ("web_search",)
    tools = tuple(name.strip() for name in raw.split(",") if name.strip())
    for name in tools:
        if name not in XAI_TOOL_NAMES:
            raise ConfigError(f"XAI_TOOLS には {', '.join(XAI_TOOL_NAMES)} のいずれかを指定してください。")
    return tools


def _read_system_prompt() -> str:
    """grok_api の system prompt。既定はリポジトリ直下の prompt.txt（routine と同じ文面）です。"""
    path = os.getenv("SYSTEM_PROMPT_FILE", DEFAULT_PROMPT_FILE)
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _read_words(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    words = tuple(word.strip() for word in raw.split(",") if word.strip())
    return words or default


def _read_stt_prompt(wake_words: tuple[str, ...]) -> str:
    """whisper に渡す初期プロンプト。

    既定はウェイクワードの先頭の一語です。これを渡さないと whisper は
    「スタックちゃん」を「スタークちゃん」「スタッグちゃん」と書き起こし、
    ウェイクワード判定が通りません。複数語を並べると語順が入れ替わるうえ、
    無音に対する幻聴へ紛れ込んで誤起動の元になるため、一語に絞っています。
    `STT_PROMPT=` と明示的に空にすれば渡しません。
    """
    configured = os.getenv("STT_PROMPT")
    if configured is not None:
        return configured.strip()
    return wake_words[0] if wake_words else ""


def _read_inbox_url() -> str:
    url = os.getenv("INBOX_URL", "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ConfigError("INBOX_URL には https の URL を指定してください。")
    if not os.getenv("INBOX_POLL_KEY", ""):
        raise ConfigError("INBOX_URL を使うときは INBOX_POLL_KEY を指定してください。")
    return url


def _read_engine(name: str, allowed: tuple[str, ...]) -> str:
    engine = os.getenv(name, MOCK_ENGINE)
    if engine not in allowed:
        raise ConfigError(f"{name} には {', '.join(allowed)} のいずれかを指定してください。")
    return engine


def _read_engine_url(name: str, engine: str) -> str:
    url = os.getenv(name, "")
    if engine == MOCK_ENGINE:
        return url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError(f"{name} に有効な HTTP URL を指定してください。")
    return url


def _read_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigError(f"{name} には整数を指定してください。") from error
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} は {minimum} 以上 {maximum} 以下にしてください。")
    return value


def _read_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as error:
        raise ConfigError(f"{name} には数値を指定してください。") from error
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ConfigError(f"{name} は {minimum} 以上 {maximum} 以下にしてください。")
    return value
