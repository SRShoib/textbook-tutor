"""
What: request/response shapes for the auth router.
Why UserRead never carries password_hash: it's a from_attributes model built
      by hand-listing fields (not by mirroring every User column), so a new
      sensitive column added to the User model later doesn't leak into an
      API response just because someone forgot to exclude it here.
Why the refresh token never appears in a response body: it only ever
      travels as the httpOnly cookie api/auth.py sets directly — putting it
      in JSON too would give a XSS-reachable script the same power the
      httpOnly flag is meant to deny it.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str
    grade: int = Field(ge=1, le=12)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    role: UserRole
    grade: int
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserRead
