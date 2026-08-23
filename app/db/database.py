from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.core.config import settings


# ── Engine ────────────────────────────────────────────────────────────────────
# The engine is the core connection to PostgreSQL.
# - pool_pre_ping=True: before each query, SQLAlchemy checks if the connection
#   is still alive and reconnects if it dropped (important for long-running apps).
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)

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


# ── Dependency for FastAPI routes ─────────────────────────────────────────────
def get_db():
    """
    FastAPI dependency injection: yields a DB session for the duration of
    one HTTP request, then closes it automatically.

    Usage in a route:
        @router.get("/items")
        def list_items(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
