from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from generate_questions import QuizQuestion

logger = logging.getLogger(__name__)


@dataclass
class MultiQuestionScript:
    """Roteiro para múltiplas perguntas em um único vídeo."""
    segments: list[dict[str, Any]]  # Lista de segmentos de áudio com timestamps
    questions: list[QuizQuestion]   # Perguntas incluídas
    total_duration: float           # Duração total do vídeo em segundos


class MultiQuestionBuilder:
    """Constrói roteiros e layouts para múltiplas perguntas em um vídeo."""
    
    def __init__(self, questions_per_video: int = 3, video_duration: int = 60):
        self.questions_per_video = questions_per_video
        self.video_duration = video_duration
        # Calcula tempo por pergunta (deixa margem para abertura/fechamento)
        self.time_per_question = (video_duration - 5) / questions_per_video
        
    def build_multi_question_script(self, questions: list[QuizQuestion], cta: str) -> MultiQuestionScript:
        """Constrói um roteiro para múltiplas perguntas em um único vídeo."""
        from generate_voice import natural_voice_text
        
        if len(questions) > self.questions_per_video:
            questions = questions[:self.questions_per_video]
        
        segments: list[dict[str, Any]] = []
        current_time = 0.5
        
        # Abertura
        segments.append({
            "start": current_time,
            "end": current_time + 2.0,
            "text": "3 Quizzes rápidos para você acertar!",
            "type": "intro"
        })
        current_time += 2.5
        
        # Cada pergunta
        for idx, question in enumerate(questions):
            question_duration = self.time_per_question - 1.0  # Margem entre perguntas
            
            hook = natural_voice_text(question.hook)
            question_text = natural_voice_text(question.question)
            options = [natural_voice_text(option) for option in question.options]
            answer = natural_voice_text(question.correct_answer)
            answer_letter = "ABCD"[question.correct_index]
            
            # Hook
            segments.append({
                "start": current_time,
                "end": current_time + 1.2,
                "text": f"Pergunta {idx + 1}: {hook}",
                "type": "hook",
                "question_idx": idx
            })
            current_time += 1.3
            
            # Pergunta
            segments.append({
                "start": current_time,
                "end": current_time + 1.5,
                "text": question_text,
                "type": "question",
                "question_idx": idx
            })
            current_time += 1.6
            
            # Opções (mais rápidas)
            option_time = 0.8
            for opt_idx, option in enumerate(options):
                segments.append({
                    "start": current_time,
                    "end": current_time + option_time,
                    "text": f"Opção {'ABCD'[opt_idx]}: {option}",
                    "type": "option",
                    "question_idx": idx,
                    "option_idx": opt_idx
                })
                current_time += option_time + 0.1
            
            # Suspense (mais curto)
            segments.append({
                "start": current_time,
                "end": current_time + 0.8,
                "text": "Qual é a resposta?",
                "type": "suspense",
                "question_idx": idx
            })
            current_time += 0.9
            
            # Resposta
            segments.append({
                "start": current_time,
                "end": current_time + 1.2,
                "text": f"Resposta: Letra {answer_letter}. {answer}",
                "type": "answer",
                "question_idx": idx
            })
            current_time += 1.5
        
        # CTA final
        from generate_voice import natural_voice_text
        cta_text = natural_voice_text(cta)
        segments.append({
            "start": current_time,
            "end": min(current_time + 1.5, self.video_duration - 0.5),
            "text": cta_text,
            "type": "cta"
        })
        
        return MultiQuestionScript(
            segments=segments,
            questions=questions,
            total_duration=float(self.video_duration)
        )
    
    def get_question_timing(self, question_idx: int) -> tuple[float, float]:
        """Retorna o tempo de início e fim para uma pergunta específica no vídeo."""
        start = 2.5 + (question_idx * self.time_per_question)
        end = start + self.time_per_question - 1.0
        return (start, end)
