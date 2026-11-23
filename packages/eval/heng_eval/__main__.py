# SPDX-License-Identifier: LGPL-3.0-or-later
"""CLI: python -m heng_eval run evals [--json report.json]  （CI 门禁入口）"""
from __future__ import annotations
import argparse
import json
import sys
from .runner import run_suite


def main():
    ap = argparse.ArgumentParser(prog="heng_eval")
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("path", nargs="?", default="evals")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()
    report = run_suite(args.path)
    for r in report["results"]:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"[{mark}] {r['id']}" + ("" if r["passed"] else "  -> " + "; ".join(r["failures"])))
    print(f"\n{report['passed']}/{report['total']} passed  (pass_rate={report['pass_rate']})")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    sys.exit(0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
