from heng_actions.compose import REPO_ROOT
from heng_eval.runner import run_suite


def test_golden_eval_suite_all_pass():
    report = run_suite(REPO_ROOT / "evals")
    failed = [r for r in report["results"] if not r["passed"]]
    assert not failed, failed
    assert report["total"] >= 15
