"""ウェイクワード判定。文字起こしの先頭に呼びかけがあるかを見ます。"""

from dataclasses import dataclass
import re
import unicodedata

DEFAULT_WAKE_WORDS = ("スタックちゃん", "すたっくちゃん", "スタックチャン")
MAX_LEADING_CHARACTERS = 3

_ARTIFACT = re.compile(r"^[\[\(（【].*[\]\)）】]$")


def normalize(text: str) -> str:
    return "".join(character for character, _ in _normalized_pairs(text))


def clean_transcript(text: str) -> str:
    """whisper の「[BLANK_AUDIO]」「(音楽)」のような注記だけの出力は空にします。"""
    stripped = text.strip()
    if not stripped or _ARTIFACT.match(stripped):
        return ""
    return stripped


@dataclass(frozen=True)
class WakeMatch:
    matched: bool
    remainder: str


@dataclass(frozen=True)
class WakeWordMatcher:
    words: tuple[str, ...] = DEFAULT_WAKE_WORDS

    def match(self, transcript: str) -> WakeMatch:
        pairs = _normalized_pairs(transcript)
        normalized = "".join(character for character, _ in pairs)
        best: tuple[int, int] | None = None
        for word in self.words:
            target = normalize(word)
            if not target:
                continue
            index = normalized.find(target)
            if index < 0 or index > MAX_LEADING_CHARACTERS:
                continue
            end = index + len(target)
            if best is None or index < best[0] or (index == best[0] and end > best[1]):
                best = (index, end)
        if best is None:
            return WakeMatch(matched=False, remainder="")
        if best[1] >= len(pairs):
            return WakeMatch(matched=True, remainder="")
        original_start = pairs[best[1]][1]
        remainder = transcript[original_start:]
        return WakeMatch(matched=True, remainder=_strip_leading_punctuation(remainder))


def _normalized_pairs(text: str) -> list[tuple[str, int]]:
    pairs: list[tuple[str, int]] = []
    for index, character in enumerate(text):
        folded = unicodedata.normalize("NFKC", character).lower()
        for piece in folded:
            code = ord(piece)
            if 0x30A1 <= code <= 0x30F6:
                piece = chr(code - 0x60)
            category = unicodedata.category(piece)
            if category[0] in "LN" or piece == "ー":
                pairs.append((piece, index))
    return pairs


def _strip_leading_punctuation(text: str) -> str:
    result = text
    while result and (
        result[0].isspace() or unicodedata.category(result[0])[0] in "PSZ"
    ):
        result = result[1:]
    return result.strip()
