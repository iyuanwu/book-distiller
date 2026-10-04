"""One bounded copy per referenced Citation, derived from canonical char ranges."""
from book_distiller.evidence.citations import SourceIndex, resolve_citation
from book_distiller.core.models.verification import Citation


def build(document, citations, rules):
    index = SourceIndex(document.blocks_path)
    entries, used = {}, 0
    # Primary citation ranges take priority over optional surrounding context.
    for row in sorted(citations, key=lambda c: c['citation_id']):
        citation = Citation.model_validate(row)
        resolved = resolve_citation(document, citation, index)
        block = index.get(citation.block_id)
        allowance = max(0, min(rules['excerpt_chars'], rules['max_evidence_chars']-used))
        excerpt = resolved['text'][:allowance]
        used += len(excerpt)
        entries[citation.citation_id] = dict(row, excerpt=excerpt, excerpt_chars=len(excerpt),
            range_chars=len(resolved['text']), excerpt_truncated=len(excerpt) < len(resolved['text']),
            budget_omitted=not excerpt and bool(resolved['text']), chapter_id=block.chapter_id,
            section_id=block.section_id, context=[], derived_from='canonical source / char range')
    for entry in entries.values():
        for bid in entry['context_block_ids']:
            b = index.get(bid)
            n = max(0, min(rules['context_chars'], rules['max_evidence_chars']-used))
            if n:
                entry['context'].append({'block_id': bid, 'text': b.text[:n], 'truncated': len(b.text) > n})
                used += min(n, len(b.text))
    return {'entries': entries, 'embedded_chars': used, 'max_embedded_chars': rules['max_evidence_chars']}
