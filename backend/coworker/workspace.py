from pathlib import Path


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def resolve_path(self, file_path: str) -> Path:
        candidate = (self.root / file_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError(f"Access denied: {file_path} is outside the workspace")
        return candidate

    def read_text(self, file_path: str) -> str:
        return self.resolve_path(file_path).read_text(encoding="utf-8")

    def write_text(self, file_path: str, content: str) -> None:
        target = self.resolve_path(file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
