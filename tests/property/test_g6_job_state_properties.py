"""Property checks for the closed G6 state transition graph."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from ansys_research_runner.domain.jobs import TERMINAL_JOB_STATUSES, JobStatus
from ansys_research_runner.services.job_registry import ALLOWED_TRANSITIONS, FORWARD_PHASES


@given(st.sampled_from(tuple(TERMINAL_JOB_STATUSES)), st.sampled_from(tuple(JobStatus)))
def test_terminal_states_have_no_outgoing_edge(source: JobStatus, target: JobStatus) -> None:
    assert target not in ALLOWED_TRANSITIONS[source]


@given(
    st.integers(min_value=0, max_value=len(FORWARD_PHASES) - 2),
    st.integers(min_value=0, max_value=len(FORWARD_PHASES) - 1),
)
def test_happy_path_never_allows_phase_jumps(source_index: int, target_index: int) -> None:
    source = FORWARD_PHASES[source_index]
    target = FORWARD_PHASES[target_index]
    if target_index == source_index + 1:
        assert target in ALLOWED_TRANSITIONS[source]
    elif target not in {
        JobStatus.CANCEL_REQUESTED,
        JobStatus.RECOVERY_REQUIRED,
    }:
        assert target not in ALLOWED_TRANSITIONS[source]
