"""DELIBERATELY FAILING test used once to show that CI blocks a broken change (Phase 3 evidence).
The fix commit that follows deletes this file."""


def test_deliberate_failure_to_prove_ci_blocks_the_merge():
    assert 1 + 1 == 3, "deliberate failure: CI must turn red and block the merge"
