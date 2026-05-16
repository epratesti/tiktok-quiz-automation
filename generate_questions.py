from __future__ import annotations

import hashlib
import html
import json
import logging
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
        """
        Gera uma assinatura semântica simplificada.
        Remove espaços, pontuação e converte para minúsculas para evitar que
        pequenas variações de texto passem como perguntas novas.
        """
        # Normalização agressiva: apenas letras e números
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
    def __init__(self, path: Path, limit: int = 2000) -> None:
        self.path = path
        self.limit = limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.signatures = self._load()

    def _load(self) -> list[str]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return list(data.get("signatures", []))
        except (json.JSONDecodeError, OSError):
            return []

    def seen(self, question: QuizQuestion) -> bool:
        return question.signature() in self.signatures

    def add_many(self, questions: list[QuizQuestion]) -> None:
        for question in questions:
            sig = question.signature()
            if sig not in self.signatures:
                self.signatures.append(sig)
        # Mantém um histórico maior para evitar repetições a longo prazo
        self.signatures = self.signatures[-self.limit :]
        self.path.write_text(json.dumps({"signatures": self.signatures}, indent=2), encoding="utf-8")


class QuestionGenerator:
    def __init__(self) -> None:
        self.history = QuestionHistory(settings.paths.history_json)

    def generate_batch(self, count: int = 3) -> list[QuizQuestion]:
        """
        Gera um lote de perguntas garantindo que sejam inéditas.
        Prioriza OpenAI para alta qualidade e perguntas de concursos.
        """
        candidates: list[QuizQuestion] = []
        
        # 1. Tenta OpenAI primeiro (Melhor qualidade para concursos)
        try:
            candidates.extend(self._from_openai(count * 3))
        except Exception as e:
            logger.warning(f"Falha ao buscar na OpenAI: {e}")

        # 2. Fallback para OpenTrivia se precisar de mais
        if len([q for q in candidates if not self.history.seen(q)]) < count:
            try:
                candidates.extend(self._from_opentrivia(count * 2))
            except Exception as e:
                logger.warning(f"Falha ao buscar no OpenTrivia: {e}")

        # 3. Fallback para Local JSON
        if len([q for q in candidates if not self.history.seen(q)]) < count:
            candidates.extend(self._from_local_json(count * 2))

        # Filtragem rigorosa
        unique_candidates = []
        seen_sigs = set()
        for q in candidates:
            sig = q.signature()
            if sig not in seen_sigs and not self.history.seen(q):
                seen_sigs.add(sig)
                unique_candidates.append(q)

        if len(unique_candidates) < count:
            logger.warning("Poucas perguntas inéditas encontradas, gerando sintéticas de segurança.")
            while len(unique_candidates) < count:
                q = self._make_math_question()
                if not self.history.seen(q):
                    unique_candidates.append(q)

        selected = unique_candidates[:count]
        self.history.add_many(selected)
        return selected

    def _from_openai(self, limit: int) -> list[QuizQuestion]:
        if not settings.ai.openai_enabled or not settings.ai.openai_api_key:
            return []
        
        from openai import OpenAI
        client = OpenAI(api_key=settings.ai.openai_api_key)
        
        # Usa um tópico aleatório de concurso para variar as perguntas
        topics = ["Direito Administrativo", "Língua Portuguesa", "Raciocínio Lógico", "História do Brasil", "Geografia Geral", "Conhecimentos Gerais", "Atualidades"]
        topic = random.choice(topics)
        
        # Adiciona um 'seed' aleatório no prompt para forçar a IA a não repetir padrões
        random_seed = random.randint(1, 10000)
        
        prompt = (
            f"Você é um especialista em concursos públicos brasileiros. (Seed: {random_seed})\n"
            f"Gere {limit} perguntas de múltipla escolha inéditas sobre {topic}.\n"
            "As perguntas devem ser desafiadoras e no estilo de bancas como FGV, CESPE ou FCC.\n"
            "Formato JSON estrito:\n"
            '{"questions":[{"category":"...","hook":"...","question":"...","options":["A","B","C","D"],"correct_index":0,"explanation":"..."}]}'
        )

        completion = client.chat.completions.create(
            model=settings.ai.openai_model,
            messages=[{"role": "system", "content": "Você só responde em JSON."}, {"role": "user", "content": prompt}],
            temperature=1.0, # Alta temperatura para mais variedade
        )
        
        try:
            content = completion.choices[0].message.content.strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            data = json.loads(content)
            return [self._normalize(q, "openai") for q in data.get("questions", [])]
        except Exception as e:
            logger.error(f"Erro ao processar JSON da OpenAI: {e}")
            return []

    def _from_opentrivia(self, limit: int) -> list[QuizQuestion]:
        if not requests: return []
        resp = requests.get("https://opentdb.com/api.php", params={"amount": limit, "type": "multiple"}, timeout=10)
        if resp.status_code != 200: return []
        
        results = resp.json().get("results", [])
        questions = []
        for item in results:
            correct = html.unescape(item["correct_answer"])
            options = [html.unescape(a) for a in item["incorrect_answers"]] + [correct]
            random.shuffle(options)
            questions.append(QuizQuestion(
                id=self._stable_id(item["question"]),
                category=item["category"],
                hook=random.choice(HOOKS),
                question=html.unescape(item["question"]),
                options=options,
                correct_index=options.index(correct),
                explanation=f"A resposta correta é {correct}.",
                source="opentrivia"
            ))
        return questions

    def _from_local_json(self, limit: int) -> list[QuizQuestion]:
        path = settings.paths.questions_json
        if not path.exists(): return []
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("questions", [])
        random.shuffle(items)
        return [self._normalize(i, "local_json") for i in items[:limit]]

    def _make_math_question(self) -> QuizQuestion:
        a, b = random.randint(10, 50), random.randint(2, 9)
        res = a * b
        opts = list({res, res+10, res-10, res+5})
        while len(opts) < 4: opts.append(res + random.randint(1, 20))
        random.shuffle(opts)
        return QuizQuestion(
            id=f"math_{a}_{b}",
            category="matemática",
            hook="Desafio de lógica",
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
