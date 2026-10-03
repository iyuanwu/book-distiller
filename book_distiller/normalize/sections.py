"""Deterministic section stack; no semantic inference."""
from book_distiller.core.models.normalized import NormalizedChapter, NormalizedSection


def add_section(chapter: NormalizedChapter, stack: list[NormalizedSection], title: str,
                level: int, block_id: str) -> NormalizedSection:
    """Attach a heading to the nearest preceding lower-level section."""
    while stack and stack[-1].heading_level >= level:
        stack.pop()
    section = NormalizedSection(section_id=f"sec_{chapter.chapter_id[3:]}_{len(chapter.sections)+1:04d}",
        chapter_id=chapter.chapter_id, title=title, heading_level=level,
        parent_section_id=stack[-1].section_id if stack else None, start_block_id=block_id)
    chapter.sections.append(section)
    stack.append(section)
    return section
