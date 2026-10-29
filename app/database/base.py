"""
database/base.py — Shared declarative base for all SQLAlchemy models.
Import this in every model file. Never import models here (avoids circular imports).
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
