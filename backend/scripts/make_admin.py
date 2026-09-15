"""
What: one-off CLI to promote an existing account to role=admin.
Why: POST /auth/register always creates role=student accounts (must stay
     that way -- public registration should never be able to mint an
     admin), so there is no way through the API to create the first admin.
     This script is that bootstrap, run once locally against the real
     database, the same way eval/runner.py is run as a script rather than
     through the API.

Usage:
      python -m scripts.make_admin you@example.com
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.models.user import User, UserRole


async def make_admin(email: str) -> None:
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == email))
        if user is None:
            raise SystemExit(f"No account with email {email!r}. Register the account first, then run this again.")

        user.role = UserRole.ADMIN
        await db.commit()
        print(f"{email} is now an admin.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email", help="Email of an existing account to promote to admin.")
    args = parser.parse_args()
    asyncio.run(make_admin(args.email))


if __name__ == "__main__":
    main()
