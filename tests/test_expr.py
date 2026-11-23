# SPDX-License-Identifier: LGPL-3.0-or-later
from decimal import Decimal
import pytest
from heng_kernel.expr import evaluate, check, ExprError


def test_decimal_arithmetic():
    assert evaluate("a + b", {"a": Decimal("0.1"), "b": 0.2}) == Decimal("0.3")


def test_sum_with_expr():
    lines = [{"amount": Decimal("100"), "tax": Decimal("13")},
             {"amount": Decimal("200"), "tax": Decimal("26")}]
    assert evaluate("sum(lines, 'amount + tax')", {"lines": lines}) == Decimal("339")


def test_injection_blocked():
    with pytest.raises(ExprError):
        evaluate("__import__('os').system('echo pwned')", {})


def test_private_attr_blocked():
    with pytest.raises(ExprError):
        evaluate("x.__class__", {"x": object()})


def test_fail_closed():
    ok, err = check("missing_name == 1", {})
    assert not ok and err


def test_compare_chain_and_in():
    assert evaluate("1 < 2 < 3", {})
    assert evaluate("state in ['draft', 'confirmed']", {"state": "draft"})
