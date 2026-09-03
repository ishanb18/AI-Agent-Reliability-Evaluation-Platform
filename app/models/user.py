import secrets
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Boolean
from app.db.database import Base

class User(Base):
    """
    User account model for authentication, user workspace data isolation, and API keys.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    api_key = Column(String(64), unique=True, index=True, nullable=False, default=lambda: f"ant_{secrets.token_hex(24)}")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "api_key": self.api_key,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
