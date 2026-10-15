"""Keep source code opaque to sentence splitting and neural translation."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ProtectedSpan:
    start: int
    end: int
    block: bool = False
    complete: bool = True


FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
CODE_LINE = re.compile(
    r"^[ \t]*(?:"
    r"#(?:Include|Requires|SingleInstance|NoEnv|InstallKeybdHook|Persistent)\b|"
    r"(?:SendInput|SendEvent|Send|FileInstall|SetTimer|ToolTip|RunWait|Run|KeyHistory)\s*,|"
    r"(?:if|while|for)\s*\(|"
    r"(?:global|local|static)(?:[ \t]+[A-Za-z_]\w*(?:[ \t]*,[ \t]*[A-Za-z_]\w*)*)?[ \t]*(?:;[^\r\n]*)?$|"
    r"return(?:[ \t]+(?:[A-Za-z_]\w*|-?\d+(?:\.\d+)?))?[ \t]*(?:;[^\r\n]*)?$|else\s*(?:\{|$)|"
    r"[{}]\s*$|[A-Za-z_]\w*\s*(?::=|=(?!=))|"
    r"(?:powershell(?:\.exe)?|pwsh|cmd\.exe|python[\d.]*(?:\.exe)?|"
    r"AutoHotkey\w*\.exe|git|curl|pip)\s+|\$\s+)", re.IGNORECASE)
CALL = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*[ \t]*\(")
WORD = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*(?![A-Za-z0-9_])")
LITERAL = re.compile(
    r"https?://[^\s<>\"'`]+|"
    r"(?:[A-Za-z]:[\\/]|(?:\.{1,2}[\\/])|(?:[A-Za-z0-9_.-]+[\\/]))[^\s<>\"'`]+|"
    r"(?<![A-Za-z0-9_])[A-Za-z0-9_.-]+\.(?:ahk|exe|dll|ps1|py|json|cpp|hpp|"
    r"cs|bat|cmd|ini|yaml|yml|so|pyd|xml|html|js|ts|go|rs|sh|log|bin|gguf|txt|md|c|h)\b|"
    r"(?<!\w)0x[0-9a-f]+\b|(?<!\w)(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?!\w)|"
    r"(?<!\w)--?[A-Za-z][A-Za-z0-9_-]*(?:=[^\s\"'`]+)?|(?<!\w)/[A-Z][A-Z0-9]+\b|"
    r"\b(?:[A-Za-z0-9]+\+)+[A-Za-z0-9]+\b|"
    r"</?[A-Za-z][A-Za-z0-9_:-]*(?:\s+[^<>\r\n]*?)?/?>|#[A-Za-z_]\w*|\.NET\b",
    re.IGNORECASE)
QUOTE_CONTEXT = re.compile(
    r"\b(?:service|process|function|method|class|label|key|named|called|literal|"
    r"parameter|argument|value|string|title)\s*$", re.IGNORECASE)
MARKER = re.compile(r"(?<![A-Za-z0-9_])A[ \t]*B[ \t]*C[ \t]*(\d+)(?![A-Za-z0-9_])", re.IGNORECASE)


def _identifier(word):
    return ("_" in word or any(c.isdigit() for c in word)
            or bool(re.search(r"[a-z][A-Z]|[A-Z]{2}[a-z]", word))
            or (len(word) >= 2 and word.isupper())
            or word in {"Interception"})


def _code_quote(body, before):
    body = body.strip()
    return bool(body and (
        re.search(r":=|[{}\\]|^#|\b(?:SendInput|FileInstall)\s*,", body)
        or CALL.search(body) or LITERAL.fullmatch(body) or CODE_LINE.match(body)
        or re.fullmatch(r"new\s+[A-Za-z_]\w*", body)
        or (WORD.fullmatch(body) and (_identifier(body) or QUOTE_CONTEXT.search(before)
                                     or body in {"global", "return", "true", "false"}))))


def protected_spans(text: str, final: bool = True) -> list[ProtectedSpan]:
    """Find literal regions. Unfinished streamed code stays protected until closed.

    Block boundaries are also safe segment boundaries. Priority is fences, code
    lines, inline code/quotes, calls and finally individual technical identifiers.
    """
    spans = []

    def covered(start, end):
        return any(start < span.end and end > span.start for span in spans)

    def add(start, end, block=False, complete=True):
        if end <= start:
            return
        overlaps = [span for span in spans if start < span.end and end > span.start]
        if overlaps:
            if any(start > span.start or end < span.end for span in overlaps):
                return
            # A multiline inline span or call can contain code lines/quotes, but
            # Markdown fenced blocks take precedence over inline parsing.
            if any(span.block and FENCE.match(text[span.start:span.end]) for span in overlaps):
                return
            spans[:] = [span for span in spans if span not in overlaps]
        spans.append(ProtectedSpan(start, end, block, complete))

    offset, opening = 0, None
    lines = text.splitlines(keepends=True)
    for line in lines:
        if opening is not None:
            start, char, width = opening
            closing = re.fullmatch(r"[ \t]{0,3}" + re.escape(char) + r"{" + str(width) + r",}[ \t]*", line.rstrip("\r\n"))
            if closing and (line.endswith(("\n", "\r")) or final):
                add(start, offset + len(line), block=True)
                opening = None
        else:
            match = FENCE.match(line)
            if match:
                opening = (offset, match[1][0], len(match[1]))
        offset += len(line)
    if opening is not None:
        add(opening[0], len(text), block=True, complete=False)

    offset = 0
    for line in lines:
        if (line.startswith(("    ", "\t")) and line.strip()) or CODE_LINE.match(line):
            add(offset, offset + len(line), block=True,
                complete=line.endswith(("\n", "\r")) or final)
        offset += len(line)

    # Exact backtick-run lengths support both `code` and ``code with ` inside``.
    pos = 0
    while pos < len(text):
        match = re.search(r"`+", text[pos:])
        if match is None:
            break
        start, end = pos + match.start(), pos + match.end()
        if covered(start, end):
            pos = end
            continue
        closing = re.search(r"(?<!`)" + re.escape(text[start:end]) + r"(?!`)", text[end:])
        finish = end + closing.end() if closing else len(text)
        add(start, finish, complete=closing is not None)
        pos = finish

    quotes = {"'": "'", '"': '"', "‘": "’", "“": "”"}
    pos = 0
    while pos < len(text):
        char = text[pos]
        if char not in quotes or covered(pos, pos + 1) or (pos and text[pos - 1].isalnum()):
            pos += 1
            continue
        end = pos + 1
        while end < len(text):
            if text[end] == quotes[char] and text[end - 1] != "\\":
                break
            end += 1
        closed = end < len(text)
        if _code_quote(text[pos + 1:end], text[max(0, pos - 40):pos]):
            add(pos, end + 1 if closed else end, complete=closed)
            pos = end + 1
        else:
            pos += 1

    for match in CALL.finditer(text):
        if covered(*match.span()):
            continue
        name = match[0].rstrip()[:-1].rstrip()
        # A space before '(' usually introduces an English aside, not a call.
        if match[0][-2:-1].isspace() and not (_identifier(name) or "." in name):
            continue
        end, depth, quote = match.end(), 1, None
        while end < len(text) and depth:
            char = text[end]
            if quote:
                if char == quote and text[end - 1] != "\\":
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            end += 1
        add(match.start(), end, complete=depth == 0)

    for match in LITERAL.finditer(text):
        end = match.end()
        if match[0].startswith(("http", "./", "../")) or "\\" in match[0] or "/" in match[0]:
            while end > match.start() and text[end - 1] in ".,;:!?)]}":
                end -= 1
        add(match.start(), end)
    for match in WORD.finditer(text):
        if _identifier(match[0]):
            add(*match.span())
    return sorted(spans, key=lambda span: span.start)


@dataclass(frozen=True)
class ProtectedText:
    masked: str
    prose: str
    literals: tuple[str, ...]

    def restore(self, translated: str) -> str | None:
        """Restore by ID, allowing Chinese word order; reject lost/extra copies."""
        if not self.literals:
            return translated
        matches = list(MARKER.finditer(translated))
        if sorted(int(match[1]) for match in matches) != list(range(len(self.literals))):
            return None
        return MARKER.sub(lambda match: self.literals[int(match[1])], translated)


def protect_text(text: str) -> ProtectedText:
    masked, prose, literals, start = [], [], [], 0
    for span in protected_spans(text):
        prefix = text[start:span.start]
        masked.extend((prefix, f"ABC{len(literals)}"))
        prose.append(prefix)
        literals.append(text[span.start:span.end])
        start = span.end
    masked.append(text[start:])
    prose.append(text[start:])
    return ProtectedText("".join(masked), " ".join(prose), tuple(literals))


def has_english_prose(text: str) -> bool:
    prose = protect_text(text).prose
    return bool(re.search(r"[A-Za-z]{2,}", prose)) and not re.search(r"[\u3400-\u9fff]", prose)
