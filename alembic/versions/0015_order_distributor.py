"""orders know their provider: one order per provider for stores with several

Revision ID: 0015_order_distributor
Revises: 0014_store_product_choices
Create Date: 2026-11-07 11:00:00.000000

Existing orders are assigned to their store's main provider.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_order_distributor"
down_revision: Union[str, Sequence[str], None] = "0014_store_product_choices"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "suggested_orders",
        sa.Column("distributor_id", sa.Integer(), sa.ForeignKey("distributors.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_suggested_orders_distributor_id", "suggested_orders", ["distributor_id"])
    op.execute(
        "UPDATE suggested_orders o SET distributor_id = s.distributor_id FROM stores s WHERE s.id = o.store_id"
    )


def downgrade() -> None:
    op.drop_index("ix_suggested_orders_distributor_id", table_name="suggested_orders")
    op.drop_column("suggested_orders", "distributor_id")
