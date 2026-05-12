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
    def create(self, question: QuizQuestion, narration: NarrationResult, video_id: str | None = None) -> dict[str, Path]:
        video_id = video_id or f"quiz_{uuid.uuid4().hex[:10]}"
        theme_name = random.choice(settings.templates)
        output_mp4 = settings.paths.output / f"{video_id}.mp4"
        output_srt = settings.paths.output / f"{video_id}.srt"
        output_thumb = settings.paths.output / f"{video_id}_thumb.jpg"
        final_audio = settings.paths.temp / f"{video_id}_final_audio.mp3"

        settings.paths.output.mkdir(parents=True, exist_ok=True)
        settings.paths.temp.mkdir(parents=True, exist_ok=True)

        logger.info("Renderizando video %s com tema %s", video_id, theme_name)
        video = self._build_video_clip(question, narration, theme_name)
        audio_path = self._build_audio(narration.audio_path, final_audio)
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
        thumbnail_image(question.question, question.correct_answer, theme_name).save(output_thumb, quality=92)
        return {"video": output_mp4, "subtitles": output_srt, "thumbnail": output_thumb}

    def _build_video_clip(self, question: QuizQuestion, narration: NarrationResult, theme_name: str) -> Any:
        width, height = settings.video.width, settings.video.height
        duration = settings.video.duration
        clips = [self._background_clip(theme_name)]

        title_img = text_panel(f"{question.hook}\nQUIZ {question.category.upper()}", width - 100, 72, theme_name)
        clips.append(self._image_clip(title_img, 0, 5, ("center", 240)))

        question_img = text_panel(question.question, width - 100, 68, theme_name, padding=38)
        clips.append(self._image_clip(question_img, 5, 40, ("center", 190)))

        option_width = width - 330
        option_x = 70
        for index, option in enumerate(question.options):
            panel = option_panel("ABCD"[index], option, option_width, theme_name)
            clips.append(self._image_clip(panel, 8 + index * 1.0, 37 - index * 0.2, (option_x, 610 + index * 180)))

        timer = VideoClip(lambda t: timer_frame(t, 40, theme_name), duration=40)
        clips.append(clip_position(clip_start(timer, 5), (width - 176, 620)))

        suspense = text_panel("RESPONDA AGORA...", width - 140, 82, theme_name, padding=40)
        clips.append(self._image_clip(suspense, 45, 10, ("center", 740)))

        reveal = text_panel(
            f"RESPOSTA: {'ABCD'[question.correct_index]}\n{question.correct_answer}",
            width - 100,
            78,
            theme_name,
            padding=42,
        )
        clips.append(self._image_clip(reveal, 55, 5, ("center", 520)))

        cta = text_panel("Comente quantas você acertou", width - 140, 54, theme_name, padding=30)
        clips.append(self._image_clip(cta, 55.3, 4.7, ("center", 1320)))

        progress = VideoClip(lambda t: progress_frame(t, duration, width, theme_name), duration=duration)
        clips.append(clip_position(progress, ("center", height - 64)))

        for segment in build_segments_from_script(narration.script):
            if segment.start >= 55:
                y = 1530
            elif segment.start < 5:
                y = 1260
            else:
                y = 1460
            img = text_panel(segment.text, width - 160, 44, theme_name, padding=24, highlight=segment.emphasis)
            clips.append(self._image_clip(img, segment.start, max(0.8, segment.end - segment.start), ("center", y)))

        return CompositeVideoClip(clips, size=(width, height))

    def _background_clip(self, theme_name: str) -> Any:
        width, height = settings.video.width, settings.video.height
        duration = settings.video.duration
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

    def _build_audio(self, narration_path: Path, output_path: Path) -> Path:
        narration = AudioSegment.from_file(narration_path)
        narration = narration[: settings.video.duration * 1000]
        if len(narration) < settings.video.duration * 1000:
            narration += AudioSegment.silent(duration=settings.video.duration * 1000 - len(narration))

        music_path = self._pick_music_asset()
        if music_path:
            music = AudioSegment.from_file(music_path)
            while len(music) < settings.video.duration * 1000:
                music += music
            music = music[: settings.video.duration * 1000] - 22
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
