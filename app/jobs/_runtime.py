"""Module-level holder for the runtime :class:`Container` accessed by scheduled jobs.

APScheduler with the Redis jobstore (ARCHITECTURE_SPEC §11.1) serializes
each job as ``(qualified_function_path, kwargs)``. When a job fires the
scheduler re-imports the function and calls it with the stored kwargs.
That means scheduled callables **cannot** receive the live ``Container``
through their argument list — it isn't picklable, and even if it were,
the deserialized copy would point at dead resources after a restart.

So the bot wires the running ``Container`` into this module once at
startup. Jobs reach the bot, the session factory, redis and settings by
calling :func:`get_runtime_container`. This is a controlled global —
single process, single bot, one instance per lifetime — not a free-for-
all (the tests prove the boundary with :func:`reset_runtime_container`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Avoid a runtime cycle: container imports AttemptService, which
    # imports the jobs registry, which imports the timer jobs, which
    # import this module. Container is only referenced in annotations
    # here, so deferring keeps the dep graph acyclic.
    from app.core.container import Container


_container: Container | None = None


def set_runtime_container(container: Container) -> None:
    """Install the live container so scheduled jobs can reach process resources."""
    global _container
    _container = container


def get_runtime_container() -> Container:
    """Return the installed container; raise if startup forgot to install it."""
    if _container is None:
        raise RuntimeError(
            "Jobs runtime container not initialized — "
            "call app.jobs._runtime.set_runtime_container(...) at startup."
        )
    return _container


def reset_runtime_container() -> None:
    """Clear the holder. Tests only."""
    global _container
    _container = None
