from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

DEFAULT_WORDS = ("хуй", "хуя", "хуи", "хуйня", "нахуй", "похуй", "блядь", "блять", "сука", "суки", "ебать", "ебаный", "ебаная", "пизда", "пиздец", "пиздеть", "долбоеб", "мудак", "ублюдок", "йобаний", "їбать", "їбати", "пізда", "пиздюк", "лайно", "fuck", "fucking", "fucked", "shit", "bitch", "asshole", "bastard", "cunt", "motherfucker")
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
_LATIN = str.maketrans("аеорсухі", "aeopcyxi")
_CYRILLIC = str.maketrans("aeopcyxi", "аеорсухі")


def normalize(text: str) -> str:
    clean = "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if unicodedata.category(c) != "Cf")
    return " ".join(clean.translate(_LEET).replace("ё", "е").split())


def variants(text: str) -> list[str]:
    text = normalize(text)
    return [text, text.translate(_LATIN), text.translate(_CYRILLIC)]


@dataclass(frozen=True)
class ModerationResult:
    level: str
    reasons: list[str]


def moderate_text(text: str, words: list[str]) -> ModerationResult:
    forms = variants(text)
    # Join isolated letters in "s.h.i.t" / "х у й", without joining ordinary words.
    forms += [re.sub(r"(?<!\w)(?:\w[\s.\-_*]+){2,}\w(?!\w)", lambda match: re.sub(r"[\s.\-_*]+", "", match[0]), form) for form in forms]
    for phrase in words:
        for pattern in variants(phrase):
            expression = r"(?<!\w)" + re.escape(pattern).replace(r"\ ", r"\s+") + r"(?!\w)"
            if any(re.search(expression, form) for form in forms):
                return ModerationResult("forbidden", ["profanity:" + normalize(phrase)])
    masked = [token for form in forms for token in re.findall(r"(?<!\w)[\w*!#]{3,}(?!\w)", form) if any(char in token for char in "*!#")]
    for token in masked:
        expression = "".join(r"\w{0,2}" if char in "*!#" else re.escape(char) for char in token)
        if any(re.fullmatch(expression, form) for word in words for form in variants(word)):
            return ModerationResult("forbidden", ["masked_profanity"])
    reasons = []
    clean = normalize(text)
    if re.search(r"https?://|www\.|t\.me/|@[\w]{3,}|\b[\w-]+\.(?:com|net|org|ru|ua|xyz|io)\b", unicodedata.normalize("NFKC", text).casefold()):
        reasons.append("link")
    if re.search(r"(.)\1{6,}", clean):
        reasons.append("repetition")
    tokens = re.findall(r"\w+", clean)
    if len(tokens) >= 6 and max((tokens.count(token) for token in set(tokens)), default=0) / len(tokens) >= .7:
        reasons.append("word_spam")
    if re.search(r"\b(?:заработок|заробіток|реклама|купить|купити|скидка|промокод|casino)\b|earn money|buy now", clean):
        reasons.append("advertising")
    return ModerationResult("suspicious" if reasons else "safe", reasons)
