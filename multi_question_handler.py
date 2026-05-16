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
    
    def __init__(self, questions_per_video: int = 3, video_duration: int = 90):
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
        
        # Abertura (Sem a palavra "quiz")
        segments.append({
            "start": current_time,
            "end": current_time + 2.0,
            "text": "Três perguntas rápidas para você acertar!",
            "type": "intro"
        })
        current_time += 2.5
        
        # Cada pergunta
        for idx, question in enumerate(questions):
            hook = natural_voice_text(question.hook)
            question_text = natural_voice_text(question.question)
            options = [natural_voice_text(option) for option in question.options]
            answer = natural_voice_text(question.correct_answer)
            answer_letter = "ABCD"[question.correct_index]
            
            # Hook
            segments.append({
                "start": current_time,
                "end": current_time + 1.5,
                "text": f"Pergunta {idx + 1}: {hook}",
                "type": "hook",
                "question_idx": idx
            })
            current_time += 1.8
            
            # Pergunta
            segments.append({
                "start": current_time,
                "end": current_time + 2.5,
                "text": question_text,
                "type": "question",
                "question_idx": idx
            })
            current_time += 2.8
            
            # Opções
            option_time = 1.2
            for opt_idx, option in enumerate(options):
                segments.append({
                    "start": current_time,
                    "end": current_time + option_time,
                    "text": f"Opção {'ABCD'[opt_idx]}: {option}",
                    "type": "option",
                    "question_idx": idx,
                    "option_idx": opt_idx
                })
                current_time += option_time + 0.2
            
            # Suspense
            segments.append({
                "start": current_time,
                "end": current_time + 1.5,
                "text": "Qual é a resposta?",
                "type": "suspense",
                "question_idx": idx
            })
            current_time += 1.8
            
            # Resposta
            segments.append({
                "start": current_time,
                "end": current_time + 2.0,
                "text": f"Resposta: Letra {answer_letter}. {answer}",
                "type": "answer",
                "question_idx": idx
            })
            current_time += 2.5
        
        # CTA final
        cta_text = natural_voice_text(cta)
        segments.append({
            "start": current_time,
            "end": current_time + 2.0,
            "text": cta_text,
            "type": "cta"
        })
        current_time += 2.5
        
        return MultiQuestionScript(
            segments=segments,
            questions=questions,
            total_duration=float(current_time)
        )
    
    def get_question_timing(self, question_idx: int) -> tuple[float, float]:
        """Retorna o tempo de início e fim para uma pergunta específica no vídeo."""
        # Nota: Com duração variável, este método precisaria ser recalculado se usado.
        # Por enquanto, o build_multi_question_script já define os tempos.
        return (0, 0)
