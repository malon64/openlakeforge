from olf import polaris


def test_unreachable_endpoint_returns_zero() -> None:
    # Port 1 is not listening; a transport failure maps to "unknown" (0),
    # which callers treat as "leave bootstrap generation unchanged".
    status = polaris.request_token_status(
        "http://127.0.0.1:1/api/catalog/v1/oauth/tokens",
        "client",
        "secret",
        "PRINCIPAL_ROLE:ALL",
        timeout=1.0,
    )
    assert status == 0


def test_stale_exit_code_is_distinct() -> None:
    assert polaris.STALE_EXIT_CODE == 3
