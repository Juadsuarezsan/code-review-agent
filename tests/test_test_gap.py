from src.analyzers.test_gap import detect_test_gaps
from src.parser.diff_parser import FileDiff


def test_new_public_function_without_tests_flagged():
    files = [
        FileDiff(path="src/new_module.py", added=[(1, "def new_public(x):"), (2, "    return x * 2")]),
    ]
    comments = detect_test_gaps(files)
    assert len(comments) == 1
    assert comments[0].category == "test_gap"


def test_new_function_WITH_test_changes_not_flagged():
    files = [
        FileDiff(path="src/new_module.py", added=[(1, "def new_public(x):"), (2, "    return x * 2")]),
        FileDiff(path="tests/test_new_module.py", added=[(1, "def test_x(): assert new_public(2) == 4")]),
    ]
    assert detect_test_gaps(files) == []


def test_private_function_not_flagged():
    files = [FileDiff(path="src/x.py", added=[(1, "def _internal():"), (2, "    pass")])]
    assert detect_test_gaps(files) == []
