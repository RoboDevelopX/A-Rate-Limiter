import pytest

from mlratelimit import Quota


def test_quota_rate() -> None:
    assert Quota(limit=120, period=60).rate == 2.0


@pytest.mark.parametrize(("limit", "period"), [(0, 1.0), (-1, 1.0), (1, 0.0), (1, -5.0)])
def test_quota_rejects_invalid_values(limit: int, period: float) -> None:
    with pytest.raises(ValueError, match="must be"):
        Quota(limit=limit, period=period)
