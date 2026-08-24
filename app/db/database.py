from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.core.config import settings


# ── Engine ────────────────────────────────────────────────────────────────────
# The engine is the core connection to PostgreSQL.
# - pool_pre_ping=True: before each query, SQLAlchemy checks if the connection
#   is still alive and reconnects if it dropped (important for long-running apps).
db_url = settings.database_url
try:
    if db_url.startswith("sqlite"):
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(db_url, pool_pre_ping=True)
except Exception:
    # Fallback to local SQLite if PostgreSQL container is down
    engine = create_engine("sqlite:///./evalplatform.db", connect_args={"check_same_thread": False})

# ── Session Factory ───────────────────────────────────────────────────────────
# SessionLocal is a factory: calling SessionLocal() creates a new DB session.
# - autocommit=False: we control when to commit (safer — we can roll back on error)
# - autoflush=False:  we control when pending changes are flushed to the DB
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ── Base Class for All Models ─────────────────────────────────────────────────
# Every SQLAlchemy model (table) will inherit from this Base.
# When we call Base.metadata.create_all(engine), SQLAlchemy creates all
# tables that inherit from Base if they don't already exist.
class Base(DeclarativeBase):
    pass


from sqlalchemy import text

def get_db():
    """
    FastAPI dependency injection: yields a DB session for the duration of
    one HTTP request, then closes it automatically. Handles automatic SQLite fallback if PostgreSQL is offline.
    """
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        try:
            yield db
        finally:
            db.close()
    except Exception:
        sqlite_engine = create_engine("sqlite:///./evalplatform.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=sqlite_engine)
        FallbackSession = sessionmaker(autocommit=False, autoflush=False, bind=sqlite_engine)
        db = FallbackSession()
        try:
            yield db
        finally:
            db.close()
