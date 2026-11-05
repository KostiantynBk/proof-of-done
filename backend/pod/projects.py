"""Built-in project registry; only explicitly listed paths enter the snapshot."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DemoProject:
    ref: str
    title: str
    description: str
    root: Path
    include: tuple[str, ...]
    spec_md: str
    spec_pdf: str
    history: dict[str, str]


_REFUNDLY = DemoProject(
    ref="demo:refundly",
    title="Refundly",
    description="A seeded refund service with a policy and synthetic review history.",
    root=Path(__file__).resolve().parents[2] / "samples" / "refundly",
    include=("refundly/", "tests/", "pytest.ini"),
    spec_md="SPEC-v2.md",
    spec_pdf="SPEC-v2.pdf",
    history={"reviews": "history/reviews.json", "git_log": "history/git-log.txt",
             "ci": "history/ci/*.txt"},
)


def get_project(ref: str) -> DemoProject:
    if ref.startswith("github:"):
        raise NotImplementedError("GitHub mode is a stretch goal")
    if ref == _REFUNDLY.ref:
        return _REFUNDLY
    raise ValueError(f"Unknown project reference: {ref}")
