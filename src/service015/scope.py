"""Visibility of owned resources (templates, voices) for a principal."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Visibility:
    """`restricted=False` sees everything; otherwise shared resources plus those owned by `user_id`."""

    restricted: bool = False
    user_id: str | None = None

    def allows(self, owner_user_id: str | None, shared: bool) -> bool:
        return not self.restricted or shared or (self.user_id is not None and owner_user_id == self.user_id)


UNRESTRICTED = Visibility()
