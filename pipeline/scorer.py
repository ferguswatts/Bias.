"""LLM-based article scoring for political lean analysis."""

import json
import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Bump this when the scoring prompt changes — triggers re-scoring of articles
PROMPT_VERSION = "v2-cuibono"

SCORING_PROMPT = """You are a political bias analyst for New Zealand media. Score the following
news article on a scale from -1.0 (hard left) to +1.0 (hard right).

Consider these dimensions:
- FRAMING: How is the topic presented? Who is the protagonist/antagonist?
- SOURCE SELECTION: Which politicians, experts, or voices are quoted? Are opposing views included?
- LANGUAGE: Is loaded or emotive language used? ("slammed", "controversial", "radical")
- TOPIC EMPHASIS: What aspects of the story are highlighted vs downplayed?
- OMISSION: What relevant context or perspectives are missing?

CRITICAL — ask "CUI BONO?" (who benefits from this story being published?):
- An article exposing a right-wing party's internal divisions, scandals, or failures
  BENEFITS THE LEFT — score it left-leaning, even if the prose reads as balanced.
- An article exposing a left-wing party's problems BENEFITS THE RIGHT — score accordingly.
- The editorial choice of what to write about is itself a signal of lean.
- "Both sides quoted" does NOT mean neutral. The overall reader impression matters more.

Additional NZ context:
- When a politician acts in their constitutional role (e.g. Attorney General commenting
  on judicial conduct), reporting that action is neutral — not pro-government.
- Omission is a strong signal: profiling a politician's controversial views while omitting
  their actual record is bias by omission.

NZ political context:
- Left = Labour, Greens, Te Pati Maori
- Right = National, ACT
- Centre = NZ First (varies by issue)
- A score of 0.0 represents genuinely neutral/centrist reporting
- Straight news reporting of government policy is not inherently biased — assess the
  journalist's editorial choices, not the topic itself

Return a JSON object:
{
  "score": float (-1.0 to 1.0),
  "confidence": float (0.0 to 1.0),
  "reasoning": "2-3 sentence explanation",
  "dimensions": {
    "story_selection": float,
    "framing": float,
    "source_selection": float,
    "language": float,
    "omission": float
  }
}

Article text:
---
{article_text}
---"""

BUCKET_BOUNDARIES = [
    (-1.0, -0.6, "left"),
    (-0.6, -0.2, "centre-left"),
    (-0.2, 0.2, "centre"),
    (0.2, 0.6, "centre-right"),
    (0.6, 1.0, "right"),
]


@dataclass
class ScoreResult:
    score: float
    confidence: float
    reasoning: str
    dimensions: dict[str, float]
    bucket: str
    model: str


def score_to_bucket(score: float) -> str:
    for low, high, label in BUCKET_BOUNDARIES:
        if low <= score < high:
            return label
    return "right" if score >= 0.6 else "left"


async def score_article_claude(article_text: str) -> ScoreResult | None:
    """Score an article using Claude API."""
    try:
        import anthropic
    except ImportError:
        log.error("anthropic package not installed. Run: pip install anthropic")
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or api_key == "your_key_here":
        log.error("ANTHROPIC_API_KEY not set. Add it to your .env file.")
        return None

    client = anthropic.AsyncAnthropic(api_key=api_key)
    prompt = SCORING_PROMPT.replace("{article_text}", article_text[:8000])

    for attempt in range(3):
        try:
            response = await client.messages.create(
                model="claude-opus-4-5",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text

            # Extract JSON from response
            json_match = text
            if "```" in text:
                json_match = text.split("```")[1].strip()
                if json_match.startswith("json"):
                    json_match = json_match[4:].strip()

            data = json.loads(json_match)
            score = float(data["score"])
            score = max(-1.0, min(1.0, score))

            return ScoreResult(
                score=score,
                confidence=float(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", ""),
                dimensions=data.get("dimensions", {}),
                bucket=score_to_bucket(score),
                model="claude",
            )
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            log.warning(f"JSON parse error on attempt {attempt + 1}: {e}")
            if attempt == 2:
                return None
            continue
        except Exception as e:
            log.warning(f"API error on attempt {attempt + 1}: {e}")
            if attempt == 2:
                return None
            continue

    return None


async def score_article_gpt(article_text: str) -> ScoreResult | None:
    """Score an article using OpenAI GPT API."""
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return None

    client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    prompt = SCORING_PROMPT.replace("{article_text}", article_text[:8000])

    for attempt in range(3):
        try:
            response = await client.chat.completions.create(
                model="gpt-4o",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.choices[0].message.content

            json_match = text
            if "```" in text:
                json_match = text.split("```")[1].strip()
                if json_match.startswith("json"):
                    json_match = json_match[4:].strip()

            data = json.loads(json_match)
            score = float(data["score"])
            score = max(-1.0, min(1.0, score))

            return ScoreResult(
                score=score,
                confidence=float(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", ""),
                dimensions=data.get("dimensions", {}),
                bucket=score_to_bucket(score),
                model="gpt",
            )
        except Exception:
            if attempt == 2:
                return None
            continue

    return None


def compute_median_score(scores: list[ScoreResult]) -> float | None:
    if not scores:
        return None
    values = sorted(s.score for s in scores)
    n = len(values)
    if n % 2 == 1:
        return values[n // 2]
    return (values[n // 2 - 1] + values[n // 2]) / 2
