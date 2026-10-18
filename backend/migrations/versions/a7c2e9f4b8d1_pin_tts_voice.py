"""Pin each parent's voice, and record every change to it

Revision ID: a7c2e9f4b8d1
Revises: e4b1c9a7d352

docs/specs/GEMINI_TTS.md §4.2. user_profiles.tts_voice holds the voice a
parent hears, as "vendor/model/speaker", so a later change of default
(TTS_VENDOR, TTS_MODEL, the voice table) only reaches new families.

Every existing profile is pinned to the Sarvam voice it hears today: the
media/voices.py rule under the TTS_MODEL this deploy runs with. Nobody's
voice changes by migrating. Moving them to Gemini is scripts/set_voice.py,
one family at a time, which writes a voice_changes row each time.
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from media import voices

revision = "a7c2e9f4b8d1"
down_revision = "e4b1c9a7d352"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("tts_voice", sa.String(), nullable=True))

    # TTS_MODEL from the environment, as config.py would read it, without
    # importing config (which needs every secret set just to migrate).
    tts_model = os.environ.get("TTS_MODEL", voices.V4_FLASH_MODEL)
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, preferred_language, katha_gender FROM user_profiles")
    ).fetchall()
    for row in rows:
        pin = voices.voice_for(row.preferred_language, row.katha_gender, tts_model).pin
        bind.execute(
            sa.text("UPDATE user_profiles SET tts_voice = :pin WHERE id = :id"),
            {"pin": pin, "id": row.id},
        )
    op.alter_column("user_profiles", "tts_voice", nullable=False)

    op.create_table(
        "voice_changes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("user_id", sa.String(), nullable=False, index=True),
        sa.Column("from_voice", sa.String(), nullable=False),
        sa.Column("to_voice", sa.String(), nullable=False),
        sa.Column("changed_by", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("voice_changes")
    op.drop_column("user_profiles", "tts_voice")
