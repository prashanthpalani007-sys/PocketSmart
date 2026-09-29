"""SQLite database: users and recommendation history."""
import json
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

engine = create_engine("sqlite:///./pocketsmart.db", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    hashed_password = Column(String(200), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    history = relationship("History", back_populates="user")


class History(Base):
    __tablename__ = "history"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    category = Column(String(20), nullable=False)  # home | party | jewelry
    inputs = Column(Text, nullable=False)          # JSON
    result = Column(Text, nullable=False)          # JSON
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="history")

    @property
    def inputs_data(self):
        return json.loads(self.inputs)

    @property
    def result_data(self):
        return json.loads(self.result)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
