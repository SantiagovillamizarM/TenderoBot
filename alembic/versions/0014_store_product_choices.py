"""store product choices: which provider's product the tendero prefers for an item sold by several

Revision ID: 0014_store_product_choices
Revises: 0013_bot_registrations
Create Date: 2026-11-07 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_store_product_choices"
down_revision: Union[str, Sequence[str], None] = "0013_bot_registrations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "store_product_choices",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chosen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("store_id", "product_id", name="uq_store_product_choices_store_product"),
    )
    op.create_index("ix_store_product_choices_store_id", "store_product_choices", ["store_id"])


def downgrade() -> None:
    op.drop_table("store_product_choices")
