"""Regression tests for Tech News robustness and API endpoint."""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _ollama_timeout():
    """Force offline-fast LLM behavior for tech-news tests."""
    old = os.environ.get("OLLAMA_TIMEOUT")
    os.environ["OLLAMA_TIMEOUT"] = "0.001"
    yield
    if old is None:
        os.environ.pop("OLLAMA_TIMEOUT", None)
    else:
        os.environ["OLLAMA_TIMEOUT"] = old


def _make_story(
    title: str,
    object_id: str = "123",
    points: Any = 5,
    comments: Any = 2,
    created_at_i: int = 1723600000,
    url: str = "http://example.com",
) -> dict[str, Any]:
    return {
        "objectID": object_id,
        "title": title,
        "url": url,
        "points": points,
        "num_comments": comments,
        "created_at_i": created_at_i,
    }


class TestBuildTechRadar:
    """build_tech_radar must not raise on malformed or missing data."""

    def test_empty_stories_returns_empty_radar(self):
        from ode.tech_radar import build_tech_radar

        with patch("ode.tech_radar._fetch_hn_stories", return_value=[]):
            result = build_tech_radar(refresh=True)

        assert result["trends"] == []
        assert result["cached"] is False
        assert "last_updated" in result

    def test_hits_none_returns_empty_radar(self):
        """A malformed HN response where hits is null must not cause a 500."""
        from ode.tech_radar import build_tech_radar

        with patch("ode.tech_radar._fetch_hn_stories", return_value=None):
            result = build_tech_radar(refresh=True)

        assert result["trends"] == []

    def test_non_dict_hits_return_empty_radar(self):
        """If the fetched payload is a list, .get() on a list must not crash."""
        from ode.tech_radar import build_tech_radar

        # Simulate _fetch_hn_stories returning a list instead of a list of dicts.
        with patch("ode.tech_radar._fetch_hn_stories", return_value=["bad"]):
            result = build_tech_radar(refresh=True)

        assert result["trends"] == []

    def test_string_points_and_comments_are_coerced(self):
        """HN sometimes returns engagement counts as strings; build must coerce them."""
        from ode.tech_radar import build_tech_radar

        # Use a repeated tech identifier so a cluster forms and momentum is computed.
        stories = [
            _make_story("OpenAI launches new model", object_id="1", points="10", comments="3"),
            _make_story("OpenAI chips are booming", object_id="2", points="5", comments="1"),
        ]
        with patch("ode.tech_radar._fetch_hn_stories", return_value=stories):
            result = build_tech_radar(refresh=True)

        assert result["trends"]
        for trend in result["trends"]:
            for article in trend["articles"]:
                assert isinstance(article["points"], int)
                assert isinstance(article["comments"], int)


class TestTechNewsEndpoint:
    """The /api/tech-news endpoint must return 200 with valid JSON."""

    def test_endpoint_returns_200_and_json(self):
        from fastapi.testclient import TestClient
        from ode.api.main import app

        with TestClient(app) as client, patch(
            "ode.api.main.build_tech_radar",
            return_value={
                "last_updated": "2026-08-14T00:00:00+00:00",
                "cached": False,
                "trends": [
                    {"id": "ai", "name": "AI", "momentum_score": 50.0, "articles": []},
                ],
            },
        ):
            response = client.get("/api/tech-news")

        assert response.status_code == 200, response.text
        data = response.json()
        assert "trends" in data
        assert "last_updated" in data

    def test_endpoint_returns_200_empty_when_build_raises(self):
        """An unexpected exception in build_tech_radar must not surface as HTTP 500."""
        from fastapi.testclient import TestClient
        from ode.api.main import app

        with TestClient(app) as client, patch(
            "ode.api.main.build_tech_radar",
            side_effect=RuntimeError("simulated radar failure"),
        ):
            response = client.get("/api/tech-news")

        assert response.status_code == 200, response.text
        data = response.json()
        assert data.get("trends") == []
