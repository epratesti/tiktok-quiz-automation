from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from generate_questions import QuestionGenerator  # noqa: E402


def main() -> int:
    generator = QuestionGenerator()
    bank_dir = ROOT / "data" / "question_banks"
    if not bank_dir.exists():
        print("ERRO: pasta data/question_banks nao encontrada.")
        return 1

    errors: list[str] = []
    signatures: Counter[str] = Counter()
    topics_by_type: dict[str, set[str]] = {}
    count = 0

    for path in sorted(bank_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path.name}: JSON invalido: {exc}")
            continue

        questions = data.get("questions")
        if not isinstance(questions, list):
            errors.append(f"{path.name}: campo questions precisa ser uma lista")
            continue

        for index, item in enumerate(questions, start=1):
            item_errors = generator._validate_question_item(item)
            if item_errors:
                errors.append(f"{path.name} #{index}: {'; '.join(item_errors)}")
                continue
            question = generator._normalize(item, f"bank:{path.stem}")
            signatures[question.signature()] += 1
            topics_by_type.setdefault(question.question_type, set()).add(question.topic)
            count += 1

    duplicated = [signature for signature, total in signatures.items() if total > 1]
    if duplicated:
        errors.append(f"assinaturas repetidas encontradas: {len(duplicated)}")

    if errors:
        print("Validacao da base falhou:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Base valida: {count} perguntas, {len(signatures)} assinaturas unicas.")
    for question_type, topics in sorted(topics_by_type.items()):
        print(f"- {question_type}: {len(topics)} topicos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
