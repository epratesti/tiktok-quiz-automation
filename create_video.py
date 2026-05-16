from __future__ import annotations

import logging
import random
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from pydub import AudioSegment

try:
    from moviepy.editor import AudioFileClip, CompositeVideoClip, ImageClip, VideoClip, VideoFileClip
except ImportError:  # MoviePy 2.x
    from moviepy import AudioFileClip, CompositeVideoClip, ImageClip, VideoClip, VideoFileClip

from config import configure_audio_tools, settings
from effects import (
    background_frame,
    option_panel,
    progress_frame,
    text_panel,
    thumbnail_image,
    timer_frame,
)
from generate_questions import QuizQuestion
from generate_voice import NarrationResult
from subtitles import build_segments_from_script, write_srt

logger = logging.getLogger(__name__)
configure_audio_tools()


def clip_duration(clip: Any, duration: float) -> Any:
    return clip.with_duration(duration) if hasattr(clip, "with_duration") else clip.set_duration(duration)


def clip_start(clip: Any, start: float) -> Any:
    return clip.with_start(start) if hasattr(clip, "with_start") else clip.set_start(start)


def clip_position(clip: Any, position: Any) -> Any:
    return clip.with_position(position) if hasattr(clip, "with_position") else clip.set_position(position)


def clip_audio(clip: Any, audio: Any) -> Any:
    return clip.with_audio(audio) if hasattr(clip, "with_audio") else clip.set_audio(audio)


def clip_resize(clip: Any, **kwargs: Any) -> Any:
    return clip.resized(**kwargs) if hasattr(clip, "resized") else clip.resize(**kwargs)


class VideoCreator:
    def create(self, question: QuizQuestion, narration: NarrationResult, video_id: str | None = None, cta: str | None = None) -> dict[str, Path]:
        return self.create_multi_question([question], narration, video_id, cta)

    def create_multi_question(self, questions: list[QuizQuestion], narration: NarrationResult, video_id: str | None = None, cta: str | None = None) -> dict[str, Path]:
        video_id = video_id or f"quiz_{uuid.uuid4().hex[:10]}"
        theme_name = random.choice(settings.templates)
        output_mp4 = settings.paths.output / f"{video_id}.mp4"
        output_srt = settings.paths.output / f"{video_id}.srt"
        output_thumb = settings.paths.output / f"{video_id}_thumb.jpg"
        final_audio = settings.paths.temp / f"{video_id}_final_audio.mp3"

        settings.paths.output.mkdir(parents=True, exist_ok=True)
        settings.paths.temp.mkdir(parents=True, exist_ok=True)

        logger.info("Renderizando video multi-pergunta %s com tema %s", video_id, theme_name)
        
        # O script da narração contém os tempos exatos de cada segmento
        video = self._build_multi_video_clip(questions, narration, theme_name, cta)
        
        audio_path = self._build_audio(narration.audio_path, final_audio, narration.duration)
        audio = AudioFileClip(str(audio_path))
        video = clip_audio(video, audio)

        video.write_videofile(
            str(output_mp4),
            fps=settings.video.fps,
            codec="libx264",
            audio_codec="aac",
            preset=settings.video.render_preset,
            ffmpeg_params=["-crf", str(settings.video.crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
            threads=4,
        )
        audio.close()
        video.close()

        segments = build_segments_from_script(narration.script)
        write_srt(segments, output_srt)
        
        # Thumbnail baseada na primeira pergunta
        thumbnail_image(questions[0].question, questions[0].correct_answer, theme_name).save(output_thumb, quality=92)
        
        return {"video": output_mp4, "subtitles": output_srt, "thumbnail": output_thumb}

    def _build_multi_video_clip(self, questions: list[QuizQuestion], narration: NarrationResult, theme_name: str, cta: str | None) -> Any:
        width, height = settings.video.width, settings.video.height
        duration = narration.duration
        clips = [self._background_clip(theme_name, duration)]

        # Mapear segmentos por tipo e índice de pergunta
        segments = narration.script
        
        for q_idx, question in enumerate(questions):
            # Encontrar tempos para esta pergunta
            q_segments = [s for s in segments if s.get("question_idx") == q_idx]
            if not q_segments:
                continue
                
            q_start = q_segments[0]["start"]
            q_end = q_segments[-1]["end"]
            
            # Hook e Título da Categoria
            hook_seg = next((s for s in q_segments if s["type"] == "hook"), q_segments[0])
            title_img = text_panel(f"{question.hook}\nPERGUNTA {q_idx + 1}", width - 100, 72, theme_name)
            clips.append(self._image_clip(title_img, hook_seg["start"], hook_seg["end"] - hook_seg["start"], ("center", 240)))

            # Pergunta
            question_seg = next((s for s in q_segments if s["type"] == "question"), None)
            if question_seg:
                # A pergunta fica visível desde o início até a revelação
                reveal_seg = next((s for s in q_segments if s["type"] == "answer"), q_segments[-1])
                question_img = text_panel(question.question, width - 100, 68, theme_name, padding=38)
                clips.append(self._image_clip(question_img, question_seg["start"], reveal_seg["start"] - question_seg["start"], ("center", 190)))

            # Opções
            option_segs = [s for s in q_segments if s["type"] == "option"]
            option_width = width - 330
            option_x = 70
            for opt_idx, opt_seg in enumerate(option_segs):
                if opt_idx < len(question.options):
                    panel = option_panel("ABCD"[opt_idx], question.options[opt_idx], option_width, theme_name)
                    # Opção aparece quando é falada e fica até o suspense
                    suspense_seg = next((s for s in q_segments if s["type"] == "suspense"), q_segments[-1])
                    clips.append(self._image_clip(panel, opt_seg["start"], suspense_seg["end"] - opt_seg["start"], (option_x, 610 + opt_idx * 180)))

            # Timer (durante o suspense)
            suspense_seg = next((s for s in q_segments if s["type"] == "suspense"), None)
            if suspense_seg:
                suspense_dur = suspense_seg["end"] - suspense_seg["start"]
                timer = VideoClip(lambda t: timer_frame(t, suspense_dur, theme_name), duration=suspense_dur)
                clips.append(clip_position(clip_start(timer, suspense_seg["start"]), (width - 176, 620)))
                
                suspense_text = text_panel("RESPONDA AGORA...", width - 140, 82, theme_name, padding=40)
                clips.append(self._image_clip(suspense_text, suspense_seg["start"], suspense_dur, ("center", 740)))

            # Revelação da Resposta
            reveal_seg = next((s for s in q_segments if s["type"] == "answer"), None)
            if reveal_seg:
                reveal_img = text_panel(
                    f"RESPOSTA: {'ABCD'[question.correct_index]}\n{question.correct_answer}",
                    width - 100,
                    78,
                    theme_name,
                    padding=42,
                )
                clips.append(self._image_clip(reveal_img, reveal_seg["start"], reveal_seg["end"] - reveal_seg["start"], ("center", 520)))

        # CTA Final
        cta_seg = next((s for s in segments if s["type"] == "cta"), None)
        if cta_seg:
            cta_text = cta or "Comente quantas você acertou"
            cta_panel = text_panel(cta_text, width - 140, 54, theme_name, padding=30)
            clips.append(self._image_clip(cta_panel, cta_seg["start"], cta_seg["end"] - cta_seg["start"], ("center", 1320)))

        # Barra de Progresso Geral
        progress = VideoClip(lambda t: progress_frame(t, duration, width, theme_name), duration=duration)
        clips.append(clip_position(progress, ("center", height - 64)))

        # Legendas Dinâmicas
        for segment in build_segments_from_script(narration.script):
            # Ajustar posição Y dependendo do tipo de segmento
            y = 1460
            img = text_panel(segment.text, width - 160, 44, theme_name, padding=24, highlight=segment.emphasis)
            clips.append(self._image_clip(img, segment.start, max(0.8, segment.end - segment.start), ("center", y)))

        return CompositeVideoClip(clips, size=(width, height))

    def _background_clip(self, theme_name: str, duration: float) -> Any:
        width, height = settings.video.width, settings.video.height
        asset = self._pick_background_asset()
        if asset:
            if asset.suffix.lower() in {".mp4", ".mov", ".webm"}:
                clip = VideoFileClip(str(asset))
                clip = clip_resize(clip, height=height)
                clip = clip_duration(clip, duration)
                return clip_position(clip, ("center", "center"))
            image = Image.open(asset).convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
            return clip_duration(ImageClip(np.array(image)), duration)
        return VideoClip(lambda t: background_frame(t, width, height, theme_name), duration=duration)

    def _image_clip(self, image: Image.Image, start: float, duration: float, position: Any) -> Any:
        clip = ImageClip(np.array(image))
        clip = clip_duration(clip, duration)
        clip = clip_start(clip, start)
        return clip_position(clip, position)

    def _pick_background_asset(self) -> Path | None:
        candidates = []
        for suffix in ("*.mp4", "*.mov", "*.webm", "*.jpg", "*.jpeg", "*.png"):
            candidates.extend(settings.paths.backgrounds.glob(suffix))
        return random.choice(candidates) if candidates else None

    def _build_audio(self, narration_path: Path, output_path: Path, duration: float) -> Path:
        narration = AudioSegment.from_file(narration_path)
        narration = narration[: int(duration * 1000)]
        if len(narration) < int(duration * 1000):
            narration += AudioSegment.silent(duration=int(duration * 1000) - len(narration))

        music_path = self._pick_music_asset()
        if music_path:
            music = AudioSegment.from_file(music_path)
            while len(music) < int(duration * 1000):
                music += music
            music = music[: int(duration * 1000)] - 22
            music = music.fade_in(1500).fade_out(2200)
            combined = music.overlay(narration)
        else:
            combined = narration

        combined.export(output_path, format="mp3", bitrate="192k")
        return output_path

    def _pick_music_asset(self) -> Path | None:
        candidates = []
        for suffix in ("*.mp3", "*.wav", "*.m4a", "*.ogg"):
            candidates.extend(settings.paths.music.glob(suffix))
        return random.choice(candidates) if candidates else None
