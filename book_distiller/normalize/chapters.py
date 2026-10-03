"""Heading-only structural grouping, including explicit synthetic chapters."""
from book_distiller.core.models.normalized import NormalizedChapter, NormalizedSection
from book_distiller.normalize.sections import add_section


class ChapterBuilder:
    def __init__(self, top_level: int | None):
        self.top_level = top_level
        self.chapters: list[NormalizedChapter] = []
        self.stack: list[NormalizedSection] = []

    def locate(self, title: str, heading_level: int | None, block_id: str) -> tuple[str, str | None]:
        """Highest effective heading starts chapters; deeper ones start sections."""
        if heading_level is not None and heading_level == self.top_level:
            self.chapters.append(NormalizedChapter(chapter_id=f"ch_{len(self.chapters)+1:04d}",
                title=title, heading_level=heading_level, start_block_id=block_id))
            self.stack.clear()
        elif not self.chapters:
            self.chapters.append(NormalizedChapter(chapter_id="ch_0001", title="Document",
                synthetic=True, start_block_id=block_id))
        chapter = self.chapters[-1]
        if heading_level is not None and heading_level != self.top_level:
            add_section(chapter, self.stack, title, heading_level, block_id)
        return chapter.chapter_id, self.stack[-1].section_id if self.stack else None
