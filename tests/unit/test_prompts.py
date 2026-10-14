from pathlib import Path

import pytest

from hackernews_lite.domain.errors import HnListenerError
from hackernews_lite.prep.prompts import load_prompt


def test_load_prompt_reads_version_and_body(tmp_path: Path) -> None:
    (tmp_path / "demo.md").write_text("---\nversion: 3\n---\nBe brief.\n")
    prompt = load_prompt(tmp_path, "demo")
    assert (prompt.name, prompt.version, prompt.text) == ("demo", 3, "Be brief.")


def test_load_prompt_requires_frontmatter(tmp_path: Path) -> None:
    (tmp_path / "demo.md").write_text("no frontmatter")
    with pytest.raises(HnListenerError):
        load_prompt(tmp_path, "demo")


def test_shipped_prompts_load() -> None:
    root = Path(__file__).resolve().parents[2] / "prompts"
    for name in ("article_prep", "comment_curation"):
        assert load_prompt(root, name).version >= 1
