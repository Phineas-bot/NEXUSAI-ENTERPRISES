"""In-memory metadata search index."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set

from ..models import FileEntry, FileVersion, SearchDocument, ShareGrant
from .base import BaseService


@dataclass
class SearchIndexService(BaseService):
    documents: Dict[str, SearchDocument] = field(default_factory=dict)
    inverted_index: Dict[str, Set[str]] = field(default_factory=dict)

    def index_file(self, entry: FileEntry, versions: List[FileVersion], shares: List[ShareGrant]) -> None:
        doc = SearchDocument(
            file_id=entry.id,
            org_id=entry.org_id,
            name=entry.name,
            labels=list(entry.labels),
            owners=[entry.created_by],
            principals=[grant.principal_id for grant in shares],
            mime_type=entry.mime_type,
            size_bytes=entry.size_bytes,
            updated_at=entry.updated_at,
        )
        self.documents[entry.id] = doc
        self._rebuild_tokens(entry.id, doc)
        self.emit_event("search_indexed", file_id=entry.id)

    def remove_file(self, file_id: str) -> None:
        if file_id in self.documents:
            self.documents.pop(file_id, None)
        for token, file_ids in list(self.inverted_index.items()):
            if file_id in file_ids:
                file_ids.remove(file_id)
            if not file_ids:
                self.inverted_index.pop(token, None)

    def search(self, org_id: str, query: str) -> List[SearchDocument]:
        tokens = self._tokenize_text(query)
        if not tokens:
            return []
        matching: Set[str] | None = None
        for token in tokens:
            ids = self.inverted_index.get(token, set())
            matching = ids if matching is None else matching.intersection(ids)
            if matching is not None and not matching:
                break
        if not matching:
            return []
        results = [self.documents[file_id] for file_id in matching if self.documents[file_id].org_id == org_id]
        return sorted(results, key=lambda doc: doc.updated_at, reverse=True)

    # Internal helpers -----------------------------------------------------

    def _rebuild_tokens(self, file_id: str, doc: SearchDocument) -> None:
        # Remove previous references
        self.remove_file(file_id)
        self.documents[file_id] = doc
        for token in self._tokenize_document(doc):
            self.inverted_index.setdefault(token, set()).add(file_id)

    def _tokenize_document(self, doc: SearchDocument) -> Set[str]:
        tokens: Set[str] = set()
        fields = [doc.name] + doc.labels + doc.owners + doc.principals + [doc.mime_type]
        for field in fields:
            tokens.update(self._tokenize_text(field))
        return tokens

    @staticmethod
    def _tokenize_text(text: str) -> Set[str]:
        if not text:
            return set()
        cleaned = ''.join(ch.lower() if ch.isalnum() else ' ' for ch in text)
        return {token for token in cleaned.split() if token}
