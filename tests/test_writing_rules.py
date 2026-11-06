"""All project text follows the writing rules that a program can check (see scripts/writing_check.py).

Two jobs: (1) the project text passes, (2) the checker still catches each kind of broken rule.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import writing_check as wc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def tracked(*patterns: str) -> list[Path]:
    out = subprocess.run(["git", "ls-files", *patterns], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()  # noqa: S603, S607
    return [ROOT / p for p in out]


def test_the_documents_follow_the_writing_rules():
    problems = [p for f in tracked("README.md", "MEMORY.md", "docs/*.md", "examples/*/README.md") for p in wc.check_markdown(f)]
    assert not problems, "\n" + "\n".join(str(p) for p in problems[:15])


def test_the_comments_docstrings_and_help_texts_follow_the_writing_rules():
    problems = [p for f in tracked("*.py") for p in wc.check_python(f)]
    assert not problems, "\n" + "\n".join(str(p) for p in problems[:15])


# ---- The checker itself -----------------------------------------------------------------------------

def rules(text: str, step: bool = False) -> list[str]:
    return [r for s in wc.sentences(text) for r, _ in wc.check_sentence(s, step)]


@pytest.mark.parametrize("text,needle", [
    ("The report is written by the engine.", "passive"),
    ("The files were deleted.", "passive"),
    ("It doesn't work.", "contraction"),
    ("We can't read it.", "contraction"),
    ("Use a tool, e.g. a script.", "Latin"),
    ("Run the test, i.e. the full suite.", "Latin"),
    ("Run the test; read the result.", "semicolon"),
    ("This sentence has many words that go on and on without any good reason to stop, so a reader gets lost in it long before the end.", "description sentence"),
    ("Open the file and read the first line of the report that the tool wrote and then check that every value in it agrees.", "procedure sentence"),
])
def test_the_checker_catches_each_kind_of_broken_rule(text, needle):
    assert any(needle in r for r in rules(text)), rules(text)


@pytest.mark.parametrize("text", [
    "The engine writes the report.",
    "Run the tests.",
    "The code keeps the result.",
    "It is empty.",                       # "is" with an adjective is not a passive voice
    "The test is ready.",
    "Use the `trustbench run` command.",
    "TrustBench stores answers, so a rerun costs nothing.",
])
def test_the_checker_accepts_good_text(text):
    assert rules(text) == [], rules(text)


def test_an_instruction_may_have_20_words_and_a_description_25():
    twenty = " ".join(["word"] * 20)
    assert rules(f"Run {twenty[5:]}.") == []                      # 20 words, starts with an instruction
    assert any("procedure" in r for r in rules(f"Run {twenty}."))  # 21 words
    twenty_five = " ".join(["word"] * 25)
    assert rules(f"The {twenty_five[5:]}.") == []
    assert any("description" in r for r in rules(f"The {twenty_five}."))


def test_a_step_in_a_numbered_list_is_an_instruction():
    sentence = "The reader opens the file and then reads the first line of the report that the tool wrote for them today."
    assert not any("procedure" in r for r in rules(sentence))     # 21 words as a description: fine
    assert any("procedure" in r for r in rules(sentence, step=True))


def test_code_blocks_inline_code_and_links_are_not_prose(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("# Title\n\n```\nThe file was written by the tool; it doesn't matter, e.g. here.\n```\n\n"
                   "Use `a value that is stored; e.g. this` and [a link](http://x.example).\n")
    assert wc.check_markdown(doc) == []


def test_a_paragraph_of_more_than_six_sentences_is_flagged(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text(" ".join(f"Sentence {n} is short." for n in range(8)) + "\n")
    assert any("paragraph" in p.rule for p in wc.check_markdown(doc))


def test_the_checker_reads_docstrings_comments_and_help_text_of_python_files(tmp_path):
    source = tmp_path / "m.py"
    source.write_text('"""The module is read by nobody."""\n# The comment is written by hand.\nx = dict(help="The value is stored.")\n')
    found = {p.line for p in wc.check_python(source)}
    assert found == {1, 2, 3}


def test_the_command_line_tool_exits_with_1_when_a_rule_is_broken(tmp_path):
    bad, good = tmp_path / "bad.md", tmp_path / "good.md"
    bad.write_text("The file was deleted.\n")
    good.write_text("Delete the file.\n")
    run = lambda f: subprocess.run([sys.executable, str(ROOT / "scripts" / "writing_check.py"), str(f)], capture_output=True, text=True)  # noqa: E731, S603
    assert run(bad).returncode == 1 and "passive" in run(bad).stdout
    assert run(good).returncode == 0
