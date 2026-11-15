"""Composition-root logging posture.

db_lifespan runs Alembic's ``fileConfig(alembic.ini)``, which pins the root
logger to WARN — swallowing every octave INFO line (MCP fleet summary,
server state transitions) in a plain ``uv run uvicorn octave.app:app`` boot.
The app pins the ``octave`` subtree to INFO so plane logs survive through
whatever handler the root ends up with (explicit levels are not inherited).
"""

import logging


def test_octave_logger_is_pinned_to_info() -> None:
    import octave.app  # noqa: F401 - the import side effect IS the subject

    assert logging.getLogger("octave").level == logging.INFO
