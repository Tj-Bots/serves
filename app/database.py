import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

logger = logging.getLogger("serves.database")

connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _auto_migrate() -> None:
    """מיגרציה מינימלית: מוסיף עמודות שחסרות בטבלאות קיימות (SQLite בלבד,
    additive only - לא מוחק/משנה עמודות). כך אין צורך למחוק את כל ה-DB
    בכל פעם שמתווספת עמודה חדשה למודל."""
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            col_type = column.type.compile(dialect=engine.dialect)
            logger.warning("migrating: adding column %s.%s (%s)", table.name, column.name, col_type)
            with engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'))
                # ALTER TABLE ADD COLUMN לא מפעיל את ה-default הפייתוני של
                # SQLAlchemy על שורות קיימות (הוא חל רק על INSERT דרך ה-ORM) -
                # בלי זה, עמודות עם default סקלרי (למשל plan="free") יישארו
                # NULL אצל משתמשים קיימים. מבצעים backfill רק לברירות מחדל
                # סקלריות פשוטות (לא callable כמו utcnow).
                default = column.default
                if default is not None and getattr(default, "is_scalar", False):
                    conn.execute(
                        text(f'UPDATE "{table.name}" SET "{column.name}" = :value WHERE "{column.name}" IS NULL'),
                        {"value": default.arg},
                    )


def _backfill_bot_app_slugs() -> None:
    from app.models import BotApp
    from app.slugs import slugify

    db = SessionLocal()
    try:
        apps = db.query(BotApp).filter((BotApp.slug.is_(None)) | (BotApp.slug == "")).all()
        for app in apps:
            app.slug = f"{slugify(app.name)}-{app.id}"
        if apps:
            db.commit()
    finally:
        db.close()


def init_db():
    from app import models  # noqa: F401  (register models on Base.metadata)

    Base.metadata.create_all(bind=engine)
    _auto_migrate()
    _backfill_bot_app_slugs()
