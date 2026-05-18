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
    questions_per_video = 3  # Padrão: 3 perguntas por vídeo
    logger.info("Iniciando pipeline: %s videos com %s perguntas cada", batch_size, questions_per_video)
    
    question_gen = QuestionGenerator()
    voice_generator = VoiceGenerator()
    video_creator = VideoCreator()
    uploader = TikTokUploader()
    results = []

    for video_idx in range(batch_size):
        logger.info("Gerando perguntas inéditas para o video %s/%s", video_idx + 1, batch_size)
        # Recarregamos o histórico a cada vídeo para garantir que duplicatas geradas 
        # na mesma execução sejam detectadas.
        question_gen.history._load()
        questions_for_video = question_gen.generate_batch(questions_per_video)
        
        if not questions_for_video:
            logger.warning("Nao ha perguntas suficientes para o video %s", video_idx + 1)
            break
            
        video_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{video_idx + 1}_{uuid.uuid4().hex[:6]}"
        cta = random.choice(settings.ctas)
        
        # Usar a primeira pergunta para o caption/hook principal
        main_question = questions_for_video[0]
        caption = build_caption(main_question.hook, main_question.category, cta)
        
        logger.info("Gerando video %s/%s com %s perguntas", video_idx + 1, batch_size, len(questions_for_video))

        # Gerar narração e vídeo para múltiplas perguntas
        # Como o código original não tinha suporte nativo a multi-pergunta no create_video.py,
        # vamos adaptar para chamar o renderizador para cada pergunta e depois concatenar ou 
        # ajustar o renderizador para aceitar a lista.
        
        # Para manter o layout EXATO de hoje à tarde, vamos ajustar o create_video.py 
        # para suportar a lista de perguntas mantendo o estilo visual.
        
        narration = voice_generator.generate_multi_question(questions_for_video, video_id, cta)
        artifacts = video_creator.create_multi_question(questions_for_video, narration, video_id, cta)

        should_upload = settings.tiktok.upload_enabled if upload is None else upload
        upload_result = uploader.upload(artifacts["video"], caption, artifacts.get("thumbnail")) if should_upload else None
        
        record = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "video_id": video_id,
            "questions": [question_to_dict(q) for q in questions_for_video],
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
