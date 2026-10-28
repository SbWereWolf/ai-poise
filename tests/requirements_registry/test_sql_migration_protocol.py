"""Library actions are state-driven; normal business replay is a separate contract."""

from copy import deepcopy

import pytest

from poise.modules.foundation.errors import PoiseError
from requirements_registry.sql_migration_support import (
    applied, files, literal, migration_request, owned, pair, start, state,
)


@pytest.fixture
def subject(project):
    return start(project)


def changed(subject):
    before = set(applied(subject.database))
    contract = literal("contract.json")
    assert owned(subject, contract["ordinary_create"]) == contract["ordinary_create_response"]
    identities = sorted(set(applied(subject.database)) - before)
    assert len(identities) == 1
    return identities


def test_fresh_guards_allow_state_no_ops_and_real_reapply_after_rollback(subject):
    identities = changed(subject)
    committed, published = state(subject.database), files(subject)

    assert owned(subject, migration_request(subject, "apply", identities)) == {
        "status": "applied", "migration_ids": [],
    }
    assert state(subject.database) == committed
    assert files(subject) == published
    assert owned(subject, migration_request(subject, "rollback", identities)) == {
        "status": "rolled_back", "migration_ids": identities,
    }
    reversed_state = state(subject.database)
    assert reversed_state != committed
    assert owned(subject, migration_request(subject, "rollback", identities)) == {
        "status": "rolled_back", "migration_ids": [],
    }
    assert state(subject.database) == reversed_state
    assert owned(subject, migration_request(subject, "apply", identities)) == {
        "status": "applied", "migration_ids": identities,
    }
    assert state(subject.database) == committed
    assert files(subject) == published


@pytest.mark.parametrize("field", ["expected_revision", "expected_state", "migration_ids"])
def test_stale_guards_and_duplicate_ids_refuse_before_sql(subject, field):
    identities = changed(subject)
    packet = migration_request(subject, "rollback", identities)
    if field == "expected_revision":
        packet["input"][field] = 1
    elif field == "expected_state":
        packet["input"][field] = "0" * 64
    else:
        packet["input"][field] = identities + identities
    before, before_ids, before_files = state(subject.database), applied(subject.database), files(subject)
    with pytest.raises(PoiseError):
        owned(subject, packet)
    assert state(subject.database) == before
    assert applied(subject.database) == before_ids
    assert files(subject) == before_files


def test_reused_business_request_id_with_changed_intent_is_not_replayed(subject):
    changed(subject)
    packet = deepcopy(literal("contract.json")["ordinary_create"])
    packet["input"]["operations"][0]["row"]["definition"] = "Different intent"
    before, before_ids, before_files = state(subject.database), applied(subject.database), files(subject)
    with pytest.raises(PoiseError, match="(?i)request|conflict|intent"):
        owned(subject, packet)
    assert state(subject.database) == before
    assert applied(subject.database) == before_ids
    assert files(subject) == before_files


def test_negative_revision_input_is_refused_without_coercion(subject):
    contract = literal("contract.json")
    assert owned(subject, contract["query"]) == contract["query_response"]
    packet = deepcopy(contract["ordinary_create"])
    packet["input"]["expected_revision"] = -1
    before = state(subject.database)
    with pytest.raises(PoiseError):
        owned(subject, packet)
    assert state(subject.database) == before
    assert files(subject) == {}


@pytest.mark.parametrize("field,value", [
    ("expected_state", "absent"), ("expected_revision", None),
    ("action", "absent"), ("migration_ids", None),
])
def test_required_action_fields_have_no_null_or_missing_defaults(subject, field, value):
    assert owned(subject, literal("contract.json")["query"]) == literal("contract.json")["query_response"]
    identity = pair(subject, "0010_pending",
                    "UPDATE domain_areas SET definition='Pending' WHERE id='D1';\n",
                    "UPDATE domain_areas SET definition='Domain fixture.' WHERE id='D1';\n")
    packet = migration_request(subject, "apply", [identity])
    if value == "absent":
        del packet["input"][field]
    else:
        packet["input"][field] = value
    before, before_ids, before_files = state(subject.database), applied(subject.database), files(subject)
    with pytest.raises(PoiseError):
        owned(subject, packet)
    assert state(subject.database) == before
    assert applied(subject.database) == before_ids
    assert files(subject) == before_files
