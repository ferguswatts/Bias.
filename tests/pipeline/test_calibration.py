"""Calibration test: validates the scoring prompt against ground-truth assessments.

Runs the LLM scorer against 15 pre-selected articles with known ground-truth scores
and checks that:
1. Pearson correlation > 0.7 (prompt tracks human judgment)
2. Bucket agreement > 60% (articles land in expected buckets)
3. Score stability: re-scoring the same article produces consistent results (std dev < 0.15)

These tests require ANTHROPIC_API_KEY and make real API calls.
Run with: pytest tests/pipeline/test_calibration.py -v -s --timeout=300
"""

import asyncio
import json
import math
import os
import sys
from pathlib import Path
from statistics import mean, stdev

import pytest

# Add pipeline to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "pipeline"))

from scorer import score_article_claude, score_to_bucket  # noqa: E402

CALIBRATION_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "calibration"
ARTICLES_DIR = CALIBRATION_DIR / "articles"
SCORES_FILE = CALIBRATION_DIR / "scores.json"


def pearson_r(x: list[float], y: list[float]) -> float:
    """Compute Pearson correlation coefficient without numpy."""
    n = len(x)
    if n < 3:
        return 0.0
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    den_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
    den_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def load_calibration_data() -> list[dict]:
    """Load ground-truth scores and corresponding article texts."""
    with open(SCORES_FILE) as f:
        scores = json.load(f)

    calibration_set = []
    for entry in scores:
        article_path = ARTICLES_DIR / entry["file"]
        if not article_path.exists():
            continue

        text = article_path.read_text()
        # Strip header comments (lines starting with #)
        lines = text.split("\n")
        body_lines = [l for l in lines if not l.startswith("#")]
        body = "\n".join(body_lines).strip()

        if len(body) < 50:
            continue

        calibration_set.append({
            "file": entry["file"],
            "text": body,
            "human_score": entry["human_score"],
            "human_bucket": entry["human_bucket"],
            "title": entry.get("title", ""),
            "author": entry.get("author", ""),
        })

    return calibration_set


@pytest.fixture(scope="module")
def calibration_data():
    return load_calibration_data()


@pytest.fixture(scope="module")
def scored_results(calibration_data):
    """Score all calibration articles via the LLM (cached for the module)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    async def score_all():
        results = []
        for item in calibration_data:
            result = await score_article_claude(item["text"])
            results.append({
                "file": item["file"],
                "human_score": item["human_score"],
                "human_bucket": item["human_bucket"],
                "llm_score": result.score if result else None,
                "llm_bucket": result.bucket if result else None,
                "llm_confidence": result.confidence if result else None,
                "title": item["title"],
                "author": item["author"],
            })
            # Print progress
            status = f"{result.score:+.2f} ({result.bucket})" if result else "FAILED"
            human = f"{item['human_score']:+.2f} ({item['human_bucket']})"
            print(f"  {item['file']}: LLM={status}  Human={human}  | {item['author']}: {item['title'][:40]}")
        return results

    return asyncio.run(score_all())


class TestScoringCorrelation:
    """Test that LLM scores correlate with ground-truth assessments."""

    def test_all_articles_scored(self, scored_results):
        """Every calibration article should produce a valid score."""
        failed = [r for r in scored_results if r["llm_score"] is None]
        assert len(failed) == 0, f"{len(failed)} articles failed to score: {[r['file'] for r in failed]}"

    def test_pearson_correlation_above_threshold(self, scored_results):
        """Pearson r between LLM and ground-truth scores must exceed 0.7."""
        human_scores = [r["human_score"] for r in scored_results if r["llm_score"] is not None]
        llm_scores = [r["llm_score"] for r in scored_results if r["llm_score"] is not None]

        r = pearson_r(human_scores, llm_scores)
        print(f"\n  Pearson r = {r:.4f} (threshold: 0.70)")

        # Print per-article comparison
        print(f"\n  {'File':<18s} {'Human':>7s} {'LLM':>7s} {'Delta':>7s} {'Bucket Match':>13s}")
        print(f"  {'-'*18} {'-'*7} {'-'*7} {'-'*7} {'-'*13}")
        for res in scored_results:
            if res["llm_score"] is not None:
                delta = res["llm_score"] - res["human_score"]
                match = "✓" if res["llm_bucket"] == res["human_bucket"] else "✗"
                print(f"  {res['file']:<18s} {res['human_score']:+.2f}  {res['llm_score']:+.2f}  {delta:+.2f}     {match} {res['human_bucket']:>13s} → {res['llm_bucket']}")

        assert r > 0.7, f"Pearson r = {r:.4f}, below threshold of 0.70"

    def test_bucket_agreement_above_threshold(self, scored_results):
        """At least 60% of articles should land in the same bucket as ground truth."""
        valid = [r for r in scored_results if r["llm_score"] is not None]
        matches = sum(1 for r in valid if r["llm_bucket"] == r["human_bucket"])
        pct = matches / len(valid) * 100

        print(f"\n  Bucket agreement: {matches}/{len(valid)} = {pct:.0f}% (threshold: 60%)")

        # Per-bucket breakdown
        from collections import Counter
        bucket_totals = Counter(r["human_bucket"] for r in valid)
        bucket_matches = Counter(r["human_bucket"] for r in valid if r["llm_bucket"] == r["human_bucket"])
        print(f"\n  {'Bucket':<15s} {'Correct':>8s} {'Total':>6s} {'Accuracy':>9s}")
        for bucket in ["left", "centre-left", "centre", "centre-right", "right"]:
            total = bucket_totals.get(bucket, 0)
            correct = bucket_matches.get(bucket, 0)
            acc = f"{correct/total*100:.0f}%" if total > 0 else "n/a"
            print(f"  {bucket:<15s} {correct:>8d} {total:>6d} {acc:>9s}")

        assert pct >= 60, f"Bucket agreement = {pct:.0f}%, below threshold of 60%"

    def test_mean_absolute_error(self, scored_results):
        """Mean absolute error between LLM and ground-truth should be < 0.25."""
        valid = [r for r in scored_results if r["llm_score"] is not None]
        errors = [abs(r["llm_score"] - r["human_score"]) for r in valid]
        mae = mean(errors)
        max_err = max(errors)

        print(f"\n  Mean Absolute Error: {mae:.3f} (threshold: 0.25)")
        print(f"  Max Absolute Error:  {max_err:.3f}")

        assert mae < 0.25, f"MAE = {mae:.3f}, above threshold of 0.25"


class TestScoreStability:
    """Test that re-scoring the same article produces consistent results."""

    def test_rescore_stability(self, calibration_data):
        """Re-score 3 articles twice each; std dev of scores should be < 0.15."""
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set")

        # Pick 3 articles: one from each end and one from the middle
        test_articles = [calibration_data[0], calibration_data[7], calibration_data[-1]]

        async def rescore():
            results = []
            for item in test_articles:
                scores = []
                for run in range(2):
                    result = await score_article_claude(item["text"])
                    if result:
                        scores.append(result.score)
                if len(scores) >= 2:
                    sd = stdev(scores)
                    results.append({
                        "file": item["file"],
                        "scores": scores,
                        "stdev": sd,
                    })
                    print(f"  {item['file']}: scores={[f'{s:+.2f}' for s in scores]} stdev={sd:.3f}")
            return results

        results = asyncio.run(rescore())
        assert len(results) > 0, "No articles were successfully re-scored"

        for r in results:
            assert r["stdev"] < 0.15, (
                f"{r['file']} has stdev={r['stdev']:.3f} across scores {r['scores']}, "
                f"above threshold of 0.15"
            )
