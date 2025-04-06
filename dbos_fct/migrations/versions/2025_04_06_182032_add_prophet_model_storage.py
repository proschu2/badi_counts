"""Add prophet model storage.

Revision ID: 2025_04_06_182032
Revises: c6b516e182b2
Create Date: 2025-04-06 18:20:32.500040
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2025_04_06_182032"
down_revision: Union[str, None] = "c6b516e182b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prophet_models",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("model_type", sa.String(), nullable=False),  # e.g., 'badi_predictions'
        sa.Column("model_data", sa.LargeBinary(), nullable=False),  # serialized model
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("last_training_date", sa.Date(), nullable=False),  # track last training date
        sa.Column("metadata", sa.JSON(), nullable=True),  # optional metadata
        sa.PrimaryKeyConstraint("id")
    )
    
    # Index for faster lookups
    op.create_index("idx_prophet_models_type", "prophet_models", ["model_type"])

    # Trigger to update updated_at timestamp
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ language 'plpgsql';
    """)

    op.execute("""
        CREATE TRIGGER update_prophet_models_updated_at
            BEFORE UPDATE ON prophet_models
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS update_prophet_models_updated_at ON prophet_models")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at_column()")
    op.drop_index("idx_prophet_models_type")
    op.drop_table("prophet_models")
