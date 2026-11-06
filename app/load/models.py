"""Tablas de la semana de carga (S4). Solo existen en la base de carga.

Usan su propio `LoadBase` (no el de app.models), así las migraciones de la app no las crean en la
base real. `scripts.load_db setup` las crea con `create_all`.

- load_runs: una corrida (por ejemplo, la de 12 horas).
- load_tasks: una fila por unidad de trabajo, con su estado y su bitácora (tokens, costo, éxito, error).
- load_heartbeats: señal de vida cada pocos minutos, para demostrar que la corrida fue continua.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Identity, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Estados de una unidad.
PENDING = "pendiente"
RUNNING = "procesando"
SUCCESS = "exito"  # el agente hizo lo correcto
FAILED = "fallo"  # el agente respondió, pero no hizo lo correcto
ERROR = "error"  # no se pudo completar (pasarela, tiempo, código) tras los reintentos
FINAL_STATES = (SUCCESS, FAILED, ERROR)


class LoadBase(DeclarativeBase):
    pass


class LoadRun(LoadBase):
    __tablename__ = "load_runs"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # creada, corriendo, terminada, interrumpida
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="creada")
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class LoadTask(LoadBase):
    __tablename__ = "load_tasks"
    __table_args__ = (
        UniqueConstraint("run_id", "unit_id", name="uq_load_tasks_run_unit"),
        Index("ix_load_tasks_run_status_position", "run_id", "status", "position"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("load_runs.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_id: Mapped[str] = mapped_column(String(32), nullable=False)
    store_index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    expected: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confirm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=PENDING)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Qué hizo el agente.
    tools: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    observed: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    failures: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Errores (si no se pudo completar).
    error_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Consumo de todos los intentos de esta unidad (también los fallidos).
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_ticks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class LoadHeartbeat(LoadBase):
    __tablename__ = "load_heartbeats"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("load_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    done: Mapped[int] = mapped_column(Integer, nullable=False)
    pending: Mapped[int] = mapped_column(Integer, nullable=False)
    success: Mapped[int] = mapped_column(Integer, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, nullable=False)
    errors: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_ticks: Mapped[int] = mapped_column(BigInteger, nullable=False)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
