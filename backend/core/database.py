import os
import json
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, String, Integer, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.sql import select, update, desc
from sqlalchemy.dialects.postgresql import UUID, JSONB

Base = declarative_base()

class Session(Base):
    __tablename__ = 'sessions'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=True) # References auth.users(id) in Supabase
    status = Column(String, nullable=False, default='initiated')
    raw_file_path = Column(String)
    file_type = Column(String)
    raw_query = Column(String)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class PipelineState(Base):
    __tablename__ = 'pipeline_states'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey('sessions.id', ondelete='CASCADE'))
    agent_name = Column(String, nullable=False)
    state_json = Column(JSONB, nullable=False)
    tokens_used = Column(Integer, default=0)
    checkpoint_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Dashboard(Base):
    __tablename__ = 'dashboards'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey('sessions.id'), unique=True)
    config_json = Column(JSONB, nullable=False)
    title = Column(String)
    published = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class SchemaEmbedding(Base):
    __tablename__ = 'schema_embeddings'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fingerprint = Column(String, unique=True, nullable=False)
    column_metadata = Column(JSONB)
    domain = Column(String)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# --- Database Connection Pool Manager ---
class DatabaseManager:
    def __init__(self):
        self.engine = None
        self.async_session_maker = None

    def init_db(self, db_url: str):
        if db_url and db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            
        self.engine = create_async_engine(
            db_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True
        )
        self.async_session_maker = sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

db = DatabaseManager()

def get_db_session() -> AsyncSession:
    if not db.async_session_maker:
        from dotenv import load_dotenv
        load_dotenv()
        db_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
        db.init_db(db_url)
    return db.async_session_maker()

# --- Async CRUD Operations ---

async def get_session(session_id: str) -> Optional[Session]:
    async with get_db_session() as session:
        result = await session.execute(select(Session).filter_by(id=uuid.UUID(session_id)))
        return result.scalars().first()

async def create_session(user_id: Optional[str], file_path: str, file_type: str, raw_query: str) -> Session:
    async with get_db_session() as session:
        uid = uuid.UUID(user_id) if user_id else None
        new_sess = Session(
            user_id=uid,
            raw_file_path=file_path,
            file_type=file_type,
            raw_query=raw_query
        )
        session.add(new_sess)
        await session.commit()
        await session.refresh(new_sess)
        return new_sess

async def update_session_status(session_id: str, status: str) -> None:
    async with get_db_session() as session:
        await session.execute(
            update(Session)
            .where(Session.id == uuid.UUID(session_id))
            .values(status=status, updated_at=datetime.now(timezone.utc))
        )
        await session.commit()

async def save_pipeline_checkpoint(session_id: str, agent_name: str, state_json: Dict[str, Any], tokens_used: int = 0) -> PipelineState:
    async with get_db_session() as session:
        ps = PipelineState(
            session_id=uuid.UUID(session_id),
            agent_name=agent_name,
            state_json=state_json,
            tokens_used=tokens_used
        )
        session.add(ps)
        await session.commit()
        await session.refresh(ps)
        return ps

async def get_latest_checkpoint(session_id: str) -> Optional[PipelineState]:
    async with get_db_session() as session:
        result = await session.execute(
            select(PipelineState)
            .filter_by(session_id=uuid.UUID(session_id))
            .order_by(desc(PipelineState.checkpoint_at))
            .limit(1)
        )
        return result.scalars().first()

async def save_dashboard(session_id: str, config_json: List[Dict[str, Any]], title: str) -> Dashboard:
    async with get_db_session() as session:
        db_dashboard = Dashboard(
            session_id=uuid.UUID(session_id),
            config_json=config_json,
            title=title
        )
        session.add(db_dashboard)
        await session.commit()
        await session.refresh(db_dashboard)
        return db_dashboard
