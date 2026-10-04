"""Local versioned workflow registry. No LLM client or network operations."""
from dataclasses import dataclass
from pathlib import Path
import re
import hashlib
from book_distiller.core.errors import ValidationError
from book_distiller.core.models.ai_tasks import BookClassification
from book_distiller.core.models.synthesis import BOOK_WORKFLOWS, RESULT_MODELS


@dataclass(frozen=True)
class Workflow:
    name: str
    task_type: str
    workflow_path: Path
    prompt_path: Path
    workflow_version: str
    prompt_version: str

    overlay_paths: tuple[Path, ...] = ()
    primary_type: str | None = None

    def prompt_text(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8") + ''.join(
            '\n\n' + ('Primary type emphasis\n' if path.parent.name == self.primary_type else 'Secondary type hint (subordinate)\n') + path.read_text(encoding="utf-8") for path in self.overlay_paths)

    def prompt_hash(self) -> str:
        return hashlib.sha256(self.prompt_text().encode('utf-8')).hexdigest()

    def result_model(self):
        if self.name in RESULT_MODELS:
            return RESULT_MODELS[self.name]
        if self.name == 'classify':
            return BookClassification
        from book_distiller.core.models.knowledge import ClaimResult, AtomResult
        return ClaimResult if self.name == 'extract_claims' else AtomResult

    def output_schema(self) -> dict:
        """Generate the output contract from its authoritative Pydantic model."""
        return self.result_model().model_json_schema()


def load_workflow(project: Path, name: str, types: list[str] | None = None) -> Workflow:
    """Resolve only registered local workflows; arbitrary paths are not workflows."""
    if name not in {"classify", "extract_claims", "build_chapter_atoms"} | BOOK_WORKFLOWS:
        raise ValidationError(f"Unknown workflow: {name}. Unsupported workflow.")
    workflow = project / f"workflows/{name}.md"
    prompt = project / f"prompts/universal/{name}.md"
    def read_version(path: Path, key: str) -> str:
        text = path.read_text(encoding="utf-8")
        match = re.search(r"<!-- " + key + r": ([a-z0-9.-]+) -->", text)
        if not match:
            raise ValidationError(f"Missing {key} in {path}")
        return match.group(1)
    overlays = tuple(project / f"prompts/{kind}/{('synthesis' if name in BOOK_WORKFLOWS else name)}.md" for kind in dict.fromkeys(types or [])
                     if kind in {'investment','philosophy','business'} and name != 'classify')
    return Workflow(name, 'classify_book' if name == 'classify' else name, workflow, prompt,
                    read_version(workflow, 'workflow_version'), read_version(prompt, 'prompt_version'), overlays, (types or [None])[0])
