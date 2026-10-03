"""Behavior-pinning tests for Korean Law MCP endpoint and tool validators."""

from __future__ import annotations

import pytest

from mcp_server.review import _validated_mcp_url, _validated_tool_name


@pytest.mark.parametrize(
    "url",
    [
        "https://law.example.com/mcp",
        "https://law.example.com/mcp?format=json",
        "HTTPS://LAW.EXAMPLE.COM/mcp",
        "http://127.0.0.1:3000/mcp",
        "http://localhost/mcp",
        "HTTP://LOCALHOST:8080/mcp",
        "http://[::1]:8080/mcp",
    ],
)
def test_https_and_loopback_http_urls_are_returned_unchanged(url: str) -> None:
    assert _validated_mcp_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "law.example.com/mcp",
        "//law.example.com/mcp",
        "ftp://law.example.com/mcp",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://",
    ],
)
def test_non_http_or_hostless_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="must be HTTP\\(S\\)"):
        _validated_mcp_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://law.example.com/mcp",
        "http://127.0.0.2/mcp",
        "http://0.0.0.0:8000/mcp",
        "http://localhost.evil.example/mcp",
        "http://127.0.0.1.nip.io/mcp",
        "http://[::ffff:127.0.0.1]/mcp",
        "http://ｌｏｃａｌｈｏｓｔ/mcp",
        # Userinfo cannot smuggle a loopback name in front of a remote host.
        "http://127.0.0.1:8080@evil.example/mcp",
        "http://localhost@evil.example/mcp",
    ],
)
def test_plain_http_is_only_allowed_for_exact_loopback_hosts(url: str) -> None:
    with pytest.raises(ValueError, match="must use HTTPS"):
        _validated_mcp_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@law.example.com/mcp",
        "https://token@law.example.com/mcp",
        "https://law.example.com/mcp#section",
        "http://localhost/mcp#@evil.example",
    ],
)
def test_credentials_and_fragments_are_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="credentials or fragments"):
        _validated_mcp_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://[::1/mcp",
        # A full-width colon becomes a port separator under NFKC.
        "http://localhost：8000/mcp",
    ],
)
def test_malformed_netlocs_are_rejected_by_url_parsing(url: str) -> None:
    with pytest.raises(ValueError):
        _validated_mcp_url(url)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("search_law", "search_law"),
        ("v1.2-beta_x", "v1.2-beta_x"),
        (" get_law_text \n", "get_law_text"),
        ("a" * 128, "a" * 128),
    ],
)
def test_tool_names_are_trimmed_and_returned(value: str, expected: str) -> None:
    assert _validated_tool_name(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "a" * 129,
        "law search",
        "법령검색",
        "tool/x",
        "../tool",
        "ｓｅａｒｃｈ",
        "search\u200blaw",
        "search_law\x00",
    ],
)
def test_invalid_tool_names_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="tool name is invalid"):
        _validated_tool_name(value)
