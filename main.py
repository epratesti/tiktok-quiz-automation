from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import ensure_directories, settings
from create_video import VideoCreator
from generate_questions import QuestionGenerator, question_to_dict
from generate_voice import VoiceGenerator
from hashtags import build_caption
from upload_tiktok import TikTokUploader


def setup_logging() -> None:
    ensure_directories()
    log_file = settings.paths.logs / "app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(log_file, encoding="utf-8")],
    )


def run_pipeline(videos: int | None = None, upload: bool | None = None) -> list[dict]:
    setup_logging()
    ensure_directories()
    logger = logging.getLogger("pipeline")
    batch_size = videos or settings.video.batch_size
    logger.info("Iniciando pipeline: %s videos", batch_size)

    questions = QuestionGenerator().generate_batch(batch_size)
    voice_generator = VoiceGenerator()
    video_creator = VideoCreator()
    uploader = TikTokUploader()
    results = []

    for index, question in enumerate(questions, start=1):
        video_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{index}_{uuid.uuid4().hex[:6]}"
        cta = random.choice(settings.ctas)
        caption = build_caption(question.hook, question.category, cta)
        logger.info("Gerando video %s/%s: %s", index, batch_size, question.question)

        narration = voice_generator.generate(question, video_id, cta)
        artifacts = video_creator.create(question, narration, video_id)

        should_upload = settings.tiktok.upload_enabled if upload is None else upload
        upload_result = uploader.upload(artifacts["video"], caption, artifacts.get("thumbnail")) if should_upload else None

        record = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "video_id": video_id,
            "question": question_to_dict(question),
            "caption": caption,
            "artifacts": {key: str(value) for key, value in artifacts.items()},
            "upload": upload_result.__dict__ if upload_result else {"attempted": False, "success": True, "mode": "disabled"},
        }
        append_analytics(record)
        results.append(record)

    logger.info("Pipeline finalizado: %s videos processados", len(results))
    return results


def append_analytics(record: dict) -> None:
    settings.paths.analytics_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with settings.paths.analytics_jsonl.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gerador automatizado de quizzes virais para TikTok.")
    parser.add_argument("--videos", type=int, default=settings.video.batch_size, help="Quantidade de videos por execucao.")
    parser.add_argument("--upload", action="store_true", help="Forca tentativa de upload se credenciais estiverem configuradas.")
    parser.add_argument("--no-upload", action="store_true", help="Gera videos sem publicar.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    upload_flag = True if args.upload else None
    if args.no_upload:
        upload_flag = False
    run_pipeline(videos=args.videos, upload=upload_flag)
