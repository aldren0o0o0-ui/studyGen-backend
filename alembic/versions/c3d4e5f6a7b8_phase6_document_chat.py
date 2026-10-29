"""Phase 6 persistent document chat.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("document_scope_mode", sa.String(30), nullable=False),
        sa.Column("system_mode", sa.String(30), nullable=False, server_default="document_qa"),
        sa.Column("message_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("regeneration_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_message_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
        sa.CheckConstraint("message_count >= 0", name="ck_chat_session_message_count"),
        sa.CheckConstraint("regeneration_count >= 0", name="ck_chat_session_regeneration_count"),
    )
    op.create_index("ix_chat_sessions_status", "chat_sessions", ["status"])
    op.create_index("ix_chat_sessions_last_message_at", "chat_sessions", ["last_message_at"])
    op.create_index("ix_chat_sessions_created_at", "chat_sessions", ["created_at"])

    op.create_table(
        "chat_session_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("chat_session_id", sa.String(36), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("chat_session_id", "document_id", name="uq_chat_session_document"),
        sa.UniqueConstraint("chat_session_id", "position", name="uq_chat_session_document_position"),
    )
    op.create_index("ix_chat_session_documents_session", "chat_session_documents", ["chat_session_id"])
    op.create_index("ix_chat_session_documents_document", "chat_session_documents", ["document_id"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("chat_session_id", sa.String(36), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("sequence_number", sa.Integer, nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=True),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("input_token_estimate", sa.Integer, nullable=True),
        sa.Column("output_token_estimate", sa.Integer, nullable=True),
        sa.Column("follow_up_suggestions_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("insufficient_evidence", sa.Integer, nullable=False, server_default="0"),
        sa.Column("supersedes_message_id", sa.String(36), sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("chat_session_id", "sequence_number", name="uq_chat_message_sequence"),
        sa.CheckConstraint("sequence_number >= 1", name="ck_chat_message_sequence"),
    )
    op.create_index("ix_chat_messages_session", "chat_messages", ["chat_session_id"])
    op.create_index("ix_chat_messages_status", "chat_messages", ["status"])

    op.create_table(
        "chat_citations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("page_number", sa.Integer, nullable=True),
        sa.Column("section_label", sa.String(255), nullable=True),
        sa.Column("excerpt", sa.String(500), nullable=False),
        sa.Column("source_order", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("message_id", "document_id", "chunk_index", name="uq_chat_citation_chunk"),
        sa.UniqueConstraint("message_id", "source_order", name="uq_chat_citation_order"),
    )
    op.create_index("ix_chat_citations_message", "chat_citations", ["message_id"])
    op.create_index("ix_chat_citations_document", "chat_citations", ["document_id"])

    op.create_table(
        "chat_generation_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("assistant_message_id", sa.String(36), sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("retrieval_query", sa.String(1000), nullable=False),
        sa.Column("retrieved_chunk_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("context_character_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("generation_status", sa.String(20), nullable=False),
        sa.Column("failure_code", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_chat_generation_message", "chat_generation_records", ["assistant_message_id"])
    op.create_index("ix_chat_generation_status", "chat_generation_records", ["generation_status"])

    op.add_column("study_activities", sa.Column("related_chat_session_id", sa.String(36), nullable=True))
    op.create_index("ix_study_activities_chat_session", "study_activities", ["related_chat_session_id"])


def downgrade() -> None:
    op.drop_index("ix_study_activities_chat_session", table_name="study_activities")
    with op.batch_alter_table("study_activities") as batch:
        batch.drop_column("related_chat_session_id")
    op.drop_table("chat_generation_records")
    op.drop_table("chat_citations")
    op.drop_table("chat_messages")
    op.drop_table("chat_session_documents")
    op.drop_table("chat_sessions")
