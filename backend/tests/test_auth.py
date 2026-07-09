import pytest
from fastapi import HTTPException

from app.auth import require_token


def test_missing_token_rejected():
    with pytest.raises(HTTPException) as exc:
        require_token(authorization="")
    assert exc.value.status_code == 401


def test_wrong_token_rejected():
    with pytest.raises(HTTPException) as exc:
        require_token(authorization="Bearer nope")
    assert exc.value.status_code == 401


def test_correct_token_accepted():
    # Token comes from LOCAL_API_TOKEN=test-token set in conftest.
    require_token(authorization="Bearer test-token")
