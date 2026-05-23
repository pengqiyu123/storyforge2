from __future__ import annotations

import re
from statistics import mean, pstdev


VAGUE_WORDS = ("似乎", "好像", "大概", "可能", "也许", "应该", "差不多", "左右", "有些", "某种", "一定程度上")
SURPRISE_WORDS = ("竟然", "居然", "想不到", "没想到", "令人", "不禁", "赫然", "顿时", "猛然", "忽然")
SENTENCE_ENDINGS = ("。", "！", "？", "；")
ELLIPSIS = "……"
LEADING_SENTENCE_NOISE = re.compile(r"^[\s\"'“”‘’《》〈〉（）（）\[\]{}<>【】「」『』、，。！？；：:]+")
LETTER_CODE_SPAN = re.compile(r"[A-Za-z][A-Za-z0-9_\-/]*[\u4e00-\u9fff]?")


def split_chinese_sentences(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    sentences: list[str] = []
    buffer: list[str] = []
    index = 0
    length = len(normalized)

    def flush() -> None:
        sentence = "".join(buffer).strip()
        if sentence:
            sentences.append(sentence)
        buffer.clear()

    while index < length:
        if normalized.startswith("\n\n", index):
            flush()
            while index < length and normalized[index] == "\n":
                index += 1
            continue
        if normalized.startswith(ELLIPSIS, index):
            buffer.append(ELLIPSIS)
            index += len(ELLIPSIS)
            flush()
            continue
        char = normalized[index]
        buffer.append(char)
        index += 1
        if char in SENTENCE_ENDINGS:
            flush()
    flush()
    return sentences


def count_chinese_chars(text: str) -> int:
    scrubbed = LETTER_CODE_SPAN.sub("", text)
    return len(re.findall(r"[\u4e00-\u9fff]", scrubbed))


def check_chinese_brackets(text: str) -> dict:
    stack: list[str] = []
    mismatches: list[str] = []
    open_to_close = {"（": "）", "(": ")", "《": "》", "[": "]", "{": "}"}
    close_to_open = {value: key for key, value in open_to_close.items()}
    paired_quotes = {"“": "”", '"': '"', "'": "'"}
    quote_counts = {quote: text.count(quote) for quote in paired_quotes}
    quote_counts.update({"”": text.count("”")})
    if quote_counts.get("“", 0) != quote_counts.get("”", 0):
        mismatches.append("“”")
    if quote_counts.get('"', 0) % 2 != 0:
        mismatches.append('""')
    if quote_counts.get("'", 0) % 2 != 0:
        mismatches.append("''")
    for char in text:
        if char in open_to_close:
            stack.append(char)
        elif char in close_to_open:
            if not stack or stack[-1] != close_to_open[char]:
                mismatches.append(char)
            else:
                stack.pop()
    mismatches.extend(stack)
    return {"balanced": not mismatches, "mismatches": mismatches}


def paragraph_stats(text: str) -> dict:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    lengths = [count_chinese_chars(part) for part in paragraphs]
    if not lengths:
        return {
            "paragraph_count": 0,
            "avg_paragraph_chars": 0.0,
            "min_paragraph_chars": 0,
            "max_paragraph_chars": 0,
            "uniformity": 0.0,
        }
    avg = mean(lengths)
    deviation = pstdev(lengths) if len(lengths) > 1 else 0.0
    return {
        "paragraph_count": len(lengths),
        "avg_paragraph_chars": round(avg, 2),
        "min_paragraph_chars": min(lengths),
        "max_paragraph_chars": max(lengths),
        "uniformity": 0.0 if avg == 0 else round(deviation / avg, 4),
    }


def vague_word_density(text: str) -> float:
    count = sum(text.count(word) for word in VAGUE_WORDS)
    chars = max(1, count_chinese_chars(text))
    return round(count / chars * 1000, 4)


def surprise_word_density(text: str) -> float:
    count = sum(text.count(word) for word in SURPRISE_WORDS)
    chars = max(1, count_chinese_chars(text))
    return round(count / chars * 1000, 4)


def list_sentence_start_repetition(text: str) -> dict:
    sentences = split_chinese_sentences(text)
    starts: list[str] = []
    for sentence in sentences:
        normalized = LEADING_SENTENCE_NOISE.sub("", sentence)
        chars = re.findall(r"[\u4e00-\u9fff]{1,2}", normalized)
        starts.append(chars[0] if chars else normalized[:2])
    current_pattern = ""
    current_count = 0
    best_pattern = ""
    best_count = 0
    for start in starts:
        if start == current_pattern:
            current_count += 1
        else:
            current_pattern = start
            current_count = 1
        if current_count > best_count:
            best_pattern = current_pattern
            best_count = current_count
    return {"detected": best_count >= 3, "pattern": best_pattern, "count": best_count}
