"""Tests for the article scoring module."""

import pytest
from pipeline.scorer import score_to_bucket, compute_median_score, ScoreResult


class TestScoreToBucket:
    def test_hard_left(self):
        assert score_to_bucket(-0.9) == "left"

    def test_left_boundary(self):
        assert score_to_bucket(-0.6) == "centre-left"

    def test_centre_left(self):
        assert score_to_bucket(-0.4) == "centre-left"

    def test_centre(self):
        assert score_to_bucket(0.0) == "centre"

    def test_centre_right(self):
        assert score_to_bucket(0.4) == "centre-right"

    def test_right(self):
        assert score_to_bucket(0.8) == "right"

    def test_exact_boundary_centre(self):
        assert score_to_bucket(-0.2) == "centre"

    def test_exact_boundary_right(self):
        assert score_to_bucket(0.6) == "centre-right"

    def test_extreme_left(self):
        assert score_to_bucket(-1.0) == "left"

    def test_extreme_right(self):
        assert score_to_bucket(1.0) == "right"


class TestComputeMedianScore:
    def _make_result(self, score: float) -> ScoreResult:
        return ScoreResult(
            score=score, confidence=0.8, reasoning="test",
            dimensions={}, bucket=score_to_bucket(score), model="test",
        )

    def test_empty_list(self):
        assert compute_median_score([]) is None

    def test_single_score(self):
        assert compute_median_score([self._make_result(0.5)]) == 0.5

    def test_two_scores(self):
        scores = [self._make_result(-0.4), self._make_result(0.2)]
        assert compute_median_score(scores) == pytest.approx(-0.1)

    def test_three_scores_takes_middle(self):
        scores = [
            self._make_result(-0.8),
            self._make_result(0.1),
            self._make_result(0.9),
        ]
        assert compute_median_score(scores) == 0.1

    def test_three_scores_resists_outlier(self):
        scores = [
            self._make_result(-0.1),
            self._make_result(0.0),
            self._make_result(0.9),  # outlier
        ]
        assert compute_median_score(scores) == 0.0
