import mlratelimit


def test_version_is_exposed() -> None:
    assert mlratelimit.__version__ == "0.1.0"
