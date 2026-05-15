from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

try:
    from dotenv import load_dotenv
except ImportError:
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

def ensure_directories():
    """Garante que os diretórios necessários existam."""
    directories = ["data", "output", "temp", "assets"]
    for directory in directories:
        Path(PROJECT_ROOT / directory).mkdir(parents=True, exist_ok=True)

def configure_audio_tools():
    """Configura ferramentas de áudio se necessário."""
    # Placeholder para compatibilidade com create_video.py
    pass

@dataclass(frozen=True)
class VideoSettings:
    width: int = env_int("VIDEO_WIDTH", 1080)
    height: int = env_int("VIDEO_HEIGHT", 1920)
    fps: int = env_int("VIDEO_FPS", 30)

@dataclass(frozen=True)
class Settings:
    video: VideoSettings = field(default_factory=VideoSettings)
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    tiktok_session_id: str = os.getenv("TIKTOK_SESSION_ID", "")
    tiktok_upload_enabled: bool = env_bool("TIKTOK_UPLOAD_ENABLED", True)
    dry_run: bool = env_bool("DRY_RUN", False)

settings = Settings()

# Configuração de Tema para Concursos
CONCURSO_THEME_PROMPT = "Você é um especialista em concursos públicos brasileiros. Gere perguntas de conhecimentos gerais que costumam cair em provas da CESPE, FGV e FCC. Foque em temas como: Direito Constitucional, Direito Administrativo, Língua Portuguesa, Raciocínio Lógico e Atualidades do Brasil."
