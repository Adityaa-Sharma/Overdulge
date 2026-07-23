from app.oauth.platforms import swiggy


def test_swiggy_config_targets_the_documented_auth_server() -> None:
    assert swiggy.CONFIG.name == "swiggy"
    assert swiggy.CONFIG.issuer == "https://mcp.swiggy.com/auth"
    assert swiggy.CONFIG.mcp_base_url == "https://mcp.swiggy.com"
