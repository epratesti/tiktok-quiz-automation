from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import random
import re
import time
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
    def __init__(self, path: Path, limit: int = 10000) -> None:
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
        changed = False
        for question in questions:
            sig = question.signature()
            if sig not in self.signatures:
                self.signatures.append(sig)
                changed = True
        
        if not changed:
            return

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
        """Gera perguntas inéditas em Português Brasileiro garantindo diversidade de temas."""
        selected: list[QuizQuestion] = []
        categories_used = set()
        max_attempts = 20
        attempt = 0

        # Fontes disponíveis
        sources = [self._from_openai, self._from_open_trivia, self._from_local_json]

        while len(selected) < count and attempt < max_attempts:
            attempt += 1
            random.shuffle(sources)
            
            for source_func in sources:
                if len(selected) >= count: break
                try:
                    candidates = source_func(count * 2)
                    for q in candidates:
                        if len(selected) >= count: break
                        
                        # Critérios de seleção rigorosos:
                        # 1. Não pode ter sido vista no histórico (anti-repetição global)
                        # 2. Não pode ter a mesma categoria no mesmo vídeo (diversidade)
                        # 3. Não pode ser apenas matemática (limite de 1 por vídeo)
                        
                        is_math = "matemática" in q.category.lower() or "raciocínio lógico" in q.category.lower()
                        has_math = any("matemática" in s.category.lower() or "raciocínio lógico" in s.category.lower() for s in selected)
                        
                        if self.history.seen(q): continue
                        if q.category in categories_used: continue
                        if is_math and has_math: continue # Apenas uma de exatas por vídeo

                        selected.append(q)
                        categories_used.add(q.category)
                        self.history.add_many([q]) # Salva imediatamente no histórico
                        logger.info(f"Selecionada ({q.source} | {q.category}): {q.question[:50]}...")
                except Exception as e:
                    logger.debug(f"Fonte {source_func.__name__} falhou: {e}")

        # Fallback diversificado e Robusto (Evita IndexError)
        while len(selected) < count:
            q = None
            # Se ainda não temos matemática, tenta gerar uma
            if not any("matemática" in s.category.lower() for s in selected):
                q = self._make_math_question()
            
            # Se não foi matemática ou se já temos matemática, tenta OpenAI
            if not q:
                openai_qs = self._from_openai(1)
                if openai_qs:
                    q = openai_qs[0]
            
            # Se OpenAI falhou, tenta Local JSON como última esperança
            if not q:
                local_qs = self._from_local_json(1)
                if local_qs:
                    q = local_qs[0]
            
            # Se TUDO falhou (raro), gera uma matemática simples para não quebrar o código
            if not q:
                q = self._make_math_question()

            if not self.history.seen(q):
                selected.append(q)
                self.history.add_many([q])
            else:
                # Se a pergunta de fallback já foi vista, tentamos de novo com um limite de segurança
                attempt += 1
                if attempt > max_attempts + 10: break 

        return selected

    def _from_openai(self, limit: int) -> list[QuizQuestion]:
        if not settings.ai.openai_enabled or not settings.ai.openai_api_key:
            return []
        
        from openai import OpenAI
        client = OpenAI()
        
        # Lista focada em Concursos Públicos e Conhecimentos Gerais de alto nível
        topics = [
            "Direito Administrativo (Agentes Públicos, Poderes, Atos Administrativos)",
            "Direito Constitucional (Direitos Fundamentais, Organização do Estado)",
            "Língua Portuguesa Avançada (Sintaxe, Regência, Crase, Pontuação)",
            "Raciocínio Lógico-Matemático (Lógica Proposicional, Conjuntos, Análise Combinatória)",
            "Matemática para Concursos (Porcentagem, Juros Simples/Compostos, Equações)",
            "História do Brasil (Brasil Colônia, Império e República)",
            "Geografia Geral e do Brasil (Geopolítica, Urbanização, Meio Ambiente)",
            "Conhecimentos Gerais (Ciência Moderna, Grandes Descobertas, Literatura Brasileira)",
            "Atualidades (Política Internacional, Economia Brasileira, Meio Ambiente)",
            "Informática para Concursos (Segurança da Informação, Redes, Pacote Office)"
        ]
        topic = random.choice(topics)
        
        prompt = (
            f"Gere {limit} perguntas de múltipla escolha de NÍVEL CONCURSO PÚBLICO sobre {topic}.\n"
            "FOCO: Questões que poderiam estar em provas da FCC, FGV ou CESPE.\n"
            "REQUISITOS:\n"
            "1. Nível de dificuldade: Médio para Difícil.\n"
            "2. Linguagem: Técnica e formal.\n"
            "3. Explicação: Deve ser didática, explicando o porquê da resposta correta.\n"
            "4. Diversidade: Não repita conceitos básicos.\n"
            "Formato JSON estrito:\n"
            '{"questions":[{"category":"...","hook":"...","question":"...","options":["A","B","C","D"],"correct_index":0,"explanation":"..."}]}'
        )

        try:
            completion = client.chat.completions.create(
                model=settings.ai.openai_model,
                messages=[
                    {"role": "system", "content": "Você é um gerador de quiz criativo. Nunca repita perguntas anteriores."},
                    {"role": "user", "content": prompt}
                ],
                temperature=1.1, # Aumenta a criatividade
            )
            content = completion.choices[0].message.content.strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            data = json.loads(content)
            return [self._normalize(q, "openai") for q in data.get("questions", [])]
        except Exception as e:
            logger.error(f"Erro OpenAI: {e}")
            return []

    def _from_open_trivia(self, limit: int) -> list[QuizQuestion]:
        """Busca perguntas no Open Trivia DB filtrando por temas de Conhecimentos Gerais (História, Geografia, Ciência)."""
        if not requests:
            return []
            
        # Categorias úteis para concursos: 9 (Geral), 22 (Geografia), 23 (História), 17 (Ciência)
        useful_categories = [9, 22, 23, 17]
        cat = random.choice(useful_categories)
        url = f"https://opentdb.com/api.php?amount={limit}&category={cat}&type=multiple"
        try:
            response = requests.get(url, timeout=10)
            data = response.json()
            if data.get("response_code") != 0:
                return []
            
            questions = []
            for item in data.get("results", []):
                q_text = html.unescape(item["question"])
                correct = html.unescape(item["correct_answer"])
                incorrects = [html.unescape(i) for i in item["incorrect_answers"]]
                
                options = incorrects + [correct]
                random.shuffle(options)
                
                raw_q = {
                    "category": html.unescape(item["category"]),
                    "question": q_text,
                    "options": options,
                    "correct_index": options.index(correct),
                    "explanation": f"A resposta correta é {correct}."
                }
                
                # Traduz para Português usando a IA (se disponível) ou marca como pendente
                translated = self._translate_question(raw_q)
                if translated:
                    questions.append(self._normalize(translated, "opentdb"))
            
            return questions
        except Exception as e:
            logger.error(f"Erro OpenTrivia: {e}")
            return []

    def _translate_question(self, q: dict) -> dict | None:
        """Usa OpenAI para traduzir a pergunta mantendo o sentido."""
        if not settings.ai.openai_enabled:
            return None # Não queremos perguntas em inglês no TikTok brasileiro
            
        from openai import OpenAI
        client = OpenAI()
        
        prompt = (
            "Traduza a seguinte pergunta de quiz para Português Brasileiro de forma natural e atraente para o TikTok.\n"
            f"Pergunta original: {q['question']}\n"
            f"Opções: {', '.join(q['options'])}\n"
            "Retorne APENAS o JSON no formato:\n"
            '{"question":"...","options":["...","...","...","..."],"explanation":"..."}'
        )
        
        try:
            completion = client.chat.completions.create(
                model=settings.ai.openai_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            content = completion.choices[0].message.content.strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            data = json.loads(content)
            q.update(data)
            return q
        except Exception:
            return None

    def _from_local_json(self, limit: int) -> list[QuizQuestion]:
        path = settings.paths.questions_json
        if not path.exists(): return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data.get("questions", [])
            # Embaralha e pega uma amostra aleatória
            random.shuffle(items)
            return [self._normalize(i, "local_json") for i in items[:limit]]
        except Exception:
            return []

    def _make_math_question(self) -> QuizQuestion:
        """Gera perguntas de matemática de nível médio/concurso."""
        q_type = random.choice(["porcentagem", "equacao", "regra_de_tres", "aritmetica_avancada"])
        
        if q_type == "porcentagem":
            p = random.choice([5, 10, 15, 20, 25, 30])
            val = random.randint(1, 20) * 50
            res = int(val * (p / 100))
            question = f"Quanto é {p}% de {val}?"
            explanation = f"{p}% de {val} = ({p}/100) * {val} = {res}"
        elif q_type == "equacao":
            x = random.randint(2, 12)
            a = random.randint(2, 5)
            b = random.randint(1, 20)
            c = a * x + b
            question = f"Se {a}x + {b} = {c}, qual o valor de x?"
            res = x
            explanation = f"{a}x = {c} - {b} => {a}x = {c-b} => x = {res}"
        elif q_type == "regra_de_tres":
            # Ex: 2 pedreiros fazem em 6 dias. 3 pedreiros fazem em quantos? (Inversamente)
            n1, d1 = 2, 6
            n2 = 3
            res = (n1 * d1) // n2
            question = f"Se {n1} operários fazem uma obra em {d1} dias, {n2} operários farão em quantos dias?"
            explanation = f"Grandezas inversamente proporcionais: {n1} * {d1} = {n2} * x => {n1*d1} = {n2}x => x = {res}"
        else: # Aritmética mais chata
            a, b, c = random.randint(10, 30), random.randint(5, 15), random.randint(2, 5)
            res = (a * b) + c
            question = f"Qual o resultado de ({a} x {b}) + {c}?"
            explanation = f"{a} x {b} = {a*b}; {a*b} + {c} = {res}"

        opts = list({res, res + random.randint(1, 5), res - random.randint(1, 5), res + 10})
        while len(opts) < 4: opts.append(res + random.randint(6, 20))
        random.shuffle(opts)
        
        return QuizQuestion(
            id=f"math_adv_{time.time()}",
            category="Matemática",
            hook="Desafio de Concurso",
            question=question,
            options=[str(o) for o in opts],
            correct_index=opts.index(res),
            explanation=explanation,
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
