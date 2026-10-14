"""Editable, versioned prompts.

Edit the file and bump `version`; stored results record which version made them.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from hackernews_lite.domain.errors import HnListenerError

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    text: str


def load_prompt(prompts_dir: Path, name: str) -> Prompt:
    path = prompts_dir / f"{name}.md"
    raw = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(raw)
    if match is None:
        raise HnListenerError(
            f"{path} must start with a '---' frontmatter block containing version"
        )
    fields = dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    try:
        version = int(fields["version"].strip())
    except (KeyError, ValueError) as exc:
        raise HnListenerError(f"{path} frontmatter needs an integer 'version'") from exc
    return Prompt(name=name, version=version, text=match.group(2).strip())
