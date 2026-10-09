"""RuntimeSupervisor protocol injected into the API, plus the NullSupervisor used when no runtime is configured."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from service015.db import connect
from service015.errors import DependencyUnavailable
from service015.identities_repo import get_identity
from service015.participants import participant_status


@runtime_checkable
class RuntimeSupervisor(Protocol):
    """Each method returns a ParticipantStatus-shaped dict (see openapi.yaml)."""

    async def launch(self, identity_id: str) -> dict[str, Any]: ...

    async def stop(self, identity_id: str) -> dict[str, Any]: ...

    async def status(self, identity_id: str) -> dict[str, Any]: ...


class NullSupervisor:
    """No runtime configured: launch/stop fail with 503; status is read from the database."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = db_path

    async def launch(self, identity_id: str) -> dict[str, Any]:
        raise DependencyUnavailable("dependency_unavailable", "runtime not configured")

    async def stop(self, identity_id: str) -> dict[str, Any]:
        raise DependencyUnavailable("dependency_unavailable", "runtime not configured")

    async def status(self, identity_id: str) -> dict[str, Any]:
        conn = connect(self._db_path)
        try:
            return participant_status(conn, get_identity(conn, identity_id))
        finally:
            conn.close()
