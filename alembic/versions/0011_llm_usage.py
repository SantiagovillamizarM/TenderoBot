"""llm usage log: tokens, cost and latency of every Grok call, grouped by task

Revision ID: 0011_llm_usage
Revises: 0010_admin_and_providers
Create Date: 2026-11-06 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_llm_usage"
down_revision: Union[str, Sequence[str], None] = "0010_admin_and_providers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("task", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id", ondelete="SET NULL"), nullable=True),
        sa.Column("endpoint", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_ticks", sa.BigInteger(), nullable=True),
        sa.Column("cost_estimated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_llm_usage_created", "llm_usage", ["created_at"])
    op.create_index("ix_llm_usage_task_id", "llm_usage", ["task_id"])


def downgrade() -> None:
    op.drop_table("llm_usage")
