from __future__ import annotations

import asyncio
import logging
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests
from pydub import AudioSegment

from config import configure_audio_tools, find_ffmpeg_binary, settings
from generate_questions import QuizQuestion

logger = logging.getLogger(__name__)
configure_audio_tools()


@dataclass
class NarrationResult:
    audio_path: Path
    script: list[dict]
    duration: float


class VoiceGenerator:
    def build_script(self, question: QuizQuestion, cta: str) -> list[dict]:
        options = question.options
        answer_letter = "ABCD"[question.correct_index]
        return [
            {"start": 0.2, "end": 4.4, "text": f"{question.hook}. Quiz rapido, valendo!"},
            {"start": 5.2, "end": 11.0, "text": question.question},
            {"start": 11.4, "end": 16.0, "text": f"A... {options[0]}."},
            {"start": 16.2, "end": 20.8, "text": f"B... {options[1]}."},
            {"start": 21.0, "end": 25.6, "text": f"C... {options[2]}."},
            {"start": 25.8, "end": 30.4, "text": f"D... {options[3]}."},
            {"start": 45.2, "end": 53.5, "text": "Pensou bem? Ultimos segundos... a resposta vem agora."},
            {
                "start": 55.0,
                "end": 59.3,
                "text": f"A resposta correta e a letra {answer_letter}: {question.correct_answer}. {cta}.",
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
                segment += settings.voice.volume_db
            except Exception as exc:  # noqa: BLE001 - TTS fallback keeps render alive
                logger.warning("TTS falhou, usando silencio para segmento %s: %s", index, exc)
                segment = AudioSegment.silent(duration=max(1200, int((item["end"] - item["start"]) * 1000)))
            base = base.overlay(segment, position=int(item["start"] * 1000))

        base.export(output_path, format="wav")
        return NarrationResult(audio_path=output_path, script=script, duration=settings.video.duration)

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
        ordered = [primary, "gtts", "edge", "elevenlabs"]
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
                "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": random.uniform(0.1, 0.35)},
            },
            timeout=30,
        )
        if api_key:
            logger.debug("OpenAI key present; ElevenLabs request uses its own key.")
        response.raise_for_status()
        output_path.write_bytes(response.content)
