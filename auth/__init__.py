"""protoVoice auth — API-key-based user identity."""

from .users import (
    AuthError,
    User,
    UserRegistry,
    load_users,
    require_user,
    single_user_fallback,
    user_registry,
)

__all__ = [
    "AuthError",
    "User",
    "UserRegistry",
    "load_users",
    "require_user",
    "single_user_fallback",
    "user_registry",
]
