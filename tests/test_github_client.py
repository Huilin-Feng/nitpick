import pytest

from nitpick.github_client import parse_pr_url


def test_parse_pr_url():
    assert parse_pr_url("https://github.com/octo/demo/pull/42") == ("octo", "demo", 42)
    assert parse_pr_url("https://github.com/octo/demo/pull/42/") == ("octo", "demo", 42)


def test_parse_pr_url_rejects_other_urls():
    with pytest.raises(ValueError):
        parse_pr_url("https://github.com/octo/demo/issues/42")
