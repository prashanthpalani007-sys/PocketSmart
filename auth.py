"""Password hashing, JWT creation/verification and the current-user dependency."""
import os
from datetime import datetime, timedelta

import bcrypt
from fastapi import Depends, Request
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from database import User, get_db

SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-secret-change-me")
ALGORITHM = "HS256"
TOKEN_MINUTES = 60 * 8


class NotAuthenticated(Exception):
    """Raised when a protected page is opened without a valid login."""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except ValueError:
        return False


def create_token(username: str) -> str:
    payload = {"sub": username, "exp": datetime.utcnow() + timedelta(minutes=TOKEN_MINUTES)}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def authenticate(db: Session, username: str, password: str):
    user = db.query(User).filter(User.username == username.strip().lower()).first()
    if user and verify_password(password, user.hashed_password):
        return user
    return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("access_token")
    if not token:
        header = request.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:]
    if not token:
        raise NotAuthenticated()
    try:
        username = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]).get("sub")
    except JWTError:
        raise NotAuthenticated()
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise NotAuthenticated()
    return user
