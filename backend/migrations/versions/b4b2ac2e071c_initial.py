"""initial

Revision ID: b4b2ac2e071c
Revises:
Create Date: 2026-07-05 19:13:49.168786

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "b4b2ac2e071c"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
