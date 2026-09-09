from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class Utterance:
    text: str


@dataclass(frozen=True)
class SpokenReply:
    speak: str


def parse_utterance(value: object) -> Utterance:
    if not isinstance(value, dict) or not isinstance(value.get("text"), str):
        raise ValueError("text には文字列を指定してください。")
    text = value["text"].strip()
    if not text:
        raise ValueError("text を空にはできません。")
    return Utterance(text=text)


def clamp_reply(reply: str, max_length: int) -> str:
    cleaned = "".join(_clean_character(character) for character in reply)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""

    sentence_end = re.search(r"[。！？.!?]", cleaned)
    if sentence_end:
        cleaned = cleaned[: sentence_end.end()]
    return cleaned[:max_length].rstrip()


def _clean_character(character: str) -> str:
    if character in "\r\n\t":
        return " "
    if unicodedata.category(character).startswith("C"):
        return ""
    return character
