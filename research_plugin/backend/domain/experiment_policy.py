"""Shared experiment workflow limits."""

ACTIVE_EXPERIMENT_CAP = 7


def active_experiment_cap_reached_message(*, active_count: int) -> str:
    return (
        "active experiment cap reached: "
        f"project has {active_count} active experiments; "
        "finish one before creating another."
    )


def active_experiment_cap_would_exceed_message(
    *, active_count: int, proposed_count: int
) -> str:
    experiment_word = "experiment" if proposed_count == 1 else "experiments"
    return (
        "active experiment cap would be exceeded: "
        f"project has {active_count} active experiments and this reflection "
        f"proposes {proposed_count} new {experiment_word}; "
        "finish one before creating another."
    )
