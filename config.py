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




@dataclass(frozen=True)
class VideoSettings:
    width: int = env_int("VIDEO_WIDTH", 1080)
    height: int = env_int("VIDEO_HEIGHT", 1920)
    fps: int = env_int("VIDEO_FPS", 30)
    duration: int = env_int("VIDEO_DURATION", 60)
    batch_size: int = env_int("VIDEOS_PER_RUN", 2)
    render_preset: str = os.getenv("FFMPEG_PRESET", "medium")
    crf: int = env_int("FFMPEG_CRF", 20)




@dataclass(frozen=True)
class VoiceSettings:
    provider: str = os.getenv("VOICE_PROVIDER", "edge").lower()
    language: str = os.getenv("VOICE_LANGUAGE", "pt-BR")
    edge_voice: str = os.getenv("EDGE_TTS_VOICE", "pt-BR-ThalitaMultilingualNeural")
    edge_rate: str = os.getenv("EDGE_TTS_RATE", "+5%")
    edge_pitch: str = os.getenv("EDGE_TTS_PITCH", "+0Hz")\n# Configuração de Tema para Concursos\nCONCURSO_THEME_PROMPT = \"Você é um especialista em concursos públicos brasileiros. Gere perguntas de conhecimentos gerais que costumam cair em provas da CESPE, FGV e FCC.
Foque em temas como: Direito Constitucional, Direito Administrativo, Língua Portuguesa (gramática), Raciocínio Lógico e Atualidades do Brasil.
As perguntas devem ter um nível de dificuldade de médio a difícil.
Mantenha o formato JSON estritamente como solicitado.\"
