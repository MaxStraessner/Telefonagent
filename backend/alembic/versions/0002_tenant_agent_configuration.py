"""Add versioned tenant agent configuration.

Revision ID: 0002
Revises: 0001
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def tenant_index(table: str) -> None:
    op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])


def upgrade() -> None:
    op.create_table(
        "app_users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(150), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_app_users_email", "app_users", ["email"], unique=True)
    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_users.id"), nullable=False),
        sa.Column("role", sa.Enum("owner", "admin", "member", name="tenantrole", native_enum=False), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_membership"),
    )
    tenant_index("tenant_memberships")
    op.create_index("ix_tenant_memberships_user_id", "tenant_memberships", ["user_id"])

    op.create_table(
        "agent_configurations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("assistant_name", sa.String(100), nullable=False),
        sa.Column("assistant_role", sa.String(200), nullable=False),
        sa.Column("transparency_notice", sa.Text(), nullable=False),
        sa.Column("address_formality", sa.Enum("formal", "informal", name="addressformality", native_enum=False), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("standard_greeting", sa.Text(), nullable=False),
        sa.Column("outside_hours_greeting", sa.Text(), nullable=False),
        sa.Column("test_greeting", sa.Text(), nullable=False),
        sa.Column("farewell", sa.Text(), nullable=False),
        sa.Column("voice", sa.String(50), nullable=False),
        sa.Column("speech_speed", sa.Float(), nullable=False),
        sa.Column("pronunciation_instructions", sa.Text(), nullable=False),
        sa.Column("pronunciation_style", sa.String(50), nullable=False),
        sa.Column("regional_accent", sa.String(50), nullable=False),
        sa.Column("tone", sa.String(100), nullable=False),
        sa.Column("custom_style_instructions", sa.Text(), nullable=False),
        sa.Column("response_length", sa.Enum("very_short", "short", "balanced", "detailed", name="responselength", native_enum=False), nullable=False),
        sa.Column("question_style", sa.String(50), nullable=False),
        sa.Column("turn_detection_type", sa.Enum("server_vad", "semantic_vad", name="turndetectiontype", native_enum=False), nullable=False),
        sa.Column("turn_eagerness", sa.Enum("low", "medium", "high", name="turneagerness", native_enum=False), nullable=False),
        sa.Column("vad_threshold", sa.Float(), nullable=False),
        sa.Column("prefix_padding_ms", sa.Integer(), nullable=False),
        sa.Column("silence_duration_ms", sa.Integer(), nullable=False),
        sa.Column("interruptions_enabled", sa.Boolean(), nullable=False),
        sa.Column("idle_prompt_enabled", sa.Boolean(), nullable=False),
        sa.Column("idle_timeout_ms", sa.Integer(), nullable=False),
        sa.Column("primary_task", sa.Text(), nullable=False),
        sa.Column("off_topic_behavior", sa.Text(), nullable=False),
        sa.Column("off_topic_mode", sa.String(50), nullable=False),
        sa.Column("uncertainty_behavior", sa.Text(), nullable=False),
        sa.Column("uncertainty_modes", sa.JSON(), nullable=False),
        sa.Column("fallback_message", sa.Text(), nullable=False),
        sa.Column("simple_mode", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), sa.ForeignKey("app_users.id"), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", name="uq_agent_configurations_tenant_id"),
    )
    tenant_index("agent_configurations")

    op.create_table(
        "agent_topics",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("label", sa.String(150), nullable=False),
        sa.Column("topic_type", sa.String(20), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        *timestamps(),
    )
    tenant_index("agent_topics")
    op.create_table(
        "agent_behavior_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("rule_text", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        *timestamps(),
    )
    tenant_index("agent_behavior_rules")
    op.create_table(
        "agent_knowledge_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("company_description", sa.Text(), nullable=False),
        sa.Column("products", sa.Text(), nullable=False),
        sa.Column("locations", sa.Text(), nullable=False),
        sa.Column("important_notes", sa.Text(), nullable=False),
        sa.Column("contact_phone", sa.String(50), nullable=False),
        sa.Column("contact_email", sa.String(320), nullable=False),
        sa.Column("website", sa.String(500), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", name="uq_agent_knowledge_profiles_tenant_id"),
    )
    tenant_index("agent_knowledge_profiles")

    op.create_table(
        "agent_faqs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("question", sa.String(300), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        *timestamps(),
    )
    tenant_index("agent_faqs")
    op.create_table(
        "agent_knowledge_services",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price_information", sa.String(150), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        *timestamps(),
    )
    tenant_index("agent_knowledge_services")
    op.create_table(
        "agent_business_hours",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("opens_at", sa.String(5), nullable=False),
        sa.Column("closes_at", sa.String(5), nullable=False),
        sa.Column("is_closed", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "weekday", name="uq_agent_business_hours_day"),
    )
    tenant_index("agent_business_hours")
    op.create_table(
        "agent_capabilities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("capability_key", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "capability_key", name="uq_agent_capability"),
    )
    tenant_index("agent_capabilities")
    op.create_table(
        "agent_configuration_audits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changed_by_user_id", sa.Uuid(), sa.ForeignKey("app_users.id"), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    tenant_index("agent_configuration_audits")
    op.add_column("call_sessions", sa.Column("configuration_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("call_sessions", "configuration_version")
    for table in (
        "agent_configuration_audits", "agent_capabilities", "agent_business_hours",
        "agent_knowledge_services", "agent_faqs", "agent_knowledge_profiles",
        "agent_behavior_rules", "agent_topics", "agent_configurations",
        "tenant_memberships", "app_users",
    ):
        op.drop_table(table)
