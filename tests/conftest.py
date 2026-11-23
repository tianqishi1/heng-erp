# SPDX-License-Identifier: LGPL-3.0-or-later
import datetime as dt
import pytest
from heng_contracts.models import ActorRef
from heng_actions.compose import build_engine
from heng_eval.fixture import build_fixture

CLOCK = dt.date(2026, 11, 23)


@pytest.fixture()
def env():
    engine, sf, inv = build_engine("sqlite://", clock=CLOCK)
    tenant = "t_test"
    build_fixture(engine, sf, tenant)
    return engine, sf, inv, tenant


def actor(tenant, type="human", id="u_test", scopes=None):
    return ActorRef(type=type, id=id, tenant_id=tenant, scopes=scopes or ["*"])
