from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import random
import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None

from config import CONCURSO_THEME_PROMPT, settings

logger = logging.getLogger(__name__)


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def keyword_tokens(text: str) -> set[str]:
    stopwords = {
        "qual", "quais", "quanto", "quantos", "quando", "onde", "como", "para",
        "pela", "pelo", "pelos", "pelas", "sobre", "entre", "uma", "umas",
        "uns", "que", "com", "dos", "das", "por", "mais", "menos", "esta",
        "este", "essa", "esse", "isso", "nas", "nos", "aos", "acerca",
    }
    return {word for word in normalize_text(text).split() if len(word) > 3 and word not in stopwords}


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
        """Gera uma assinatura baseada em palavras-chave para detectar e bloquear paráfrases e temas repetidos."""
        # Normalização: remove pontuação e mantém apenas palavras significativas (>3 letras)
        words = sorted(keyword_tokens(self.question))
        # Ordenamos as palavras para que a ordem da frase não mude a assinatura (detecta a mesma ideia reescrita)
        # Se a pergunta for muito curta, usamos a resposta correta para ajudar na distinção
        if len(words) < 3:
            words.extend(sorted(keyword_tokens(self.correct_answer)))
            
        content_id = "|".join(words)
        return hashlib.sha256(content_id.encode("utf-8")).hexdigest()[:32]


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
        self.records: list[dict[str, str]] = []
        self.signatures: list[str] = []
        self.reload()
        logger.info(f"Histórico carregado: {len(self.signatures)} perguntas registradas.")

    def reload(self) -> None:
        self.records = self._load_records()
        self.signatures = []
        for record in self.records:
            signature = record.get("signature", "")
            if signature and signature not in self.signatures:
                self.signatures.append(signature)

    def _load_records(self) -> list[dict[str, str]]:
        signatures = []
        records: list[dict[str, str]] = []
        # 1. Tenta carregar do histórico principal
        if self.path.exists():
            try:
                content = self.path.read_text(encoding="utf-8").strip()
                if content:
                    data = json.loads(content)
                    signatures = list(data.get("signatures", []))
                    raw_records = data.get("records", [])
                    if isinstance(raw_records, list):
                        records = [record for record in raw_records if isinstance(record, dict)]
            except Exception as e:
                logger.error(f"Erro ao carregar histórico principal: {e}")
        
        # 2. Redundância: Tenta reconstruir a partir do analytics.jsonl
        # Isso garante que mesmo que o histórico.json falhe no push, o analytics (que é salvo como artefato) ajude.
        analytics_path = self.path.parent / "analytics.jsonl"
        if analytics_path.exists():
            try:
                with open(analytics_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip(): continue
                        record = json.loads(line)
                        for q_data in record.get("questions", []):
                            # Recriamos o objeto temporário para gerar a assinatura correta
                            # Note: O código de assinatura deve ser estável
                            sig = self._generate_sig_from_data(q_data)
                            if sig and sig not in signatures:
                                signatures.append(sig)
                logger.info(f"Histórico reforçado com analytics.jsonl. Total agora: {len(signatures)}")
            except Exception as e:
                logger.error(f"Erro ao ler analytics para redundância: {e}")
                
        existing = {record.get("signature") for record in records}
        for signature in signatures:
            if signature and signature not in existing:
                records.append({"signature": signature, "question": "", "normalized_question": ""})
                existing.add(signature)
        return records[-self.limit :]

    def _generate_sig_from_data(self, data: dict) -> str:
        """Recria a assinatura a partir de dados brutos do JSON para redundância."""
        try:
            words = sorted(keyword_tokens(data.get("question", "")))
            if len(words) < 3:
                options = data.get("options", [])
                correct_idx = data.get("correct_index", 0)
                if options:
                    words.extend(sorted(keyword_tokens(options[correct_idx])))
            content_id = "|".join(words)
            return hashlib.sha256(content_id.encode("utf-8")).hexdigest()[:32]
        except:
            return ""

    def seen(self, question: QuizQuestion) -> bool:
        sig = question.signature()
        is_seen = sig in self.signatures
        if not is_seen:
            incoming_tokens = keyword_tokens(question.question)
            for record in self.records:
                previous = record.get("question", "") or record.get("normalized_question", "")
                previous_tokens = keyword_tokens(previous)
                if not incoming_tokens or not previous_tokens:
                    continue
                similarity = len(incoming_tokens & previous_tokens) / max(len(incoming_tokens | previous_tokens), 1)
                if similarity >= 0.82:
                    is_seen = True
                    break
        if is_seen:
            logger.info(f"Pergunta repetida detectada: {question.question[:50]}...")
        return is_seen

    def add_many(self, questions: list[QuizQuestion]) -> None:
        changed = False
        for question in questions:
            sig = question.signature()
            if sig not in self.signatures:
                self.signatures.append(sig)
                self.records.append(
                    {
                        "signature": sig,
                        "question": question.question,
                        "normalized_question": normalize_text(question.question),
                        "category": question.category,
                        "correct_answer": question.correct_answer,
                        "source": question.source,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                changed = True
        
        if not changed:
            return

        # Mantém o histórico dentro do limite
        self.signatures = self.signatures[-self.limit :]
        self.records = self.records[-self.limit :]
        
        # Salvamento atômico para evitar corrupção de arquivo
        temp_path = self.path.with_suffix(".tmp")
        try:
            temp_path.write_text(
                json.dumps({"signatures": self.signatures, "records": self.records}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            temp_path.replace(self.path)
            logger.info(f"Histórico atualizado com sucesso. Total: {len(self.signatures)}")
        except Exception as e:
            logger.error(f"Erro ao salvar histórico: {e}")


class QuestionGenerator:
    def __init__(self) -> None:
        self.history = QuestionHistory(settings.paths.history_json)

    def generate_batch(self, count: int = 3) -> list[QuizQuestion]:
        """Gera perguntas inéditas garantindo diversidade e eliminando dominância de matemática."""
        selected: list[QuizQuestion] = []
        categories_used = set()
        max_attempts = 30
        attempt = 0

        # Fontes disponíveis - Prioridade para Conhecimentos Gerais
        sources = [self._from_openai, self._from_open_trivia, self._from_local_json]

        while len(selected) < count and attempt < max_attempts:
            attempt += 1
            # Se já temos matemática, removemos a fonte sintética ou forçamos outras
            current_sources = sources.copy()
            random.shuffle(current_sources)
            
            for source_func in current_sources:
                if len(selected) >= count: break
                try:
                    candidates = source_func(count * 2)
                    for q in candidates:
                        if len(selected) >= count: break
                        
                        is_math = "matemática" in q.category.lower() or "raciocínio lógico" in q.category.lower()
                        has_math = any("matemática" in s.category.lower() or "raciocínio lógico" in s.category.lower() for s in selected)
                        
                        # REGRAS RÍGIDAS:
                        # 1. Anti-repetição global
                        if self.history.seen(q): continue
                        # 2. Diversidade de categoria no mesmo vídeo
                        if q.category in categories_used: continue
                        # 3. Limite de 1 de matemática por vídeo (SÓ SE NECESSÁRIO)
                        if is_math and has_math: continue 

                        selected.append(q)
                        categories_used.add(q.category)
                        self.history.add_many([q])
                        logger.info(f"Selecionada ({q.source} | {q.category}): {q.question[:50]}...")
                except Exception as e:
                    logger.debug(f"Fonte {source_func.__name__} falhou: {e}")

        # Fallback Robusto SEM matemática automática (prioriza banco local e OpenAI)
        while len(selected) < count and attempt < max_attempts + 20:
            attempt += 1
            q = None
            
            # Tenta OpenAI primeiro para temas de concurso
            openai_qs = self._from_openai(1)
            if openai_qs: q = openai_qs[0]
            
            # Se falhou, tenta Local JSON
            if not q:
                local_qs = self._from_local_json(1)
                if local_qs: q = local_qs[0]
            
            # Se ainda assim falhou, tenta Open Trivia (Conhecimentos Gerais)
            if not q:
                trivia_qs = self._from_open_trivia(1)
                if trivia_qs: q = trivia_qs[0]

            if q and not self.history.seen(q):
                selected.append(q)
                self.history.add_many([q])

        # Apenas como ÚLTIMO RECURSO absoluto para não quebrar o vídeo
        fallback_attempts = 0
        while len(selected) < count and fallback_attempts < 100:
            fallback_attempts += 1
            fallback = self._make_math_question()
            if self.history.seen(fallback):
                continue
            selected.append(fallback)
            self.history.add_many([fallback])

        while len(selected) < count:
            fallback = self._make_emergency_question()
            selected.append(fallback)
            self.history.add_many([fallback])

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
        if not settings.ai.opentrivia_enabled:
            return []
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

    def _make_emergency_question(self) -> QuizQuestion:
        """Fallback final, variado por timestamp, para nunca reutilizar a mesma pergunta."""
        seed = int(time.time() * 1000) + random.randint(1000, 9999)
        a = random.randint(12, 89)
        b = random.randint(11, 77)
        res = a + b
        opts = list({res, res + random.randint(2, 9), res - random.randint(2, 9), res + random.randint(10, 18)})
        while len(opts) < 4:
            opts.append(res + random.randint(19, 35))
        random.shuffle(opts)
        return QuizQuestion(
            id=f"emergency_{seed}",
            category="Raciocinio Logico",
            hook="Questao relampago",
            question=f"Desafio {seed}: qual e o resultado de {a} + {b}?",
            options=[str(o) for o in opts],
            correct_index=opts.index(res),
            explanation=f"{a} + {b} = {res}.",
            source="emergency",
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
