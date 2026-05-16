from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import random
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None

from config import CONCURSO_THEME_PROMPT, settings

logger = logging.getLogger(__name__)


@dataclass
class QuizQuestion:
    id: str
    category: str
    hook: str
    question: str
    options: list[str]
    correct_index: int
    explanation: str
    source: str
    difficulty: str = "medio"

    @property
    def correct_answer(self) -> str:
        return self.options[self.correct_index]

    def signature(self) -> str:
        """Gera uma assinatura semântica rigorosa para evitar repetições."""
        # Normalização agressiva: apenas letras e números para ignorar variações de pontuação/espaço
        normalized = re.sub(r"[^a-z0-9]", "", self.question.lower())
        # Se a pergunta for muito curta, adiciona as opções na assinatura para diferenciar
        if len(normalized) < 20:
            opts = "".join(sorted([re.sub(r"[^a-z0-9]", "", o.lower()) for o in self.options]))
            normalized += opts
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


HOOKS = [
    "90% das pessoas erram essa",
    "Só gênios acertam em 10 segundos",
    "Você consegue responder sem pausar?",
    "Essa parece fácil, mas engana muita gente",
    "Teste relâmpago para o seu cérebro",
    "Se você acertar, comenta no final",
]


class QuestionHistory:
    def __init__(self, path: Path, limit: int = 5000) -> None:
        self.path = path
        self.limit = limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.signatures = self._load()
        logger.info(f"Histórico carregado: {len(self.signatures)} perguntas registradas.")

    def _load(self) -> list[str]:
        if not self.path.exists():
            logger.info("Arquivo de histórico não encontrado, iniciando novo.")
            return []
        try:
            content = self.path.read_text(encoding="utf-8").strip()
            if not content:
                return []
            data = json.loads(content)
            return list(data.get("signatures", []))
        except Exception as e:
            logger.error(f"Erro ao carregar histórico: {e}")
            return []

    def seen(self, question: QuizQuestion) -> bool:
        sig = question.signature()
        is_seen = sig in self.signatures
        if is_seen:
            logger.info(f"Pergunta repetida detectada: {question.question[:50]}...")
        return is_seen

    def add_many(self, questions: list[QuizQuestion]) -> None:
        for question in questions:
            sig = question.signature()
            if sig not in self.signatures:
                self.signatures.append(sig)
        
        # Mantém o histórico dentro do limite
        self.signatures = self.signatures[-self.limit :]
        
        # Salvamento atômico para evitar corrupção de arquivo
        temp_path = self.path.with_suffix(".tmp")
        try:
            temp_path.write_text(json.dumps({"signatures": self.signatures}, indent=2), encoding="utf-8")
            temp_path.replace(self.path)
            logger.info(f"Histórico atualizado com sucesso. Total: {len(self.signatures)}")
        except Exception as e:
            logger.error(f"Erro ao salvar histórico: {e}")


class QuestionGenerator:
    def __init__(self) -> None:
        self.history = QuestionHistory(settings.paths.history_json)

    def generate_batch(self, count: int = 3) -> list[QuizQuestion]:
        """Gera perguntas inéditas em Português Brasileiro."""
        selected: list[QuizQuestion] = []
        max_attempts = 5
        attempt = 0

        while len(selected) < count and attempt < max_attempts:
            attempt += 1
            candidates: list[QuizQuestion] = []
            
            # 1. Tenta OpenAI (Sempre em PT-BR)
            try:
                needed = count - len(selected)
                candidates.extend(self._from_openai(needed * 3))
            except Exception as e:
                logger.warning(f"Falha OpenAI: {e}")

            # 2. Fallback para Local JSON
            if len(candidates) < (count - len(selected)):
                candidates.extend(self._from_local_json(10))

            # Filtragem rigorosa
            for q in candidates:
                if len(selected) >= count:
                    break
                
                # Verifica se é inglês (filtro de segurança)
                is_english = any(word in q.question.lower() for word in [" the ", " which ", " what ", " where ", " who "])
                if is_english:
                    continue

                if not self.history.seen(q):
                    selected.append(q)
                    # Adiciona ao histórico imediatamente para evitar repetição no mesmo lote
                    self.history.add_many([q])
                    logger.info(f"Pergunta inédita selecionada: {q.question[:50]}...")

        # 3. Fallback final: Gerador Sintético se nada mais funcionar
        while len(selected) < count:
            q = self._make_math_question()
            if not self.history.seen(q):
                selected.append(q)
                self.history.add_many([q])

        return selected

    def _from_openai(self, limit: int) -> list[QuizQuestion]:
        if not settings.ai.openai_enabled or not settings.ai.openai_api_key:
            return []
        
        from openai import OpenAI
        client = OpenAI()
        
        topics = ["Direito Administrativo", "Língua Portuguesa", "Raciocínio Lógico", "História do Brasil", "Geografia do Brasil", "Conhecimentos Gerais", "Atualidades Brasileiras"]
        topic = random.choice(topics)
        
        # Instrução explícita para não repetir temas comuns
        prompt = (
            f"Gere {limit} perguntas de múltipla escolha INÉDITAS e EXCLUSIVAMENTE EM PORTUGUÊS BRASILEIRO sobre {topic}.\n"
            "As perguntas devem ser de nível de concurso público (FGV, CESPE).\n"
            "IMPORTANTE: Não gere perguntas óbvias ou que você já tenha gerado recentemente.\n"
            "Formato JSON:\n"
            '{"questions":[{"category":"...","hook":"...","question":"...","options":["A","B","C","D"],"correct_index":0,"explanation":"..."}]}'
        )

        completion = client.chat.completions.create(
            model=settings.ai.openai_model,
            messages=[{"role": "system", "content": "Você é um especialista em concursos brasileiros. Responda apenas em JSON."}, {"role": "user", "content": prompt}],
            temperature=1.0,
        )
        
        try:
            content = completion.choices[0].message.content.strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            data = json.loads(content)
            return [self._normalize(q, "openai") for q in data.get("questions", [])]
        except Exception as e:
            logger.error(f"Erro OpenAI JSON: {e}")
            return []

    def _from_local_json(self, limit: int) -> list[QuizQuestion]:
        path = settings.paths.questions_json
        if not path.exists(): return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data.get("questions", [])
            random.shuffle(items)
            return [self._normalize(i, "local_json") for i in items[:limit]]
        except Exception:
            return []

    def _make_math_question(self) -> QuizQuestion:
        a, b = random.randint(10, 99), random.randint(2, 9)
        res = a * b
        opts = list({res, res+10, res-10, res+5})
        while len(opts) < 4: opts.append(res + random.randint(1, 30))
        random.shuffle(opts)
        return QuizQuestion(
            id=f"math_{a}_{b}_{random.randint(0,1000)}",
            category="matemática",
            hook="Desafio rápido",
            question=f"Quanto é {a} vezes {b}?",
            options=[str(o) for o in opts],
            correct_index=opts.index(res),
            explanation=f"{a} x {b} = {res}",
            source="synthetic"
        )

    def _normalize(self, item: dict, source: str) -> QuizQuestion:
        return QuizQuestion(
            id=str(item.get("id", self._stable_id(item["question"]))),
            category=item.get("category", "geral"),
            hook=item.get("hook", random.choice(HOOKS)),
            question=item["question"],
            options=item["options"][:4],
            correct_index=item["correct_index"],
            explanation=item.get("explanation", ""),
            source=source
        )

    def _stable_id(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:12]

def question_to_dict(question: QuizQuestion) -> dict[str, Any]:
    return asdict(question)
