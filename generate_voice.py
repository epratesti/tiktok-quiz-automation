from __future__ import annotations

import asyncio
import logging
import random
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests
from pydub import AudioSegment
from pydub.effects import compress_dynamic_range, normalize

from config import configure_audio_tools, find_ffmpeg_binary, settings
from generate_questions import QuizQuestion

logger = logging.getLogger(__name__)
configure_audio_tools()


TEXT_REPLACEMENTS = {
    "Voce": "Você",
    "voce": "você",
    "So ": "Só ",
    "so ": "só ",
    "genios": "gênios",
    "facil": "fácil",
    "cerebro": "cérebro",
    "Ultimos": "Últimos",
    "ultimos": "últimos",
    "numero": "número",
    "pais": "país",
    "paises": "países",
    "Italia": "Itália",
    "Grecia": "Grécia",
    "Venus": "Vênus",
    "Jupiter": "Júpiter",
    "Mercurio": "Mercúrio",
    "oxido": "óxido",
    "simbolo": "símbolo",
    "aparencia": "aparência",
    "nao": "não",
    "comentarios": "comentários",
}


NUMBERS_PT = {
    0: "zero",
    1: "um",
    2: "dois",
    3: "três",
    4: "quatro",
    5: "cinco",
    6: "seis",
    7: "sete",
    8: "oito",
    9: "nove",
    10: "dez",
    11: "onze",
    12: "doze",
    13: "treze",
    14: "quatorze",
    15: "quinze",
    16: "dezesseis",
    17: "dezessete",
    18: "dezoito",
    19: "dezenove",
    20: "vinte",
    30: "trinta",
    40: "quarenta",
    50: "cinquenta",
    60: "sessenta",
    70: "setenta",
    80: "oitenta",
    90: "noventa",
    100: "cem",
}


def number_to_pt(value: int) -> str:
    if value in NUMBERS_PT:
        return NUMBERS_PT[value]
    if 21 <= value <= 99:
        tens = value // 10 * 10
        ones = value % 10
        return f"{NUMBERS_PT[tens]} e {NUMBERS_PT[ones]}"
    if 101 <= value <= 199:
        return f"cento e {number_to_pt(value - 100)}"
    if 200 <= value <= 999:
        hundreds = {
            2: "duzentos",
            3: "trezentos",
            4: "quatrocentos",
            5: "quinhentos",
            6: "seiscentos",
            7: "setecentos",
            8: "oitocentos",
            9: "novecentos",
        }
        remainder = value % 100
        base = hundreds[value // 100]
        return base if remainder == 0 else f"{base} e {number_to_pt(remainder)}"
    return str(value)


def expand_numbers_for_speech(text: str) -> str:
    def replace_math(match: re.Match[str]) -> str:
        left = number_to_pt(int(match.group(1)))
        right = number_to_pt(int(match.group(2)))
        return f"{left} vezes {right}"

    def replace_percent(match: re.Match[str]) -> str:
        return f"{number_to_pt(int(match.group(1)))} por cento"

    def replace_plain(match: re.Match[str]) -> str:
        return number_to_pt(int(match.group(0)))

    text = re.sub(r"\b(\d{1,3})\s*x\s*(\d{1,3})\b", replace_math, text, flags=re.IGNORECASE)
    text = re.sub(r"\b(\d{1,3})%", replace_percent, text)
    return re.sub(r"\b\d{1,3}\b", replace_plain, text)


def natural_voice_text(text: str) -> str:
    """Small PT-BR cleanup layer so neural TTS reads like speech, not raw data."""
    cleaned = text
    for source, target in TEXT_REPLACEMENTS.items():
        cleaned = cleaned.replace(source, target)
    cleaned = cleaned.replace("responder rapido", "responder no impulso")
    cleaned = cleaned.replace("responder rápido", "responder no impulso")
    cleaned = cleaned.replace("Teste rapido", "Teste relâmpago")
    cleaned = cleaned.replace("teste rapido", "teste relâmpago")
    cleaned = cleaned.replace("acerta rapido", "acerta de primeira")
    cleaned = cleaned.replace("acerta rápido", "acerta de primeira")
    cleaned = cleaned.replace("rapido", "relâmpago")
    cleaned = cleaned.replace("rápido", "relâmpago")
    cleaned = cleaned.replace("Qual e", "Qual é")
    cleaned = cleaned.replace("qual e", "qual é")
    cleaned = cleaned.replace("Quanto e", "Quanto é")
    cleaned = cleaned.replace("quanto e", "quanto é")
    cleaned = cleaned.replace("Este e", "Este é")
    cleaned = cleaned.replace("este e", "este é")
    cleaned = cleaned.replace("Destes e", "Destes é")
    cleaned = cleaned.replace("destes e", "destes é")
    cleaned = cleaned.replace("A resposta correta e", "A resposta correta é")
    return expand_numbers_for_speech(cleaned)


@dataclass
class NarrationResult:
    audio_path: Path
    script: list[dict]
    duration: float


class VoiceGenerator:
    def build_script(self, question: QuizQuestion, cta: str) -> list[dict]:
        hook = natural_voice_text(question.hook)
        question_text = natural_voice_text(question.question)
        options = [natural_voice_text(option) for option in question.options]
        answer = natural_voice_text(question.correct_answer)
        cta_text = natural_voice_text(cta)
        answer_letter = "ABCD"[question.correct_index]
        return [
            {"start": 0.2, "end": 4.4, "text": f"{hook}. Presta atenção: essa vale ponto."},
            {"start": 5.2, "end": 11.0, "text": question_text},
            {"start": 11.4, "end": 16.0, "text": f"Opção A: {options[0]}."},
            {"start": 16.2, "end": 20.8, "text": f"Opção B: {options[1]}."},
            {"start": 21.0, "end": 25.6, "text": f"Opção C: {options[2]}."},
            {"start": 25.8, "end": 30.4, "text": f"Opção D: {options[3]}."},
            {"start": 45.2, "end": 53.5, "text": "Pensou bem? Últimos segundos... olha a resposta."},
            {
                "start": 55.0,
                "end": 59.3,
                "text": f"A resposta correta é a letra {answer_letter}: {answer}. {cta_text}.",
            },
        ]

    def generate(self, question: QuizQuestion, video_id: str, cta: str) -> NarrationResult:
        script = self.build_script(question, cta)
        output_path = settings.paths.voices / f"{video_id}_narration.wav"
        temp_dir = settings.paths.temp / video_id / "voice"
        temp_dir.mkdir(parents=True, exist_ok=True)

        base = AudioSegment.silent(duration=settings.video.duration * 1000)
        for index, item in enumerate(script):
            segment_path = temp_dir / f"{index:02d}.mp3"
            try:
                self._generate_segment(item["text"], segment_path)
                segment = self._load_segment(segment_path)
                segment = self._polish_voice_segment(segment + settings.voice.volume_db)
            except Exception as exc:  # noqa: BLE001 - TTS fallback keeps render alive
                logger.warning("TTS falhou, usando silencio para segmento %s: %s", index, exc)
                segment = AudioSegment.silent(duration=max(1200, int((item["end"] - item["start"]) * 1000)))
            base = base.overlay(segment, position=int(item["start"] * 1000))

        base.export(output_path, format="wav")
        return NarrationResult(audio_path=output_path, script=script, duration=settings.video.duration)

    def _polish_voice_segment(self, segment: AudioSegment) -> AudioSegment:
        segment = segment.strip_silence(silence_len=180, silence_thresh=-42, padding=80)
        segment = compress_dynamic_range(segment, threshold=-18.0, ratio=2.2, attack=6.0, release=80.0)
        segment = normalize(segment, headroom=1.5)
        return segment.fade_in(20).fade_out(45)

    def _load_segment(self, segment_path: Path) -> AudioSegment:
        try:
            return AudioSegment.from_file(segment_path)
        except Exception:
            wav_path = segment_path.with_suffix(".wav")
            self._convert_to_wav(segment_path, wav_path)
            return AudioSegment.from_wav(wav_path)

    def _convert_to_wav(self, input_path: Path, output_path: Path) -> None:
        ffmpeg_binary = find_ffmpeg_binary()
        if not ffmpeg_binary:
            raise RuntimeError("FFmpeg nao encontrado para converter audio TTS.")
        subprocess.run(
            [ffmpeg_binary, "-y", "-hide_banner", "-loglevel", "error", "-i", str(input_path), str(output_path)],
            check=True,
        )

    def _generate_segment(self, text: str, output_path: Path) -> None:
        provider_chain = self._provider_chain()
        errors: list[str] = []
        for provider in provider_chain:
            try:
                self._run_provider(provider, text, output_path)
                if output_path.exists() and output_path.stat().st_size > 0:
                    return
                errors.append(f"{provider}: arquivo de audio vazio")
            except Exception as exc:  # noqa: BLE001 - fallback providers keep pipeline resilient
                errors.append(f"{provider}: {exc}")
        raise RuntimeError("Todos os provedores TTS falharam. " + " | ".join(errors))

    def _provider_chain(self) -> list[str]:
        primary = settings.voice.provider.lower().strip()
        ordered = [primary, "elevenlabs", "edge", "gtts"]
        chain: list[str] = []
        for provider in ordered:
            if provider not in chain:
                chain.append(provider)
        return chain

    def _run_provider(self, provider: str, text: str, output_path: Path) -> None:
        if provider == "edge":
            asyncio.run(self._edge_tts(text, output_path))
            return
        if provider == "gtts":
            self._gtts(text, output_path)
            return
        if provider == "elevenlabs":
            self._elevenlabs(text, output_path)
            return
        raise ValueError(f"VOICE_PROVIDER invalido: {provider}")

    async def _edge_tts(self, text: str, output_path: Path) -> None:
        import edge_tts

        communicate = edge_tts.Communicate(
            text,
            settings.voice.edge_voice,
            rate=settings.voice.edge_rate,
            pitch=settings.voice.edge_pitch,
            volume=settings.voice.edge_volume,
        )
        await communicate.save(str(output_path))

    def _gtts(self, text: str, output_path: Path) -> None:
        from gtts import gTTS

        tts = gTTS(text=text, lang="pt", tld=settings.voice.gtts_tld, slow=False)
        tts.save(str(output_path))

    def _elevenlabs(self, text: str, output_path: Path) -> None:
        api_key = settings.ai.openai_api_key
        eleven_key = __import__("os").getenv("ELEVENLABS_API_KEY", "")
        if not eleven_key:
            raise RuntimeError("ELEVENLABS_API_KEY nao configurada.")
        voice_id = settings.voice.elevenlabs_voice_id
        if not voice_id:
            raise RuntimeError("ELEVENLABS_VOICE_ID nao configurado.")
        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": eleven_key, "Content-Type": "application/json"},
            json={
                "text": text,
                "model_id": settings.voice.elevenlabs_model,
                "voice_settings": {
                    "stability": settings.voice.elevenlabs_stability,
                    "similarity_boost": settings.voice.elevenlabs_similarity,
                    "style": min(1.0, max(0.0, settings.voice.elevenlabs_style + random.uniform(-0.05, 0.05))),
                    "use_speaker_boost": settings.voice.elevenlabs_speaker_boost,
                },
            },
            timeout=30,
        )
        if api_key:
            logger.debug("OpenAI key present; ElevenLabs request uses its own key.")
        response.raise_for_status()
        output_path.write_bytes(response.content)
