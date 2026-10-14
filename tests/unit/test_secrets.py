from pathlib import Path

import pytest

from hackernews_lite.secrets import read_secret


@pytest.fixture(autouse=True)
def no_ambient_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEMO_KEY", raising=False)


def test_the_environment_wins_over_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "k.env").write_text("DEMO_KEY=from-file\n")
    monkeypatch.setenv("DEMO_KEY", "from-env")
    assert read_secret("DEMO_KEY", tmp_path / "k.env") == "from-env"


def test_falls_back_to_the_env_file(tmp_path: Path) -> None:
    (tmp_path / "k.env").write_text("OTHER=1\nDEMO_KEY=from-file\n")
    assert read_secret("DEMO_KEY", tmp_path / "k.env") == "from-file"


@pytest.mark.parametrize(
    "line",
    ['DEMO_KEY="quoted"', "DEMO_KEY='quoted'", "export DEMO_KEY=quoted", "  DEMO_KEY = quoted  "],
)
def test_understands_common_env_file_formats(tmp_path: Path, line: str) -> None:
    (tmp_path / "k.env").write_text(f"# comment\n{line}\n")
    assert read_secret("DEMO_KEY", tmp_path / "k.env") == "quoted"


def test_missing_everywhere_is_none_not_an_error(tmp_path: Path) -> None:
    assert read_secret("DEMO_KEY") is None
    assert read_secret("DEMO_KEY", tmp_path / "does-not-exist.env") is None
    (tmp_path / "k.env").write_text("DEMO_KEY=\n")
    assert read_secret("DEMO_KEY", tmp_path / "k.env") is None


def test_a_tilde_path_is_expanded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "k.env").write_text("DEMO_KEY=home-key\n")
    assert read_secret("DEMO_KEY", Path("~/k.env")) == "home-key"
