from dataclasses import dataclass
from typing import Protocol

from .domain import SpokenReply, Utterance


class StackChanListener(Protocol):
    def receive(self) -> Utterance | None:
        ...


class StackChanSpeaker(Protocol):
    def speak(self, reply: SpokenReply) -> None:
        ...


@dataclass
class MockStackChanListener:
    text: str
    _received: bool = False

    def receive(self) -> Utterance | None:
        if self._received:
            return None
        self._received = True
        return Utterance(text=self.text)


class MockStackChanSpeaker:
    def speak(self, reply: SpokenReply) -> None:
        if reply.speak:
            print(f"SPOKEN: {reply.speak}")
