"""Local versioned workflow registry. No LLM client or network operations."""
from dataclasses import dataclass
from pathlib import Path
import re
from book_distiller.core.errors import ValidationError
from book_distiller.core.models.ai_tasks import BookClassification


@dataclass(frozen=True)
class Workflow:
    name: str
    task_type: str
    workflow_path: Path
    prompt_path: Path
    workflow_version: str
    prompt_version: str

    def output_schema(self) -> dict:
        """Generate the output contract from its authoritative Pydantic model."""
        return BookClassification.model_json_schema()


def load_workflow(project: Path, name: str) -> Workflow:
    """Resolve only registered local workflows; arbitrary paths are not workflows."""
    if name != "classify":
        raise ValidationError(f"Unknown workflow: {name}. Phase 3 supports classify only.")
    workflow = project / "workflows/classify.md"
    prompt = project / "prompts/universal/classify.md"
    def read_version(path: Path, key: str) -> str:
        text = path.read_text(encoding="utf-8")
        match = re.search(r"<!-- " + key + r": ([a-z0-9.-]+) -->", text)
        if not match:
            raise ValidationError(f"Missing {key} in {path}")
        return match.group(1)
    return Workflow(name, "classify_book", workflow, prompt,
                    read_version(workflow, "workflow_version"), read_version(prompt, "prompt_version"))
