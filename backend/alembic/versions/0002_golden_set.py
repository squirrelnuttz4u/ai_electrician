"""Golden set + accuracy scoring.

Adds the three things needed to measure extraction quality rather than guess at it:

  print_pages.reviewed  - a tech asserts "this page is now fully correct", which
                          turns that page into ground truth.
  components.origin     - distinguishes what the model extracted from what a tech
  wires.origin            added by hand. Without it, recall is unmeasurable: you
                          cannot count what the model missed.

Deletions are recorded in the existing `corrections` table (corrected = NULL),
so a rejected entity leaves evidence instead of vanishing.

Revision ID: 0002_golden_set
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_golden_set"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "print_pages",
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_print_pages_reviewed", "print_pages", ["reviewed"])

    for table in ("components", "wires"):
        op.add_column(
            table,
            sa.Column("origin", sa.String(length=20), nullable=False,
                      server_default="extracted"),  # extracted | manual
        )
        op.create_index(f"ix_{table}_origin", table, ["origin"])


def downgrade() -> None:
    for table in ("components", "wires"):
        op.drop_index(f"ix_{table}_origin", table_name=table)
        op.drop_column(table, "origin")
    op.drop_index("ix_print_pages_reviewed", table_name="print_pages")
    op.drop_column("print_pages", "reviewed")
