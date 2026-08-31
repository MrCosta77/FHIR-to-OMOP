import pytest

from src.quality.validate_dqd import validate_dqd_result

POLICY = {
    "status": "approved",
    "max_errors": 0,
    "max_failed_checks": 1,
    "allowed_failure_ids": {
        "check-2": {
            "reason": "Reviewed synthetic omission.",
            "max_violated_rows": 1,
            "max_pct_violated_rows": 0.1,
        }
    },
}


def _check(**values):
    return {
        "checkId": "check-1", "checkName": "cdmTable", "isError": 0,
        "failed": 0, "passed": 1,
        "numViolatedRows": 0, "pctViolatedRows": 0,
        **values,
    }


def test_dqd_policy_accepts_only_reviewed_failure_budget():
    result = {"CheckResults": [_check(), _check(checkId="check-2", checkName="sourceValueCompleteness", failed=1, passed=0, numViolatedRows=1, pctViolatedRows=0.1)]}
    summary = validate_dqd_result(result, POLICY)
    assert summary["errors"] == 0
    assert summary["failed"] == 1


def test_dqd_execution_error_is_never_hidden_by_failed_budget():
    result = {"CheckResults": [_check(isError=1, passed=0)]}
    with pytest.raises(ValueError, match="error budget exceeded"):
        validate_dqd_result(result, POLICY)


def test_unreviewed_dqd_failure_is_rejected():
    result = {"CheckResults": [_check(checkId="check-3", checkName="sourceValueCompleteness", failed=1, passed=0)]}
    with pytest.raises(ValueError, match="unreviewed failed checks"):
        validate_dqd_result(result, POLICY)


def test_unapproved_policy_is_rejected():
    result = {"CheckResults": [_check()]}
    with pytest.raises(ValueError, match="not been approved"):
        validate_dqd_result(result, {**POLICY, "status": "proposed"})


def test_stale_dqd_allowance_is_rejected():
    result = {"CheckResults": [_check()]}
    with pytest.raises(ValueError, match="stale failure allowances"):
        validate_dqd_result(result, POLICY)


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"numViolatedRows": 2, "pctViolatedRows": 0.1}, "violated-row cap exceeded"),
        ({"numViolatedRows": 1, "pctViolatedRows": 0.2}, "violated-percent cap exceeded"),
    ],
)
def test_dqd_allowance_numeric_caps_are_enforced(values, message):
    failed = _check(
        checkId="check-2", checkName="sourceValueCompleteness",
        failed=1, passed=0, **values,
    )
    with pytest.raises(ValueError, match=message):
        validate_dqd_result({"CheckResults": [failed]}, POLICY)
