"""Web research module for market intelligence.

Searches the web for news and information about Polymarket questions
to build informed probability estimates instead of guessing.
"""

import logging
import re
import requests
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# DuckDuckGo Instant Answer API (no API key needed)
DDG_API = "https://api.duckduckgo.com/"


class MarketResearcher:
    """Searches for information about market questions to estimate probabilities."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "PolymarketBot/1.0 (research)",
        })
        self._cache: dict[str, dict] = {}

    def research_market(self, question: str, description: str = "") -> dict:
        """Research a market question and return intelligence.

        Returns:
            dict with keys:
                - snippets: list of relevant text excerpts
                - sentiment: float from -1 (NO likely) to +1 (YES likely)
                - has_recent_news: bool
                - keywords_found: list of relevant keywords detected
                - suggested_probability_shift: float adjustment to apply
        """
        cache_key = question[:100]
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = {
            "snippets": [],
            "sentiment": 0.0,
            "has_recent_news": False,
            "keywords_found": [],
            "suggested_probability_shift": 0.0,
        }

        # Extract key entities and terms from the question
        search_query = self._build_search_query(question)

        # Search DuckDuckGo for context
        ddg_results = self._search_ddg(search_query)
        if ddg_results:
            result["snippets"] = ddg_results["snippets"]
            result["has_recent_news"] = ddg_results["has_results"]

        # Analyze question for predictable patterns
        pattern_analysis = self._analyze_question_patterns(question, description)
        result["keywords_found"] = pattern_analysis["keywords"]
        result["sentiment"] = pattern_analysis["sentiment"]
        result["suggested_probability_shift"] = pattern_analysis["shift"]

        # Combine signals
        if ddg_results.get("has_results"):
            news_sentiment = self._analyze_snippet_sentiment(
                ddg_results["snippets"], question
            )
            # Blend pattern analysis with news sentiment
            result["sentiment"] = (
                0.4 * pattern_analysis["sentiment"] + 0.6 * news_sentiment
            )
            result["suggested_probability_shift"] = result["sentiment"] * 0.08

        self._cache[cache_key] = result
        return result

    def _build_search_query(self, question: str) -> str:
        """Extract a good search query from a market question."""
        # Remove common Polymarket question framing
        q = question
        for prefix in [
            "Will ", "Will the ", "Is ", "Are ", "Does ", "Do ",
            "Has ", "Have ", "Can ", "Could ", "Should ",
        ]:
            if q.startswith(prefix):
                q = q[len(prefix):]
                break

        # Remove trailing question mark and "by <date>" clauses
        q = q.rstrip("?").strip()
        q = re.sub(r'\b(by|before|after|on)\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(,?\s*\d{4})?', '', q, flags=re.IGNORECASE)

        # Add "latest news" to get recent results
        return f"{q.strip()} latest news 2026"

    def _search_ddg(self, query: str) -> dict:
        """Search DuckDuckGo for information."""
        result = {"snippets": [], "has_results": False}

        try:
            resp = self.session.get(
                DDG_API,
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            snippets = []

            # Abstract (main answer)
            abstract = data.get("AbstractText", "")
            if abstract:
                snippets.append(abstract[:500])

            # Related topics
            for topic in data.get("RelatedTopics", [])[:5]:
                text = topic.get("Text", "")
                if text:
                    snippets.append(text[:300])

            # Infobox
            infobox = data.get("Infobox", {})
            if isinstance(infobox, dict):
                for item in infobox.get("content", [])[:3]:
                    label = item.get("label", "")
                    value = item.get("value", "")
                    if label and value:
                        snippets.append(f"{label}: {value}")

            result["snippets"] = snippets
            result["has_results"] = len(snippets) > 0

        except Exception as e:
            logger.debug("DuckDuckGo search failed for '%s': %s", query[:50], e)

        return result

    def _analyze_question_patterns(self, question: str, description: str) -> dict:
        """Analyze question text for predictable patterns and biases."""
        q_lower = (question + " " + description).lower()
        keywords = []
        sentiment = 0.0

        # Temporal patterns - things closer to deadline with no movement
        # tend to resolve NO
        if any(w in q_lower for w in ["by end of", "before", "by march", "by april"]):
            keywords.append("deadline_pressure")
            sentiment -= 0.05  # Slight NO bias for deadline questions

        # Political patterns
        if any(w in q_lower for w in ["trump", "biden", "congress", "senate", "election"]):
            keywords.append("political")
            # Political markets are well-traded, hard to beat
            sentiment *= 0.5  # Reduce confidence

        # Crypto/tech patterns
        if any(w in q_lower for w in ["bitcoin", "ethereum", "btc", "eth", "crypto"]):
            keywords.append("crypto")

        # Sports patterns
        if any(w in q_lower for w in ["win", "championship", "super bowl", "world cup", "nba", "nfl"]):
            keywords.append("sports")

        # Regulatory/legal
        if any(w in q_lower for w in ["ban", "regulation", "approve", "fda", "sec"]):
            keywords.append("regulatory")
            sentiment -= 0.03  # Regulatory actions tend to be slow/NO

        # "Record" or "all-time high" - usually NO
        if any(w in q_lower for w in ["record", "all-time", "highest ever", "most ever"]):
            keywords.append("record_breaking")
            sentiment -= 0.04

        # Positive action words that suggest YES momentum
        if any(w in q_lower for w in ["announce", "confirm", "launch", "release"]):
            keywords.append("action_word")
            sentiment += 0.03

        return {
            "keywords": keywords,
            "sentiment": max(-0.3, min(0.3, sentiment)),
            "shift": sentiment * 0.1,
        }

    def _analyze_snippet_sentiment(self, snippets: list[str], question: str) -> float:
        """Simple sentiment analysis on search snippets relative to the question.

        Returns float from -1 (suggests NO) to +1 (suggests YES).
        """
        if not snippets:
            return 0.0

        text = " ".join(snippets).lower()

        # Count positive vs negative signal words
        positive_words = [
            "confirmed", "approved", "passed", "signed", "announced",
            "agreed", "successful", "achieved", "completed", "won",
            "surpassed", "exceeded", "likely", "expected", "set to",
            "will", "plans to", "on track",
        ]
        negative_words = [
            "denied", "rejected", "failed", "blocked", "delayed",
            "unlikely", "canceled", "cancelled", "opposed", "vetoed",
            "stalled", "postponed", "doubt", "uncertain", "not expected",
            "won't", "will not", "dropped",
        ]

        pos_count = sum(1 for w in positive_words if w in text)
        neg_count = sum(1 for w in negative_words if w in text)

        total = pos_count + neg_count
        if total == 0:
            return 0.0

        # Normalized sentiment: +1 if all positive, -1 if all negative
        return (pos_count - neg_count) / total
