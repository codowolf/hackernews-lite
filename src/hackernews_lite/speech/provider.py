"""The text-to-speech seam. Add a vendor by writing one class that satisfies this."""

from typing import Protocol


class TtsProvider(Protocol):
    name: str

    def voices(self) -> list[str]:
        """Voice ids worth auditioning."""
        ...

    def synthesize(self, text: str, voice: str) -> bytes:
        """Return MP3 bytes for `text`."""
        ...

    def cost_usd(self, characters: int) -> float:
        """Estimated price for speaking this many characters."""
        ...
