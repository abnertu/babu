from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RunRecord(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("source", "event_id"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    principal_id: Mapped[str] = mapped_column(String, index=True)
    session_id: Mapped[str] = mapped_column(String, index=True)
    source: Mapped[str] = mapped_column(String)
    event_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    state_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[float] = mapped_column(Float)


class ExecutionRecord(Base):
    __tablename__ = "executions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String)
    writes: Mapped[bool] = mapped_column(Boolean)
    arguments_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String)
    result_json: Mapped[str | None] = mapped_column(Text)


class EventRecord(Base):
    __tablename__ = "run_events"
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String)
    created_at: Mapped[float] = mapped_column(Float)
    data_json: Mapped[str] = mapped_column(Text)


class SessionLease(Base):
    __tablename__ = "session_leases"
    __table_args__ = (UniqueConstraint("principal_id", "session_id"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    principal_id: Mapped[str] = mapped_column(String)
    session_id: Mapped[str] = mapped_column(String)
    owner: Mapped[str] = mapped_column(String)
    expires_at: Mapped[float] = mapped_column(Float)
