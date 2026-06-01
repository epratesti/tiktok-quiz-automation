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
    question_type: str = "conhecimentos_gerais"
    topic: str = ""

    @property
    def correct_answer(self) -> str:
        return self.options[self.correct_index]

    def signature(self) -> str:
        """Gera uma assinatura baseada em palavras-chave para detectar e bloquear paráfrases e temas repetidos."""
        # Normalização: remove pontuação e mantém apenas palavras significativas (>3 letras)
        clean_q = re.sub(r"[^a-z0-9\s]", "", self.question.lower())
        words = [w for w in clean_q.split() if len(w) > 3 or any(char.isdigit() for char in w)]
        # Ordenamos as palavras para que a ordem da frase não mude a assinatura (detecta a mesma ideia reescrita)
        words.sort()
        # Se a pergunta for muito curta, usamos a resposta correta para ajudar na distinção
        if len(words) < 3:
            clean_ans = re.sub(r"[^a-z0-9\s]", "", self.correct_answer.lower())
            words.extend([w for w in clean_ans.split() if len(w) > 2])
            words.sort()
            
        content_id = "".join(words)
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
        self.signatures = self._load()
        logger.info(f"Histórico carregado: {len(self.signatures)} perguntas registradas.")

    def _load(self) -> list[str]:
        signatures = []
        # 1. Tenta carregar do histórico principal
        if self.path.exists():
            try:
                content = self.path.read_text(encoding="utf-8").strip()
                if content:
                    data = json.loads(content)
                    signatures = list(data.get("signatures", []))
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
                
        return signatures

    def _generate_sig_from_data(self, data: dict) -> str:
        """Recria a assinatura a partir de dados brutos do JSON para redundância."""
        try:
            import hashlib
            import re
            question_text = data.get("question", "").lower()
            clean_q = re.sub(r"[^a-z0-9\s]", "", question_text)
            words = [w for w in clean_q.split() if len(w) > 3 or any(char.isdigit() for char in w)]
            words.sort()
            if len(words) < 3:
                options = data.get("options", [])
                correct_idx = data.get("correct_index", 0)
                if options:
                    ans = options[correct_idx].lower()
                    clean_ans = re.sub(r"[^a-z0-9\s]", "", ans)
                    words.extend([w for w in clean_ans.split() if len(w) > 2])
                    words.sort()
            content_id = "".join(words)
            return hashlib.sha256(content_id.encode("utf-8")).hexdigest()[:32]
        except:
            return ""

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

    def _generate_batch_legacy(self, count: int = 3) -> list[QuizQuestion]:
        """Gera perguntas inéditas garantindo diversidade e eliminando dominância de matemática."""
        selected: list[QuizQuestion] = []
        categories_used = set()
        max_attempts = 30
        attempt = 0

        # Fontes disponíveis - Prioridade para Conhecimentos Gerais
        sources = [self._from_openai, self._from_open_trivia, self._from_local_json, self._from_synthetic_conhecimentos_gerais]

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

            if not q:
                synthetic_qs = self._from_synthetic_conhecimentos_gerais(4)
                q = next((item for item in synthetic_qs if not self.history.seen(item)), None)

            if q and not self.history.seen(q):
                selected.append(q)
                categories_used.add(q.category)
                self.history.add_many([q])

        # Apenas como ÚLTIMO RECURSO absoluto para não quebrar o vídeo
        # Removido o gerador de matemática sintética para evitar vídeos indesejados
        if len(selected) < count:
            logger.warning("Nao foi possivel gerar perguntas ineditas suficientes nas APIs. Usando banco ampliado de conhecimentos gerais.")
            emergency_qs = self._from_synthetic_conhecimentos_gerais(80) + self._from_local_json(40)
            for eq in emergency_qs:
                if len(selected) >= count:
                    break
                if self.history.seen(eq):
                    continue
                selected.append(eq)
                categories_used.add(eq.category)
                self.history.add_many([eq])

        return selected[:count]

    def generate_batch(self, count: int = 3) -> list[QuizQuestion]:
        """Gera perguntas ineditas mantendo cada video dentro de um mesmo tipo."""
        selected: list[QuizQuestion] = []
        categories_used: set[str] = set()
        topics_used: set[str] = set()
        max_attempts = 30
        attempt = 0
        target_type = self._select_question_type()
        logger.info("Tipo de perguntas escolhido para este video: %s", target_type)

        sources = [
            ("question_banks", lambda limit: self._from_question_banks(limit, target_type)),
            ("openai", self._from_openai),
            ("opentdb", self._from_open_trivia),
            ("local_json", self._from_local_json),
            ("synthetic", self._from_synthetic_conhecimentos_gerais),
            ("procedural", self._from_procedural_conhecimentos_gerais),
        ]

        while len(selected) < count and attempt < max_attempts:
            attempt += 1
            current_sources = sources.copy()
            random.shuffle(current_sources)

            for source_name, source_func in current_sources:
                if len(selected) >= count:
                    break
                try:
                    candidates = source_func(count * 3)
                    for q in candidates:
                        if self._can_select_question(q, selected, categories_used, topics_used, target_type):
                            selected.append(q)
                            categories_used.add(q.category)
                            if q.topic:
                                topics_used.add(q.topic)
                            self.history.add_many([q])
                            logger.info("Selecionada (%s | %s): %s...", q.source, q.category, q.question[:50])
                            if len(selected) >= count:
                                break
                except Exception as exc:
                    logger.debug("Fonte %s falhou: %s", source_name, exc)

        if len(selected) < count:
            logger.warning("Nao foi possivel gerar %s perguntas ineditas do tipo %s.", count, target_type)

        return selected[:count]

    def _can_select_question(
        self,
        question: QuizQuestion,
        selected: list[QuizQuestion],
        categories_used: set[str],
        topics_used: set[str],
        target_type: str,
    ) -> bool:
        if question.question_type != target_type:
            return False
        if self.history.seen(question):
            return False
        if question.question_type not in {"charadas", "raciocinio_logico"} and question.category in categories_used:
            return False
        if question.topic and question.topic in topics_used:
            return False

        is_math = "matematica" in self._plain(question.category) or "raciocinio logico" in self._plain(question.category)
        has_math = any("matematica" in self._plain(item.category) or "raciocinio logico" in self._plain(item.category) for item in selected)
        return not (is_math and has_math)

    def _select_question_type(self) -> str:
        requested_type = os.getenv("QUESTION_TYPE", "").strip().lower()
        if requested_type:
            return requested_type

        return "conhecimentos_gerais"

    def _available_question_types(self) -> list[str]:
        types = set()
        for question in self._from_question_banks(500):
            types.add(question.question_type)
        return sorted(types)

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
            "FOCO: Questões desafiadoras das bancas FCC, FGV ou CESPE.\n"
            "REQUISITOS:\n"
            "1. Nível de dificuldade: DIFÍCIL (Nível Superior).\n"
            "2. Linguagem: Técnica, formal e precisa.\n"
            "3. Explicação: Didática e fundamentada.\n"
            "4. Inovação: Evite temas manjados; busque detalhes importantes do edital.\n"
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
            questions = []
            for item in data.get("questions", []):
                item.setdefault("type", "concursos")
                item.setdefault("topic", item.get("category", "concursos"))
                questions.append(self._normalize(item, "openai"))
            return questions
        except Exception as e:
            logger.error(f"Erro OpenAI: {e}")
            return []

    def _from_open_trivia(self, limit: int) -> list[QuizQuestion]:
        """Busca perguntas no Open Trivia DB filtrando por temas de Conhecimentos Gerais (História, Geografia, Ciência)."""
        if not settings.ai.opentrivia_enabled or not requests:
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
                    "type": "conhecimentos_gerais",
                    "topic": html.unescape(item["category"]),
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
        if not settings.ai.openai_enabled or not settings.ai.openai_api_key:
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

    def _from_question_banks(self, limit: int, question_type: str | None = None) -> list[QuizQuestion]:
        bank_dir = settings.paths.data / "question_banks"
        if not bank_dir.exists():
            return []

        questions: list[QuizQuestion] = []
        for path in sorted(bank_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items = data.get("questions", [])
                for index, item in enumerate(items, start=1):
                    errors = self._validate_question_item(item)
                    if errors:
                        logger.warning("Pergunta invalida em %s #%s: %s", path.name, index, "; ".join(errors))
                        continue
                    q = self._normalize(item, f"bank:{path.stem}")
                    if question_type and q.question_type != question_type:
                        continue
                    questions.append(q)
            except Exception as exc:
                logger.error("Erro ao carregar banco %s: %s", path, exc)

        random.shuffle(questions)
        return questions[:limit]

    def _validate_question_item(self, item: dict) -> list[str]:
        errors = []
        for field_name in ("type", "topic", "category", "hook", "question", "options", "correct_index", "explanation"):
            if field_name not in item:
                errors.append(f"campo ausente: {field_name}")

        options = item.get("options", [])
        if not isinstance(options, list) or len(options) != 4:
            errors.append("options precisa ter exatamente 4 alternativas")
        elif len({str(option).strip().lower() for option in options}) != 4:
            errors.append("options tem alternativas duplicadas")

        correct_index = item.get("correct_index")
        if not isinstance(correct_index, int) or correct_index < 0 or correct_index > 3:
            errors.append("correct_index precisa ser um inteiro de 0 a 3")

        for field_name in ("type", "topic", "category", "hook", "question", "explanation"):
            value = item.get(field_name, "")
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{field_name} precisa ser texto nao vazio")

        question = str(item.get("question", ""))
        explanation = str(item.get("explanation", ""))
        if len(question) > 150:
            errors.append("question esta longa demais para o layout")
        if len(explanation) > 180:
            errors.append("explanation esta longa demais para narracao curta")

        return errors

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

    def _from_synthetic_conhecimentos_gerais(self, limit: int) -> list[QuizQuestion]:
        """Gera um banco local grande de conhecimentos gerais sem repetir assinaturas."""
        facts = [
            ("Geografia", "Amazonia", "maior bioma brasileiro em extensao territorial", "A Amazonia ocupa a maior area entre os biomas do Brasil."),
            ("Geografia", "Cerrado", "bioma brasileiro conhecido por savanas tropicais", "O Cerrado tem vegetacao adaptada a seca sazonal e grande biodiversidade."),
            ("Geografia", "Pantanal", "grande planicie inundavel brasileira", "O Pantanal se destaca pelo regime de cheias e por sua biodiversidade."),
            ("Geografia", "Caatinga", "bioma exclusivamente brasileiro do semiarido", "A Caatinga e o unico bioma exclusivamente brasileiro."),
            ("Geografia", "Pampa", "bioma de campos predominante no Rio Grande do Sul", "O Pampa brasileiro aparece principalmente no Rio Grande do Sul."),
            ("Geografia", "Mata Atlantica", "bioma muito reduzido pela ocupacao historica do litoral", "A Mata Atlantica foi intensamente desmatada desde a colonizacao."),
            ("Geografia", "Rio Amazonas", "rio de maior volume de agua do mundo", "O rio Amazonas e reconhecido pelo enorme volume de agua."),
            ("Geografia", "Planalto Central", "area associada a Brasilia e ao Distrito Federal", "Brasilia foi construida no Planalto Central."),
            ("Geografia", "Equador", "linha imaginaria que divide a Terra em hemisferios norte e sul", "A Linha do Equador divide a Terra em hemisferio norte e sul."),
            ("Geografia", "Meridiano de Greenwich", "referencia para a longitude zero", "O Meridiano de Greenwich e a referencia internacional da longitude zero."),
            ("Historia do Brasil", "Independencia do Brasil", "processo proclamado em 1822", "A Independencia do Brasil foi proclamada em 1822."),
            ("Historia do Brasil", "Abolicao da escravidao", "evento formalizado pela Lei Aurea em 1888", "A Lei Aurea aboliu formalmente a escravidao em 1888."),
            ("Historia do Brasil", "Proclamacao da Republica", "evento ocorrido em 1889", "A Republica foi proclamada no Brasil em 1889."),
            ("Historia do Brasil", "Constituicao de 1988", "texto conhecido como Constituicao Cidada", "A Constituicao de 1988 marcou a redemocratizacao brasileira."),
            ("Historia do Brasil", "Era Vargas", "periodo iniciado com a Revolucao de 1930", "A Era Vargas comecou apos a Revolucao de 1930."),
            ("Historia do Brasil", "Inconfidencia Mineira", "movimento associado a Tiradentes", "Tiradentes e o personagem mais lembrado da Inconfidencia Mineira."),
            ("Historia do Brasil", "Guerra de Canudos", "conflito ocorrido no interior da Bahia", "Canudos ocorreu no sertao baiano no fim do seculo XIX."),
            ("Historia do Brasil", "Ciclo do ouro", "atividade colonial que fortaleceu Minas Gerais", "A exploracao do ouro impulsionou a ocupacao de Minas Gerais."),
            ("Historia Geral", "Revolucao Francesa", "processo iniciado em 1789", "A Revolucao Francesa comecou em 1789 e transformou a politica moderna."),
            ("Historia Geral", "Revolucao Industrial", "processo associado a mecanizacao da producao", "A Revolucao Industrial ampliou o uso de maquinas na producao."),
            ("Historia Geral", "Guerra Fria", "disputa geopolitica entre Estados Unidos e Uniao Sovietica", "A Guerra Fria marcou a rivalidade entre EUA e URSS."),
            ("Historia Geral", "Renascimento", "movimento cultural europeu de valorizacao do humanismo", "O Renascimento valorizou o humanismo e a cultura classica."),
            ("Ciencia", "Fotossintese", "processo em que plantas produzem glicose usando luz", "Na fotossintese, plantas usam luz, agua e gas carbonico para produzir glicose."),
            ("Ciencia", "Mitocondria", "organela associada a producao de energia celular", "A mitocondria participa da respiracao celular e da producao de ATP."),
            ("Ciencia", "DNA", "molecula que armazena informacao genetica", "O DNA carrega instrucoes geneticas dos seres vivos."),
            ("Ciencia", "Evaporacao", "transformacao de agua liquida em vapor pela acao do calor", "Evaporacao e a transformacao de liquido em vapor."),
            ("Ciencia", "Condensacao", "formacao de goticulas quando o vapor esfria", "Condensacao ocorre quando vapor se transforma em liquido."),
            ("Ciencia", "Gravidade", "forca de atracao entre corpos com massa", "A gravidade atrai corpos que possuem massa."),
            ("Ciencia", "Oxigenio", "gas essencial para respiracao humana", "O oxigenio participa da respiracao celular humana."),
            ("Ciencia", "Agua", "substancia formada por hidrogenio e oxigenio", "A formula da agua e H2O."),
            ("Literatura", "Machado de Assis", "autor de Dom Casmurro", "Machado de Assis escreveu Dom Casmurro."),
            ("Literatura", "Carlos Drummond de Andrade", "poeta modernista brasileiro", "Drummond e um dos grandes nomes da poesia modernista brasileira."),
            ("Literatura", "Clarice Lispector", "autora de A Hora da Estrela", "Clarice Lispector escreveu A Hora da Estrela."),
            ("Literatura", "Modernismo brasileiro", "movimento marcado pela Semana de Arte Moderna de 1922", "A Semana de 1922 e marco do Modernismo no Brasil."),
            ("Literatura", "Realismo", "escola literaria associada a critica social e psicologica", "O Realismo buscou analisar a sociedade com olhar critico."),
            ("Artes e Cultura", "Aleijadinho", "artista barroco brasileiro ligado a Minas Gerais", "Aleijadinho e grande nome do barroco mineiro."),
            ("Artes e Cultura", "Tarsila do Amaral", "artista modernista autora de Abaporu", "Tarsila do Amaral pintou Abaporu."),
            ("Artes e Cultura", "Oscar Niemeyer", "arquiteto associado a Brasilia", "Niemeyer projetou importantes edificios de Brasilia."),
            ("Artes e Cultura", "Samba", "manifestacao cultural fortemente ligada ao Brasil", "O samba e uma das principais expressoes culturais brasileiras."),
            ("Politica e Cidadania", "Tres Poderes", "Executivo, Legislativo e Judiciario", "A organizacao classica dos poderes inclui Executivo, Legislativo e Judiciario."),
            ("Politica e Cidadania", "Voto direto", "forma em que o eleitor escolhe diretamente seu representante", "No voto direto, o eleitor vota diretamente no candidato ou opcao."),
            ("Politica e Cidadania", "Cidadania", "exercicio de direitos e deveres na vida publica", "Cidadania envolve participacao social, direitos e deveres."),
            ("Politica e Cidadania", "Soberania popular", "ideia de que o poder emana do povo", "A soberania popular expressa que o poder tem origem no povo."),
            ("Economia", "Inflacao", "aumento generalizado e persistente dos precos", "Inflacao e a alta continua e generalizada dos precos."),
            ("Economia", "PIB", "soma dos bens e servicos finais produzidos em um periodo", "O PIB mede a producao final de bens e servicos de uma economia."),
            ("Economia", "Juros", "remuneracao pelo uso do dinheiro ao longo do tempo", "Juros representam o custo ou remuneracao do dinheiro no tempo."),
            ("Economia", "Oferta e demanda", "relacao que influencia precos de mercado", "Precos podem variar conforme oferta e demanda."),
            ("Matematica Basica", "Porcentagem", "representacao de uma parte em cem", "Porcentagem indica uma proporcao em relacao a cem."),
            ("Matematica Basica", "Media aritmetica", "soma dos valores dividida pela quantidade de valores", "A media aritmetica e calculada somando valores e dividindo pela quantidade."),
            ("Matematica Basica", "Regra de tres", "tecnica para resolver proporcoes", "Regra de tres usa proporcionalidade entre grandezas."),
            ("Matematica Basica", "Numero primo", "numero natural com exatamente dois divisores positivos", "Numero primo possui apenas dois divisores positivos: 1 e ele mesmo."),
            ("Informatica", "Phishing", "golpe que tenta obter dados por mensagem enganosa", "Phishing usa mensagens falsas para roubar informacoes."),
            ("Informatica", "Backup", "copia de seguranca dos dados", "Backup e uma copia criada para recuperar dados em caso de perda."),
            ("Informatica", "Firewall", "filtro de trafego de rede", "Firewall ajuda a controlar conexoes permitidas e bloqueadas."),
            ("Informatica", "Criptografia", "tecnica para proteger informacoes por codificacao", "Criptografia transforma dados para proteger seu conteudo."),
            ("Informatica", "Autenticacao em dois fatores", "camada extra de verificacao de identidade", "A autenticacao em dois fatores acrescenta uma etapa alem da senha."),
        ]

        subjects_by_category: dict[str, list[str]] = {}
        for fact_category, fact_subject, *_ in facts:
            subjects_by_category.setdefault(fact_category, []).append(fact_subject)

        questions = []
        shuffled = facts.copy()
        random.shuffle(shuffled)
        for category, subject, description, explanation in shuffled:
            same_category_subjects = subjects_by_category.get(category, [])
            wrong_options = [item for item in same_category_subjects if item != subject]
            options = random.sample(wrong_options, k=3) + [subject]
            random.shuffle(options)
            if category.startswith("Historia"):
                question = f"Qual fato historico esta corretamente associado a esta descricao: {description}?"
            elif category == "Geografia":
                question = f"Em geografia, qual alternativa identifica corretamente: {description}?"
            elif category == "Ciencia":
                question = f"Nas ciencias, qual conceito corresponde a esta definicao: {description}?"
            elif category == "Literatura":
                question = f"Na literatura, qual alternativa combina com esta pista: {description}?"
            elif category in {"Artes e Cultura", "Politica e Cidadania", "Economia", "Informatica"}:
                question = f"Em {category.lower()}, qual alternativa esta ligada a: {description}?"
            else:
                question = f"Qual alternativa de conhecimentos gerais corresponde a: {description}?"
            questions.append(
                QuizQuestion(
                    id=self._stable_id(f"{category}:{subject}:{description}"),
                    category=category,
                    hook="Conhecimentos gerais de concurso!",
                    question=question,
                    options=options,
                    correct_index=options.index(subject),
                    explanation=explanation,
                    source="synthetic_general_knowledge",
                    difficulty="medio",
                    question_type="conhecimentos_gerais",
                    topic=self._plain(subject),
                )
            )

        return questions[:limit]

    def _from_procedural_conhecimentos_gerais(self, limit: int) -> list[QuizQuestion]:
        """Gera perguntas factuais offline com alta variedade para manter a automacao ativa sem API."""
        templates: list[tuple[str, str, str, list[tuple[str, str, str]]]] = [
            (
                "Geografia do Brasil",
                "capitais_brasileiras",
                "Qual capital brasileira corresponde ao estado de {prompt}?",
                [
                    ("Acre", "Rio Branco", "Rio Branco e a capital do Acre."),
                    ("Alagoas", "Maceio", "Maceio e a capital de Alagoas."),
                    ("Amapa", "Macapa", "Macapa e a capital do Amapa."),
                    ("Amazonas", "Manaus", "Manaus e a capital do Amazonas."),
                    ("Bahia", "Salvador", "Salvador e a capital da Bahia."),
                    ("Ceara", "Fortaleza", "Fortaleza e a capital do Ceara."),
                    ("Distrito Federal", "Brasilia", "Brasilia e a capital federal do Brasil."),
                    ("Espirito Santo", "Vitoria", "Vitoria e a capital do Espirito Santo."),
                    ("Goias", "Goiania", "Goiania e a capital de Goias."),
                    ("Maranhao", "Sao Luis", "Sao Luis e a capital do Maranhao."),
                    ("Mato Grosso", "Cuiaba", "Cuiaba e a capital de Mato Grosso."),
                    ("Mato Grosso do Sul", "Campo Grande", "Campo Grande e a capital de Mato Grosso do Sul."),
                    ("Minas Gerais", "Belo Horizonte", "Belo Horizonte e a capital de Minas Gerais."),
                    ("Para", "Belem", "Belem e a capital do Para."),
                    ("Paraiba", "Joao Pessoa", "Joao Pessoa e a capital da Paraiba."),
                    ("Parana", "Curitiba", "Curitiba e a capital do Parana."),
                    ("Pernambuco", "Recife", "Recife e a capital de Pernambuco."),
                    ("Piaui", "Teresina", "Teresina e a capital do Piaui."),
                    ("Rio de Janeiro", "Rio de Janeiro", "Rio de Janeiro e a capital do estado de mesmo nome."),
                    ("Rio Grande do Norte", "Natal", "Natal e a capital do Rio Grande do Norte."),
                    ("Rio Grande do Sul", "Porto Alegre", "Porto Alegre e a capital do Rio Grande do Sul."),
                    ("Rondonia", "Porto Velho", "Porto Velho e a capital de Rondonia."),
                    ("Roraima", "Boa Vista", "Boa Vista e a capital de Roraima."),
                    ("Santa Catarina", "Florianopolis", "Florianopolis e a capital de Santa Catarina."),
                    ("Sao Paulo", "Sao Paulo", "Sao Paulo e a capital do estado de mesmo nome."),
                    ("Sergipe", "Aracaju", "Aracaju e a capital de Sergipe."),
                    ("Tocantins", "Palmas", "Palmas e a capital do Tocantins."),
                ],
            ),
            (
                "Geografia Mundial",
                "capitais_mundiais",
                "Qual cidade e capital de {prompt}?",
                [
                    ("Argentina", "Buenos Aires", "Buenos Aires e a capital da Argentina."),
                    ("Chile", "Santiago", "Santiago e a capital do Chile."),
                    ("Uruguai", "Montevideu", "Montevideu e a capital do Uruguai."),
                    ("Paraguai", "Assuncao", "Assuncao e a capital do Paraguai."),
                    ("Peru", "Lima", "Lima e a capital do Peru."),
                    ("Colombia", "Bogota", "Bogota e a capital da Colombia."),
                    ("Mexico", "Cidade do Mexico", "Cidade do Mexico e a capital mexicana."),
                    ("Canada", "Ottawa", "Ottawa e a capital do Canada."),
                    ("Portugal", "Lisboa", "Lisboa e a capital de Portugal."),
                    ("Espanha", "Madri", "Madri e a capital da Espanha."),
                    ("Franca", "Paris", "Paris e a capital da Franca."),
                    ("Italia", "Roma", "Roma e a capital da Italia."),
                    ("Alemanha", "Berlim", "Berlim e a capital da Alemanha."),
                    ("Japao", "Toquio", "Toquio e a capital do Japao."),
                    ("Coreia do Sul", "Seul", "Seul e a capital da Coreia do Sul."),
                    ("Australia", "Camberra", "Camberra e a capital da Australia."),
                ],
            ),
            (
                "Ciencia",
                "simbolos_quimicos",
                "Na tabela periodica, qual elemento tem simbolo {prompt}?",
                [
                    ("H", "Hidrogenio", "H e o simbolo do hidrogenio."),
                    ("O", "Oxigenio", "O e o simbolo do oxigenio."),
                    ("C", "Carbono", "C e o simbolo do carbono."),
                    ("N", "Nitrogenio", "N e o simbolo do nitrogenio."),
                    ("Fe", "Ferro", "Fe e o simbolo do ferro."),
                    ("Au", "Ouro", "Au e o simbolo do ouro."),
                    ("Ag", "Prata", "Ag e o simbolo da prata."),
                    ("Na", "Sodio", "Na e o simbolo do sodio."),
                    ("K", "Potassio", "K e o simbolo do potassio."),
                    ("Ca", "Calcio", "Ca e o simbolo do calcio."),
                    ("He", "Helio", "He e o simbolo do helio."),
                    ("Cu", "Cobre", "Cu e o simbolo do cobre."),
                ],
            ),
            (
                "Historia Geral",
                "seculos",
                "A qual seculo pertence o ano {prompt}?",
                [
                    ("1500", "seculo XV", "O ano 1500 pertence ao seculo XV."),
                    ("1789", "seculo XVIII", "O ano 1789 pertence ao seculo XVIII."),
                    ("1822", "seculo XIX", "O ano 1822 pertence ao seculo XIX."),
                    ("1888", "seculo XIX", "O ano 1888 pertence ao seculo XIX."),
                    ("1930", "seculo XX", "O ano 1930 pertence ao seculo XX."),
                    ("1945", "seculo XX", "O ano 1945 pertence ao seculo XX."),
                    ("1988", "seculo XX", "O ano 1988 pertence ao seculo XX."),
                    ("2001", "seculo XXI", "O ano 2001 pertence ao seculo XXI."),
                ],
            ),
            (
                "Lingua Portuguesa",
                "classes_gramaticais",
                "Qual e a classe gramatical principal da palavra '{prompt}'?",
                [
                    ("rapidamente", "adverbio", "Rapidamente indica modo, portanto funciona como adverbio."),
                    ("feliz", "adjetivo", "Feliz caracteriza um ser ou estado, portanto e adjetivo."),
                    ("cidade", "substantivo", "Cidade nomeia um lugar, portanto e substantivo."),
                    ("correr", "verbo", "Correr expressa uma acao, portanto e verbo."),
                    ("nos", "pronome", "Nos substitui ou acompanha nomes, portanto e pronome."),
                    ("mas", "conjuncao", "Mas liga ideias com oposicao, portanto e conjuncao."),
                    ("sob", "preposicao", "Sob relaciona termos, portanto e preposicao."),
                ],
            ),
        ]

        questions: list[QuizQuestion] = []
        for category, topic_prefix, prompt, rows in templates:
            answers = list(dict.fromkeys(answer for _, answer, _ in rows))
            for clue, answer, explanation in rows:
                wrong_options = [item for item in answers if item != answer]
                if len(wrong_options) < 3:
                    continue
                options = random.sample(wrong_options, k=3) + [answer]
                random.shuffle(options)
                question_text = prompt.format(prompt=clue)
                questions.append(
                    QuizQuestion(
                        id=self._stable_id(f"{topic_prefix}:{clue}:{answer}"),
                        category=category,
                        hook="Conhecimentos gerais de concurso!",
                        question=question_text,
                        options=options,
                        correct_index=options.index(answer),
                        explanation=explanation,
                        source="procedural_general_knowledge",
                        difficulty="medio",
                        question_type="conhecimentos_gerais",
                        topic=self._plain(f"{topic_prefix}:{clue}"),
                    )
                )

        random.shuffle(questions)
        return questions[:limit]

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
        question_type = item.get("type", item.get("question_type", "conhecimentos_gerais"))
        return QuizQuestion(
            id=str(item.get("id", self._stable_id(item["question"]))),
            category=item.get("category", "geral"),
            hook=item.get("hook", random.choice(HOOKS)),
            question=item["question"],
            options=item["options"][:4],
            correct_index=item["correct_index"],
            explanation=item.get("explanation", ""),
            source=source,
            difficulty=item.get("difficulty", "medio"),
            question_type=self._plain(question_type),
            topic=self._plain(item.get("topic", item.get("category", ""))),
        )

    def _stable_id(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:12]

    def _plain(self, text: str) -> str:
        normalized = unicodedata.normalize("NFKD", str(text))
        ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")


def question_to_dict(question: QuizQuestion) -> dict[str, Any]:
    return asdict(question)
