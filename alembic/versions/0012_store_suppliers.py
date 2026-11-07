"""store suppliers: a store can work with several providers, each with its own status and days

Revision ID: 0012_store_suppliers
Revises: 0011_llm_usage
Create Date: 2026-11-06 18:00:00.000000

Each existing store keeps its provider as an approved supplier, with the same route days, delivery
days, lead time and reminder state. stores.distributor_id becomes nullable: a tendero who signs up
in Telegram has a store before having any provider.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_store_suppliers"
down_revision: Union[str, Sequence[str], None] = "0011_llm_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "store_suppliers",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("distributor_id", sa.Integer(), sa.ForeignKey("distributors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(length=300), nullable=True),
        sa.Column("decision_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("route_weekdays", sa.String(length=32), nullable=True),
        sa.Column("visit_weekdays", sa.String(length=32), nullable=True),
        sa.Column("lead_time_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("delivery_days_set_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reminder_for", sa.Date(), nullable=True),
        sa.UniqueConstraint("store_id", "distributor_id", name="uq_store_suppliers_store_distributor"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_store_suppliers_status"),
    )
    op.create_index("ix_store_suppliers_store_id", "store_suppliers", ["store_id"])
    op.create_index("ix_store_suppliers_distributor_status", "store_suppliers", ["distributor_id", "status"])

    # Las tiendas de hoy: su proveedor queda aceptado, con los mismos días (ya se le avisó al tendero).
    op.execute(
        """
        INSERT INTO store_suppliers (store_id, distributor_id, status, requested_at, decided_at,
                                     decision_notified_at, route_weekdays, visit_weekdays, lead_time_days,
                                     delivery_days_set_at, last_reminder_for)
        SELECT id, distributor_id, 'approved', now(), now(), now(), route_weekdays, visit_weekdays,
               lead_time_days, delivery_days_set_at, last_reminder_for
        FROM stores
        WHERE distributor_id IS NOT NULL
        """
    )
    op.alter_column("stores", "distributor_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # Una tienda sin proveedor principal no cabe en el esquema anterior: se le pone el primero que la
    # aceptó; si no tiene ninguno, se borra (solo pasa con tiendas creadas desde Telegram).
    op.execute(
        """
        UPDATE stores s SET distributor_id = (
            SELECT ss.distributor_id FROM store_suppliers ss
            WHERE ss.store_id = s.id AND ss.status = 'approved' ORDER BY ss.decided_at, ss.id LIMIT 1)
        WHERE s.distributor_id IS NULL
        """
    )
    op.execute("DELETE FROM stores WHERE distributor_id IS NULL")
    op.alter_column("stores", "distributor_id", existing_type=sa.Integer(), nullable=False)
    op.drop_table("store_suppliers")
