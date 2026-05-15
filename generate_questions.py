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
except ImportError:  # Local JSON and synthetic generation still work before install.
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
        # Normaliza a pergunta removendo espaços, pontuação e convertendo para minúsculas
        # Também inclui as opções para garantir que variações da mesma pergunta sejam tratadas
        norm_q = re.sub(r"\W+", "", self.question.lower())
        norm_opts = "".join(sorted([re.sub(r"\W+", "", str(o).lower()) for o in self.options]))
        combined = f"{norm_q}|{norm_opts}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:24]


HOOKS = [
    "90% das pessoas erram essa",
    "Só gênios acertam em 10 segundos",
    "Você consegue responder sem pausar?",
    "Essa parece fácil, mas engana muita gente",
    "Teste relâmpago para o seu cérebro",
    "Se você acertar, comenta no final",
]


SYNTHETIC_BANK = [
    {
        "category": "matemática",
        "question": "Quanto é 15% de 200?",
        "options": ["15", "20", "30", "45"],
        "correct_index": 2,
        "explanation": "15% de 200 é 30.",
    },
    {
        "category": "geografia",
        "question": "Qual país tem formato parecido com uma bota?",
        "options": ["Itália", "Portugal", "Chile", "Grécia"],
        "correct_index": 0,
        "explanation": "A Itália é famosa pelo formato de bota no mapa.",
    },
    {
        "category": "curiosidades",
        "question": "Qual animal aparece no logo da Ferrari?",
        "options": ["Touro", "Cavalo", "Leão", "Águia"],
        "correct_index": 1,
        "explanation": "O símbolo da Ferrari é um cavalo empinado.",
    },
    {
        "category": "historia",
        "question": "Quem foi o primeiro imperador do Brasil?",
        "options": ["Dom Pedro I", "Dom Pedro II", "Tiradentes", "Getúlio Vargas"],
        "correct_index": 0,
        "explanation": "Dom Pedro I foi o primeiro imperador do Brasil.",
    },
    {
        "category": "ciência",
        "question": "Qual planeta é conhecido como planeta vermelho?",
        "options": ["Vênus", "Marte", "Júpiter", "Mercúrio"],
        "correct_index": 1,
        "explanation": "Marte tem aparência avermelhada por causa do óxido de ferro.",
    },
]


class QuestionHistory:
    def __init__(self, path: Path, limit: int = 800) -> None:
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
            logger.warning("Historico de perguntas invalido, recriando arquivo.")
            return []

    def seen(self, question: QuizQuestion) -> bool:
        return question.signature() in self.signatures

    def add_many(self, questions: list[QuizQuestion]) -> None:
        for question in questions:
            signature = question.signature()
            if signature not in self.signatures:
                self.signatures.append(signature)
        self.signatures = self.signatures[-self.limit :]
        self.path.write_text(json.dumps({"signatures": self.signatures}, indent=2), encoding="utf-8")


class QuestionGenerator:
    def __init__(self) -> None:
        self.history = QuestionHistory(settings.paths.history_json)

    def generate_batch(self, count: int = 2) -> list[QuizQuestion]:
        candidates: list[QuizQuestion] = []
        sources = [
            self._from_local_json,
            self._from_opentrivia,
            self._from_openai,
            self._from_synthetic,
        ]

        for source in sources:
            try:
                candidates.extend(source(max(count * 4, 8)))
            except (requests.RequestException, json.JSONDecodeError, ValueError, RuntimeError) as exc:
                logger.warning("Fonte de perguntas %s falhou: %s", source.__name__, exc)
            except Exception as exc:  # noqa: BLE001 - source fallback should keep pipeline alive
                logger.error("Erro inesperado em fonte de perguntas %s: %s", source.__name__, exc, exc_info=True)

            unique = self._dedupe(candidates)
            fresh = [question for question in unique if not self.history.seen(question)]
            if len(fresh) >= count:
                selected = self._select_balanced(fresh, count)
                self.history.add_many(selected)
                logger.info("Lote de %s perguntas gerado e salvo no historico.", len(selected))
                return selected

        # Se não houver perguntas frescas suficientes, usa o que tiver (mesmo que repetidas)
        # mas prioriza as únicas
        all_unique = self._dedupe(candidates)
        selected = self._select_balanced(all_unique, count)
        self.history.add_many(selected)
        logger.warning("Apenas %s perguntas frescas encontradas. Usando repetidas para completar o lote.", len(fresh))
        return selected

    def _from_local_json(self, limit: int) -> list[QuizQuestion]:
        path = settings.paths.questions_json
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw.get("questions", raw if isinstance(raw, list) else [])
        random.shuffle(items)
        return [self._normalize(item, "local_json") for item in items[:limit]]

    def _from_opentrivia(self, limit: int) -> list[QuizQuestion]:
        if not settings.ai.opentrivia_enabled:
            return []
        if requests is None:
            logger.warning("Pacote requests nao instalado; pulando Open Trivia DB.")
            return []
        amount = min(max(limit, 1), 20)
        response = requests.get(
            "https://opentdb.com/api.php",
            params={"amount": amount, "type": "multiple"},
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        questions = []
        for item in payload.get("results", []):
            correct = html.unescape(item["correct_answer"])
            options = [html.unescape(answer) for answer in item["incorrect_answers"]] + [correct]
            random.shuffle(options)
            questions.append(
                QuizQuestion(
                    id=self._stable_id(item["question"]),
                    category=self._map_category(html.unescape(item.get("category", "curiosidades"))),
                    hook=random.choice(HOOKS),
                    question=self._pt_hint(html.unescape(item["question"])),
                    options=options,
                    correct_index=options.index(correct),
                    explanation=f"A resposta correta é {correct}.",
                    source="opentrivia",
                    difficulty=item.get("difficulty", "medio"),
                )
            )
        return questions

    def _from_openai(self, limit: int) -> list[QuizQuestion]:
        if not settings.ai.openai_enabled or not settings.ai.openai_api_key:
            return []
        try:
            from openai import OpenAI
        except ImportError:
            logger.warning("Pacote openai nao instalado; pulando geracao via OpenAI.")
            return []

        client = OpenAI(api_key=settings.ai.openai_api_key)
        category = random.choice(settings.categories)
        prompt = (
            f"{CONCURSO_THEME_PROMPT}\n\n"
            "Gere perguntas de quiz viral para TikTok em português brasileiro. "
            "Cada pergunta deve ser curta, ter 4 alternativas, uma resposta correta, "
            "explicação curta e hook de retenção. Responda somente JSON válido no formato "
            '{"questions":[{"category":"...","hook":"...","question":"...",'
            '"options":["A","B","C","D"],"correct_index":0,"explanation":"..."}]}. '
            f"Categoria preferida: {category}. Quantidade: {min(limit, 6)}."
        )
        completion = client.chat.completions.create(
            model=settings.ai.openai_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
        )
        content = completion.choices[0].message.content or "{}"
        content = content.strip().removeprefix("```json").removesuffix("```").strip()
        payload = json.loads(content)
        return [self._normalize(item, "openai") for item in payload.get("questions", [])]

    def _from_synthetic(self, limit: int) -> list[QuizQuestion]:
        items = list(SYNTHETIC_BANK)
        random.shuffle(items)
        generated = [self._normalize(item, "synthetic") for item in items]
        while len(generated) < limit:
            generated.append(self._make_math_question())
        return generated[:limit]

    def _make_math_question(self) -> QuizQuestion:
        a = random.randint(8, 32)
        b = random.choice([3, 4, 5, 6, 7, 8, 9])
        correct = a * b
        options = sorted({correct, correct + random.randint(2, 12), correct - random.randint(2, 12), correct + b})
        while len(options) < 4:
            options.append(correct + random.randint(-20, 20))
            options = sorted(set(options))
        return QuizQuestion(
            id=self._stable_id(f"{a}x{b}"),
            category="matemática",
            hook=random.choice(HOOKS),
            question=f"Quanto é {a} x {b}?",
            options=[str(option) for option in options[:4]],
            correct_index=options[:4].index(correct),
            explanation=f"{a} vezes {b} é {correct}.",
            source="synthetic",
            difficulty="facil",
        )

    def _normalize(self, item: dict[str, Any], source: str) -> QuizQuestion:
        options = [str(option).strip() for option in item.get("options", [])][:4]
        if len(options) != 4:
            raise ValueError(f"Pergunta sem 4 alternativas: {item}")
        correct_index = int(item.get("correct_index", 0))
        if correct_index < 0 or correct_index > 3:
            correct_index = 0
        question = str(item["question"]).strip()
        return QuizQuestion(
            id=str(item.get("id") or self._stable_id(question)),
            category=str(item.get("category") or random.choice(settings.categories)).strip().lower(),
            hook=str(item.get("hook") or random.choice(HOOKS)).strip(),
            question=question,
            options=options,
            correct_index=correct_index,
            explanation=str(item.get("explanation") or f"A resposta correta é {options[correct_index]}.").strip(),
            source=source,
            difficulty=str(item.get("difficulty") or "medio"),
        )

    def _dedupe(self, questions: list[QuizQuestion]) -> list[QuizQuestion]:
        seen: set[str] = set()
        result = []
        for question in questions:
            signature = question.signature()
            if signature not in seen:
                seen.add(signature)
                result.append(question)
        return result

    def _select_balanced(self, questions: list[QuizQuestion], count: int) -> list[QuizQuestion]:
        random.shuffle(questions)
        selected: list[QuizQuestion] = []
        categories_seen: set[str] = set()
        for question in questions:
            if question.category not in categories_seen or len(selected) + len(categories_seen) >= count:
                selected.append(question)
                categories_seen.add(question.category)
            if len(selected) == count:
                break
        return selected[:count]

    def _stable_id(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

    def _map_category(self, category: str) -> str:
        lowered = category.lower()
        if "geography" in lowered:
            return "geografia"
        if "history" in lowered:
            return "historia"
        if "science" in lowered:
            return "ciencia"
        if "film" in lowered or "entertainment" in lowered:
            return "filmes"
        if "sports" in lowered:
            return "futebol"
        return "curiosidades"

    def _pt_hint(self, question: str) -> str:
        """Melhora perguntas do OpenTrivia mantendo a estrutura em inglês quando necessário."""
        # Para agora, mantém como está. Implementação futura pode usar OpenAI para tradução.
        # Exemplo de uso futuro:
        # if settings.ai.openai_enabled:
        #     return self._translate_with_openai(question)
        return question


def question_to_dict(question: QuizQuestion) -> dict[str, Any]:
    return asdict(question)
