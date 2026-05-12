from __future__ import annotations

import random

from config import settings


CATEGORY_HASHTAGS = {
    "geografia": ["#geografia", "#mapa", "#paises"],
    "historia": ["#historia", "#voceSabia", "#aprendanotiktok"],
    "ciencia": ["#ciencia", "#curiosidade", "#conhecimento"],
    "filmes": ["#filmes", "#cinema", "#quizfilmes"],
    "futebol": ["#futebol", "#brasileirao", "#quizfutebol"],
    "matematica rapida": ["#matematica", "#raciocinio", "#desafio"],
    "bandeiras": ["#bandeiras", "#paises", "#geografia"],
    "90% erram": ["#90porcentoerram", "#desafio", "#viral"],
    "so genios acertam": ["#genios", "#desafio", "#quiz"],
}


def build_hashtags(category: str, extra: list[str] | None = None, limit: int = 10) -> list[str]:
    tags = list(settings.base_hashtags)
    tags.extend(CATEGORY_HASHTAGS.get(category.lower(), []))
    if extra:
        tags.extend(extra)
    deduped = []
    for tag in tags:
        normalized = tag if tag.startswith("#") else f"#{tag}"
        if normalized not in deduped:
            deduped.append(normalized)
    random.shuffle(deduped)
    return deduped[:limit]


def build_caption(title: str, category: str, cta: str) -> str:
    hashtags = " ".join(build_hashtags(category))
    return f"{title}\n\n{cta}\n\n{hashtags}".strip()
