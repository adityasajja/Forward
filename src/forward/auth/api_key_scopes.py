"""Parse the permission scopes stored on an API key."""

from __future__ import annotations

from forward.auth.rbac import Permission


def resolve_api_key_scopes(value: str) -> tuple[set[Permission], list[str]]:
    """Keep known API-key scopes and report unknown stored values.

    Unknown values are omitted so a removed permission can never grant access.
    The caller can log them without making logging part of this policy.

    Args:
        value: Comma-separated scopes stored with an API key.

    Returns:
        The recognized permissions and unknown scope names.
    """
    permissions: set[Permission] = set()
    unknown: list[str] = []
    for raw_scope in value.split(","):
        raw_scope = raw_scope.strip()
        if not raw_scope:
            continue
        try:
            permissions.add(Permission.parse(raw_scope))
        except ValueError:
            unknown.append(raw_scope)
    return permissions, unknown
