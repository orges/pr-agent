"""Tests for @mention-style command triggering in the GitHub app.

Covers _rewrite_command_mention: configured triggers rewrite "@orges improve" to
"/improve", unknown triggers/commands stay None (ignored), and slash comments
are never touched.
"""

import pytest

from pr_agent.servers.github_app import _rewrite_command_mention


@pytest.fixture
def mention_settings(monkeypatch):
    """Point [github_app] command_mentions at the configured triggers."""
    settings = {"github_app": {"command_mentions": ["orges", "orges-pr"]}}

    class _GithubApp:
        def get(self, key, default=None):
            return settings.get("github_app", {}).get(key, default)

    class _Settings:
        github_app = _GithubApp()

    monkeypatch.setattr("pr_agent.servers.github_app.get_settings", lambda: _Settings())
    return settings


@pytest.mark.parametrize("body", [
    "@orges improve",
    "@Orges Improve",
    "@orges-pr improve",
    "@orges improve focus on the retry logic",
    "  @orges   improve",
    "@orges review",
    "@orges ask what does the retry loop do?",
])
def test_configured_trigger_rewrites_to_slash_command(mention_settings, body):
    rewritten = _rewrite_command_mention(body)
    assert rewritten is not None
    assert rewritten.startswith("/")
    assert rewritten.split(maxsplit=1)[0].lstrip("/").lower() in {
        "improve", "review", "ask",
    }


def test_multiline_body_preserved_after_command_token(mention_settings):
    rewritten = _rewrite_command_mention("@orges improve\n\nfocus on auth")
    assert rewritten == "/improve\n\nfocus on auth"


def test_unknown_trigger_is_ignored(mention_settings):
    assert _rewrite_command_mention("@someone review") is None


def test_unknown_command_word_is_ignored(mention_settings):
    assert _rewrite_command_mention("@orges hello") is None


def test_mention_without_command_is_ignored(mention_settings):
    assert _rewrite_command_mention("@orges") is None


def test_non_string_body_is_ignored(mention_settings):
    assert _rewrite_command_mention(None) is None
    assert _rewrite_command_mention(123) is None


def test_slash_comments_are_not_mention_business(mention_settings):
    assert _rewrite_command_mention("/improve") is None


def test_disabled_when_no_triggers_configured(monkeypatch):
    settings = {"github_app": {}}

    class _GithubApp:
        def get(self, key, default=None):
            return default

    class _Settings:
        github_app = _GithubApp()

    monkeypatch.setattr("pr_agent.servers.github_app.get_settings", lambda: _Settings())
    assert _rewrite_command_mention("@orges improve") is None


def test_string_setting_value_is_supported(monkeypatch):
    settings = {"github_app": {"command_mentions": "orges"}}

    class _GithubApp:
        def get(self, key, default=None):
            return settings.get("github_app", {}).get(key, default)

    class _Settings:
        github_app = _GithubApp()

    monkeypatch.setattr("pr_agent.servers.github_app.get_settings", lambda: _Settings())
    assert _rewrite_command_mention("@orges review") == "/review"
