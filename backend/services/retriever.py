from __future__ import annotations

import re

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.database import CodeChunk
from backend.models import RepositoryFile

TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
CHUNK_LINES = 80


def index_repository(
    session: Session, repository: str, files: list[RepositoryFile]
) -> int:
    paths = {file.path for file in files}
    session.execute(
        delete(CodeChunk).where(
            CodeChunk.repository == repository,
            CodeChunk.path.in_(paths),
        )
    )
    added = 0
    for file in files:
        lines = file.content.splitlines()
        for chunk_index, start in enumerate(range(0, len(lines), CHUNK_LINES)):
            content = "\n".join(lines[start : start + CHUNK_LINES])
            session.add(
                CodeChunk(
                    repository=repository,
                    path=file.path,
                    chunk_index=chunk_index,
                    content=content,
                )
            )
            added += 1
    session.commit()
    return added


def retrieve_context(
    session: Session,
    repository: str,
    code: str,
    *,
    limit: int = 4,
) -> str:
    query_terms = {token.lower() for token in TOKEN_PATTERN.findall(code)}
    if not query_terms:
        return ""

    candidates = session.scalars(
        select(CodeChunk).where(CodeChunk.repository == repository)
    ).all()
    ranked: list[tuple[float, CodeChunk]] = []
    for chunk in candidates:
        terms = {token.lower() for token in TOKEN_PATTERN.findall(chunk.content)}
        overlap = query_terms.intersection(terms)
        if overlap:
            score = len(overlap) / (len(query_terms) ** 0.5 * len(terms) ** 0.5)
            ranked.append((score, chunk))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return "\n\n".join(
        f"--- {chunk.path} (chunk {chunk.chunk_index + 1}) ---\n{chunk.content}"
        for _, chunk in ranked[:limit]
    )
