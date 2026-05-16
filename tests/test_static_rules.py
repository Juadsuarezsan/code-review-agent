from src.analyzers.bug_detector import static_scan
from src.parser.diff_parser import FileDiff


def _diff(added: list[tuple[int, str]]) -> FileDiff:
    return FileDiff(path="x.py", added=added)


def test_bare_except_flagged():
    c = static_scan(_diff([(1, "    except:")]))
    assert any(x.category == "bug" for x in c)


def test_eval_flagged_as_security():
    c = static_scan(_diff([(1, "    return eval(s)")]))
    assert any(x.category == "security" for x in c)


def test_os_system_flagged():
    c = static_scan(_diff([(1, "    os.system(cmd)")]))
    assert any(x.category == "security" for x in c)


def test_print_is_info_only():
    c = static_scan(_diff([(1, "    print('debug')")]))
    severities = {x.severity for x in c}
    assert severities == {"info"}


def test_clean_code_no_comments():
    c = static_scan(_diff([(1, "    return a + b")]))
    assert c == []


def test_eq_none_style_warning():
    c = static_scan(_diff([(1, "    if x == None:")]))
    assert any(x.category == "style" for x in c)
