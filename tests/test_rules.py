# SPDX-License-Identifier: LGPL-3.0-or-later
import datetime as dt
from decimal import Decimal
import pytest
from pathlib import Path
from heng_kernel.rules import RuleEngine
from heng_actions.compose import REPO_ROOT


@pytest.fixture()
def rules():
    return RuleEngine().load_dir(REPO_ROOT / "rulesets" / "tax_cn")


def test_effective_dating(rules):
    assert rules.rate_for("standard", dt.date(2019, 6, 1)) == Decimal("0.16")
    assert rules.rate_for("standard", dt.date(2026, 11, 23)) == Decimal("0.13")
    assert rules.ruleset_version(dt.date(2026, 11, 23)) == "tax_cn@2026.1"


def test_no_ruleset_outside_range(rules):
    with pytest.raises(LookupError):
        rules.rate_for("standard", dt.date(2015, 1, 1))


def test_split_tax_exact(rules):
    amount, tax = rules.split_tax(Decimal("113000"), Decimal("0.13"))
    assert amount == Decimal("100000.00") and tax == Decimal("13000.00")


def test_split_tax_rounding_closure(rules):
    for total in ["100001", "0.03", "99999.99", "12345.67"]:
        amount, tax = rules.split_tax(Decimal(total), Decimal("0.13"))
        assert amount + tax == Decimal(total).quantize(Decimal("0.01"))
