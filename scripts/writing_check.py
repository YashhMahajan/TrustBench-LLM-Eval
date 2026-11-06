"""Check the project text against the writing rules that a program can check.

The script checks these rules:
  - Sentence length. An instruction has up to 20 words. Other sentences have up to 25 words.
  - Passive voice. It looks for a form of "be" with a past participle.
  - Contractions of a verb with "not", "is", "will", "are" or "have".
  - Latin abbreviations and semicolons. Both join two ideas in one sentence.
  - Paragraphs with more than 6 sentences.
The script does not check the choice of words or the meaning of the text.
If it finds no problem, the text follows every rule that a program can check.

To use it, run `python scripts/writing_check.py` with the files to check. Or run `make writing`.
The exit code is 1 when the text breaks a rule.
It reads Markdown files. It also reads the docstrings, comments and `help=` text of Python files.
"""

import ast
import io
import re
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

PROCEDURE_MAX, DESCRIPTION_MAX, PARAGRAPH_MAX = 20, 25, 6
IMPERATIVES = {
    "add", "ask", "change", "check", "choose", "click", "close", "copy", "create", "delete", "edit", "enable", "ensure", "find", "give",
    "install", "keep", "make", "name", "open", "pass", "put", "read", "record", "remove", "replace", "report", "run", "save", "see",
    "send", "set", "start", "stop", "take", "tell", "try", "turn", "type", "update", "use", "wait", "write", "do", "never", "always", "mark",
}
PARTICIPLE = (r"(?:\w+ed|made|done|run|set|kept|shown|written|built|given|found|seen|stored|used|known|taken|sent|read|left|put|"
              r"chosen|hidden|broken|spoken|driven|gone|held|told|bought|brought|caught|taught|thought|begun|born|cut|hit|let|lost)")
PASSIVE = re.compile(rf"\b(?:is|are|was|were|be|been|being)\s+(?:not\s+|never\s+|also\s+|only\s+|always\s+|then\s+)?{PARTICIPLE}\b", re.I)
PASSIVE_IGNORE = re.compile(r"\b(?:is|are|was|were|be|been)\s+(?:not\s+)?(?:used to|supposed to|allowed to|going to|required to|based on|related to|set to|"
                            r"empty|closed|open|ready|done|red|fixed|sure|enabled|disabled|off|on)\b", re.I)
CONTRACTION = re.compile(r"\b(?:\w+n't|it's|that's|there's|here's|what's|let's|who's|he's|she's|we'll|you'll|they'll|it'll|we're|you're|they're|"
                         r"we've|you've|they've|i'm|i've|i'll|i'd|you'd|we'd|they'd)\b", re.I)
LATIN = re.compile(r"\b(?:e\.g\.|i\.e\.|etc\.|et al\.|vs\.|via)(?=\W|$)", re.I)
WORD = re.compile(r"[A-Za-z0-9$%][A-Za-z0-9$%'_./+-]*")


@dataclass
class Problem:
    file: str
    line: int
    rule: str
    text: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.rule}: {self.text[:140]}"


def clean(text: str) -> str:
    """Remove what is not prose: inline code, links, images, HTML, emphasis marks."""
    text = re.sub(r"`[^`]*`", "X", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[*_]{1,2}([^*_]+)[*_]{1,2}", r"\1", text).strip()


def sentences(block: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])", clean(block))
    return [p.strip() for p in parts if re.search(r"[A-Za-z]{2}", p)]


def is_procedure(sentence: str, step: bool) -> bool:
    first = (WORD.findall(sentence) or [""])[0].lower()
    return step or first in IMPERATIVES


def check_sentence(s: str, step: bool = False) -> list[tuple[str, str]]:
    out = []
    n = len(WORD.findall(s))
    limit = PROCEDURE_MAX if is_procedure(s, step) else DESCRIPTION_MAX
    if n > limit:
        out.append((f"{'procedure' if limit == PROCEDURE_MAX else 'description'} sentence has {n} words (maximum {limit})", s))
    if PASSIVE.search(s) and not PASSIVE_IGNORE.search(s):
        out.append(("passive voice: name who does the action", s))
    if CONTRACTION.search(s):
        out.append(("contraction: write the full words", s))
    if LATIN.search(s):
        out.append(("Latin abbreviation: write 'for example', 'that is' or 'and so on'", s))
    if ";" in s:
        out.append(("semicolon: write two sentences", s))
    return out


def check_markdown(path: Path) -> list[Problem]:
    problems, in_code, para, para_line = [], False, [], 0
    lines = path.read_text().splitlines()

    def flush():
        nonlocal para
        if para:
            sents = sentences(" ".join(para))
            if len(sents) > PARAGRAPH_MAX:
                problems.append(Problem(str(path), para_line, f"paragraph has {len(sents)} sentences (maximum {PARAGRAPH_MAX})", sents[0]))
        para = []

    for i, raw in enumerate(lines, 1):
        line = raw.rstrip()
        if line.strip().startswith("```"):
            flush()
            in_code = not in_code
            continue
        if in_code or not line.strip() or line.startswith("    ") and not re.match(r"\s*([-*]|\d+\.)\s", line):
            flush()
            continue
        if re.match(r"\s*\|?\s*:?-{3,}", line) or line.startswith("<!--"):
            continue
        step = bool(re.match(r"\s*\d+[.)]\s", line))
        text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+|^#+\s*|^>\s*", "", line)
        cells = [c for c in text.strip("|").split("|")] if line.lstrip().startswith("|") else [text]
        is_prose = not line.lstrip().startswith(("|", "#"))
        for cell in cells:
            for s in sentences(cell):
                for rule, bad in check_sentence(s, step):
                    problems.append(Problem(str(path), i, rule, bad))
        if is_prose and not re.match(r"\s*([-*+]|\d+[.)])\s", line):
            if not para:
                para_line = i
            para.append(text)
        else:
            flush()
    flush()
    return problems


def python_text(path: Path) -> list[tuple[int, str]]:
    """(line, text) for the prose in a Python file: docstrings, comments, and the `help=` text of command options."""
    source = path.read_text()
    out: list[tuple[int, str]] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=True)
            if doc and node.body and isinstance(node.body[0], ast.Expr):
                out.append((node.body[0].lineno, doc))
        if isinstance(node, ast.keyword) and node.arg == "help" and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            out.append((node.value.lineno, node.value.value))
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            text = tok.string.lstrip("# ").strip()
            if not re.match(r"(noqa|type:|pragma|!|-\*-|S\d+|fmt:)", text) and len(text.split()) > 2:
                out.append((tok.start[0], text))
    return out


def check_python(path: Path) -> list[Problem]:
    problems = []
    for line, text in python_text(path):
        for block in re.split(r"\n\s*\n", text):
            lines = [ln for ln in block.splitlines() if ln.strip() and not re.match(
                r"\s*(?:>>>|\.\.\.|\$|[A-Za-z_]+\(.*\)\s*$|from |import |[a-z_]+ *[:=] |\{|\[|[A-Za-z_.]+ *[-=]> )", ln)]
            aligned = any(re.search(r"\S {3,}\S", ln) for ln in lines)       # Columns lined up with spaces: each line is its own item.
            units = lines if aligned else []
            if not aligned:                                    # A list item is its own unit. Other lines continue the unit before them.
                for ln in lines:
                    if re.match(r"\s*(?:[-*]|\d+[.)])\s+", ln) or not units:
                        units.append(ln)
                    else:
                        units[-1] += " " + ln.strip()
            for unit in units:
                unit = re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", unit)
                if aligned:
                    unit = re.sub(r"\s{3,}", ": ", unit.strip(), count=1)
                for sent in sentences(unit):
                    for rule, bad in check_sentence(sent):
                        problems.append(Problem(str(path), line, rule, bad))
    return problems


def check_file(path: Path) -> list[Problem]:
    return check_python(path) if path.suffix == ".py" else check_markdown(path)


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv] or [Path("README.md")]
    problems = [p for f in files for p in check_file(f)]
    for p in problems:
        print(p)
    print(f"\n{len(problems)} problem(s) in {len(files)} file(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
