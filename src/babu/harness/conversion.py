"""Optional conversion of model replies to another Chinese script."""

import re
from typing import Literal

import opencc  # type: ignore[import-untyped]

Conversion = Literal["s2t", "s2tw", "s2hk"]

# Sentences end at CJK or ASCII terminators and line breaks.
_SENTENCE = re.compile(r"[^。！？!?\n]*[。！？!?\n]?")


def _traditional(character: str) -> bool:
    try:
        character.encode("cp950")
    except UnicodeEncodeError:
        return False
    return True


class ScriptConverter:
    """Convert sentences that contain Simplified-only characters; leave other sentences untouched.

    Characters valid in both scripts, such as 台, 范 and 后, change only inside a sentence that
    also contains a character Big5 lacks, so correct Traditional text keeps its own choices.
    """

    def __init__(self, conversion: Conversion) -> None:
        self._converter = opencc.OpenCC(conversion)

    def convert(self, text: str) -> str:
        return "".join(self._sentence(match.group()) for match in _SENTENCE.finditer(text))

    def _sentence(self, sentence: str) -> str:
        if all(_traditional(character) for character in sentence if "㐀" <= character <= "鿿"):
            return sentence
        return str(self._converter.convert(sentence))
