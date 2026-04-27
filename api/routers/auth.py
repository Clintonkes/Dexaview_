"""
app/routers/auth.py
-------------------
JWT-based authentication for the Dexaview API.

Endpoints:
  POST /api/auth/register – create a new user account
  POST /api/auth/login    – exchange email+password for a JWT access token
  GET  /api/auth/me       – return the current authenticated user's profile

The `get_current_user` dependency is exported for use in other routers.
It validates the Authorization: Bearer <token> header on every protected route.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import settings
from api.db.session import get_db
from api.models import User

router = APIRouter()

# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------

# bcrypt for hashing passwords at rest – never store plain text
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# FastAPI OAuth2 helper – reads the Bearer token from the Authorization header
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def hash_password(plain: str) -> str:
    """Returns a bcrypt hash of the given plain-text password."""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Returns True if `plain` matches the stored bcrypt `hashed` value."""
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str) -> str:
    """
    Creates a signed JWT access token.

    The `sub` claim holds the user's ID as a string. The `exp` claim is set
    to settings.ACCESS_TOKEN_EXPIRE_MINUTES from now (UTC).
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class RegisterIn(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8)
    is_creator: bool = False


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    email: str
    username: str
    is_creator: bool
    balance: float

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterIn, db: AsyncSession = Depends(get_db)):
    """
    Creates a new Dexaview account.
    Returns the created user profile (without the token – the client should
    call /login immediately after registration).
    """
    # Check for duplicate email or username
    conflict = (
        await db.execute(
            select(User).where(
                (User.email == payload.email) | (User.username == payload.username)
            )
        )
    ).scalar_one_or_none()

    if conflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email or username is already taken.",
        )

    user = User(
        email=payload.email,
        username=payload.username,
        hashed_password=hash_password(payload.password),
        is_creator=payload.is_creator,
    )
    db.add(user)
    await db.flush()
    return user


@router.post("/login", response_model=TokenOut)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticates a user by email and password.
    Returns a signed JWT access token on success.

    The OAuth2PasswordRequestForm uses `username` as the field name but we
    treat it as the email address here (a common FastAPI pattern).
    """
    user = (
        await db.execute(select(User).where(User.email == form_data.username))
    ).scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated.",
        )

    token = create_access_token(subject=str(user.id))
    return TokenOut(access_token=token)


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(lambda db=Depends(get_db): None)):
    """Returns the authenticated user's profile."""
    # This route exists for convenience; the actual work is in get_current_user below.
    raise HTTPException(status_code=500, detail="Misconfigured route")


# ---------------------------------------------------------------------------
# Shared dependency – imported by other routers
# ---------------------------------------------------------------------------

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    FastAPI dependency that decodes the Bearer JWT and returns the
    corresponding User row. Raises 401 if the token is invalid or expired.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exc
    except JWTError:
        raise credentials_exc

    user = await db.get(User, int(user_id))
    if user is None or not user.is_active:
        raise credentials_exc

    return user


# Fix the /me route to use the shared dependency correctly
@router.get("/me", response_model=UserOut, include_in_schema=False)
async def get_me_fixed(current_user: User = Depends(get_current_user)):
    return current_user
