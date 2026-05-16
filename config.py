from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

try:
    from dotenv import load_dotenv
except ImportError:  # Allows local modules to load before requirements are installed.
    def load_dotenv(*_args: object, **_kwargs: object) -> bool:
        return False


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_list(name: str, default: Iterable[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


CONCURSO_THEME_PROMPT = (
    "Você é um especialista em concursos públicos brasileiros. Gere perguntas de conhecimentos gerais "
    "que costumam cair em provas da CESPE, FGV e FCC. Foque em temas como: Direito Constitucional, "
    "Direito Administrativo, Língua Portuguesa, Raciocínio Lógico e Atualidades do Brasil."
)


@dataclass(frozen=True)
class VideoSettings:
    width: int = env_int("VIDEO_WIDTH", 1080)
    height: int = env_int("VIDEO_HEIGHT", 1920)
    fps: int = env_int("VIDEO_FPS", 30)
    duration: int = env_int("VIDEO_DURATION", 90)  # Aumentado para suportar 3 perguntas individuais
    batch_size: int = env_int("VIDEOS_PER_RUN", 2)
    render_preset: str = os.getenv("FFMPEG_PRESET", "medium")
    crf: int = env_int("FFMPEG_CRF", 20)


@dataclass(frozen=True)
class VoiceSettings:
    provider: str = os.getenv("VOICE_PROVIDER", "edge").lower()
    language: str = os.getenv("VOICE_LANGUAGE", "pt-BR")
    edge_voice: str = os.getenv("EDGE_TTS_VOICE", "pt-BR-ThalitaMultilingualNeural")
    edge_rate: str = os.getenv("EDGE_TTS_RATE", "+5%")
    edge_pitch: str = os.getenv("EDGE_TTS_PITCH", "+0Hz")
    edge_volume: str = os.getenv("EDGE_TTS_VOLUME", "+0%")
    gtts_tld: str = os.getenv("GTTS_TLD", "com.br")
    elevenlabs_voice_id: str = os.getenv("ELEVENLABS_VOICE_ID", "")
    elevenlabs_model: str = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")
    elevenlabs_stability: float = env_float("ELEVENLABS_STABILITY", 0.38)
    elevenlabs_similarity: float = env_float("ELEVENLABS_SIMILARITY", 0.82)
    elevenlabs_style: float = env_float("ELEVENLABS_STYLE", 0.42)
    elevenlabs_speaker_boost: bool = env_bool("ELEVENLABS_SPEAKER_BOOST", True)
    volume_db: float = env_float("VOICE_VOLUME_DB", 1.0)


@dataclass(frozen=True)
class TikTokSettings:
    upload_enabled: bool = env_bool("TIKTOK_UPLOAD_ENABLED", False)
    dry_run: bool = env_bool("DRY_RUN", True)
    username: str = os.getenv("TIKTOK_USERNAME", "")
    storage_state_path: Path = PROJECT_ROOT / os.getenv("TIKTOK_STORAGE_STATE", "data/tiktok_state.json")
    upload_url: str = os.getenv("TIKTOK_UPLOAD_URL", "https://www.tiktok.com/upload")
    caption_prefix: str = os.getenv("TIKTOK_CAPTION_PREFIX", "")
    headless: bool = env_bool("PLAYWRIGHT_HEADLESS", True)
    max_retries: int = env_int("UPLOAD_MAX_RETRIES", 2)
    min_delay_seconds: float = env_float("UPLOAD_MIN_DELAY_SECONDS", 2.0)
    max_delay_seconds: float = env_float("UPLOAD_MAX_DELAY_SECONDS", 6.0)


@dataclass(frozen=True)
class AISettings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_enabled: bool = env_bool("OPENAI_ENABLED", bool(os.getenv("OPENAI_API_KEY")))
    opentrivia_enabled: bool = env_bool("OPENTRIVIA_ENABLED", True)


@dataclass(frozen=True)
class Paths:
    root: Path = PROJECT_ROOT
    logs: Path = PROJECT_ROOT / "logs"
    assets: Path = PROJECT_ROOT / "assets"
    music: Path = PROJECT_ROOT / "music"
    fonts: Path = PROJECT_ROOT / "fonts"
    voices: Path = PROJECT_ROOT / "voices"
    backgrounds: Path = PROJECT_ROOT / "backgrounds"
    output: Path = PROJECT_ROOT / "output"
    temp: Path = PROJECT_ROOT / "temp"
    data: Path = PROJECT_ROOT / "data"
    questions_json: Path = PROJECT_ROOT / "data" / "questions.json"
    history_json: Path = PROJECT_ROOT / "data" / "question_history.json"
    analytics_jsonl: Path = PROJECT_ROOT / "data" / "analytics.jsonl"


@dataclass(frozen=True)
class AppSettings:
    video: VideoSettings = field(default_factory=VideoSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    tiktok: TikTokSettings = field(default_factory=TikTokSettings)
    ai: AISettings = field(default_factory=AISettings)
    paths: Paths = field(default_factory=Paths)
    categories: list[str] = field(
        default_factory=lambda: [
            "Direito Constitucional",
            "Direito Administrativo",
            "Língua Portuguesa",
            "Raciocínio Lógico",
            "Atualidades",
            "conhecimentos gerais",
            "geografia",
            "história",
            "curiosidades",
            "ciência",
            "matemática",
            "90% erram",
            "só gênios acertam",
            "você consegue",
        ]
    )
    ctas: list[str] = field(
        default_factory=lambda: [
            "Comente quantas você acertou",
            "Segue para mais quizzes",
            "Desafie um amigo nos comentários",
            "Comente sua resposta antes do final",
        ]
    )
    base_hashtags: list[str] = field(
        default_factory=lambda: ["#quiz", "#curiosidades", "#viral", "#fyp", "#brasil", "#tiktokquiz", "#concursos"]
    )
    templates: list[str] = field(default_factory=lambda: ["dark_neon", "neon_grid", "minimal_glow"])


settings = AppSettings()


def ensure_directories() -> None:
    for path in vars(settings.paths).values():
        if isinstance(path, Path) and path.suffix == "":
            path.mkdir(parents=True, exist_ok=True)


def require_ffmpeg_note() -> str:
    return "FFmpeg precisa estar instalado e disponivel no PATH para renderizacao e audio."


def find_ffmpeg_binary() -> str:
    ffmpeg_binary = os.getenv("FFMPEG_BINARY", "").strip()
    if not ffmpeg_binary:
        try:
            import imageio_ffmpeg

            ffmpeg_binary = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg_binary = ""
    return ffmpeg_binary if ffmpeg_binary and Path(ffmpeg_binary).exists() else ""


def configure_audio_tools() -> None:
    """Point Pydub to a bundled FFmpeg binary when the system PATH does not have one."""
    ffmpeg_binary = find_ffmpeg_binary()
    if not ffmpeg_binary:
        return

    try:
        from pydub import AudioSegment

        AudioSegment.converter = ffmpeg_binary
        AudioSegment.ffmpeg = ffmpeg_binary
    except Exception:
        return
