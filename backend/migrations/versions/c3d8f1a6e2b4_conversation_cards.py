"""Conversation cards: her sharing preference, private turns, the cards
and the child's questions

Revision ID: c3d8f1a6e2b4
Revises: a7c2e9f4b8d1

docs/specs/CONVERSATION_CARDS.md §11. Every new column has a default that
means "as today": cards off for every family, nothing private, nothing
asked. So migrating changes no family's behaviour; an operator turns
cards on one family at a time (scripts/cards.py).

conversation_cards carries a CHECK constraint that a card can only be
approved, scheduled or sent with her "approve" on its exact current text
(§4.1). It is the backstop under every code path that sends.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "c3d8f1a6e2b4"
down_revision = "a7c2e9f4b8d1"
branch_labels = None
depends_on = None

CARD_STATUSES = (
    "draft",
    "operator_review",
    "rejected",
    "awaiting_parent",
    "correction",
    "approved",
    "kept_private",
    "expired",
    "withdrawn",
    "failed_fidelity",
    "scheduled",
    "sent",
)


def _in(column: str, values) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _timestamps():
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def upgrade() -> None:
    # user_profiles
    op.add_column(
        "user_profiles",
        sa.Column(
            "conversation_cards_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "user_profiles",
        sa.Column(
            "share_preference",
            sa.String(),
            nullable=False,
            server_default="not_asked",
        ),
    )
    op.create_check_constraint(
        "ck_user_profiles_share_preference",
        "user_profiles",
        _in(
            "share_preference",
            ("not_asked", "share_with_approval", "unsure", "never"),
        ),
    )
    op.add_column(
        "user_profiles",
        sa.Column(
            "share_preference_asks", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "user_profiles",
        sa.Column(
            "private_topics",
            ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "user_profiles",
        sa.Column(
            "card_whatsapp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )

    # family_accounts
    op.add_column("family_accounts", sa.Column("child_name", sa.String()))
    op.add_column("family_accounts", sa.Column("parent_title", sa.String()))
    op.add_column("family_accounts", sa.Column("timezone", sa.String()))
    op.add_column(
        "family_accounts",
        sa.Column("call_slots", JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "family_accounts",
        sa.Column(
            "card_email_enabled", sa.Boolean(), nullable=False, server_default="true"
        ),
    )

    # turns
    op.add_column(
        "turns",
        sa.Column("is_private", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("turns", sa.Column("private_topic", sa.Text()))

    # sessions
    op.add_column("sessions", sa.Column("pending_ask", sa.String()))
    op.add_column("sessions", sa.Column("pending_ask_ref", UUID(as_uuid=True)))
    op.add_column(
        "sessions",
        sa.Column("ask_made", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("sessions", sa.Column("ask_outcome", sa.String()))

    op.create_table(
        "conversation_cards",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False, index=True),
        *_timestamps(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column(
            "source_atom_ids",
            ARRAY(UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "source_turn_ids",
            ARRAY(UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("source_cutoff", sa.DateTime(timezone=True)),
        sa.Column("answers_question_id", UUID(as_uuid=True)),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("hook", sa.Text(), nullable=False, server_default=""),
        sa.Column("excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("original_quote", sa.Text()),
        sa.Column("original_quote_gloss", sa.Text()),
        sa.Column("call_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("held_back_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_language", sa.String()),
        sa.Column("readback_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("readback_language", sa.String()),
        sa.Column("readback_hash", sa.String()),
        sa.Column("approved_hash", sa.String()),
        sa.Column("fidelity_json", JSONB(), nullable=False, server_default="{}"),
        sa.Column("operator_action", sa.String()),
        sa.Column(
            "operator_edited", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("operator_reason", sa.Text()),
        sa.Column("operator_reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("readback_offers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correction_round", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parent_answer", sa.String()),
        sa.Column("parent_answer_turn_id", UUID(as_uuid=True)),
        sa.Column("parent_answered_at", sa.DateTime(timezone=True)),
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
        sa.Column("image_s3_key", sa.String()),
        sa.Column("wa_message_sid", sa.String(), index=True),
        sa.Column("wa_delivery_status", sa.String()),
        sa.Column("wa_error_code", sa.String()),
        sa.Column("wa_delivered_at", sa.DateTime(timezone=True)),
        sa.Column("wa_skipped_reason", sa.String()),
        sa.Column("email_message_id", sa.String()),
        sa.Column("email_status", sa.String()),
        sa.Column("email_sent_at", sa.DateTime(timezone=True)),
        sa.Column("child_reaction", sa.String()),
        sa.Column("child_reaction_at", sa.DateTime(timezone=True)),
        sa.Column("child_reaction_channel", sa.String()),
        sa.Column("call_tag", sa.String()),
        sa.CheckConstraint(
            _in("status", CARD_STATUSES), name="ck_conversation_cards_status"
        ),
        # COALESCE: a CHECK that evaluates to NULL passes, so without it a
        # card with no answer at all would satisfy this.
        sa.CheckConstraint(
            "status NOT IN ('approved', 'scheduled', 'sent') OR COALESCE("
            "parent_answer = 'approve' AND approved_hash = readback_hash, false)",
            name="ck_conversation_cards_parent_approved",
        ),
    )

    op.create_table(
        "family_questions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False, index=True),
        sa.Column("card_id", UUID(as_uuid=True)),
        *_timestamps(),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False, server_default="question"),
        sa.Column("raw_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("transcript", sa.Text(), nullable=False, server_default=""),
        sa.Column("screening", sa.String()),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("asked_in_session_id", UUID(as_uuid=True)),
        sa.Column("asked_at", sa.DateTime(timezone=True)),
        sa.Column("inbound_message_sid", sa.String(), unique=True),
        sa.CheckConstraint(
            _in("channel", ("whatsapp", "email")), name="ck_family_questions_channel"
        ),
        sa.CheckConstraint(
            _in("kind", ("question", "reaction")), name="ck_family_questions_kind"
        ),
        sa.CheckConstraint(
            "screening IS NULL OR "
            + _in("screening", ("ok", "sensitive", "correction", "reaction", "other")),
            name="ck_family_questions_screening",
        ),
        sa.CheckConstraint(
            _in(
                "status",
                (
                    "pending",
                    "held",
                    "released",
                    "dropped",
                    "asked",
                    "expired",
                    "not_relayed",
                ),
            ),
            name="ck_family_questions_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("family_questions")
    op.drop_table("conversation_cards")
    for column in ("ask_outcome", "ask_made", "pending_ask_ref", "pending_ask"):
        op.drop_column("sessions", column)
    for column in ("private_topic", "is_private"):
        op.drop_column("turns", column)
    for column in (
        "card_email_enabled",
        "call_slots",
        "timezone",
        "parent_title",
        "child_name",
    ):
        op.drop_column("family_accounts", column)
    op.drop_constraint(
        "ck_user_profiles_share_preference", "user_profiles", type_="check"
    )
    for column in (
        "card_whatsapp_enabled",
        "private_topics",
        "share_preference_asks",
        "share_preference",
        "conversation_cards_enabled",
    ):
        op.drop_column("user_profiles", column)
