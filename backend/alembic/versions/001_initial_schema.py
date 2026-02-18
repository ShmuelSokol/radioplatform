"""Initial schema

Revision ID: 001
Revises:
Create Date: 2025-01-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enums
    user_role = postgresql.ENUM("admin", "manager", "viewer", name="user_role", create_type=True)
    user_role.create(op.get_bind(), checkfirst=True)

    station_type = postgresql.ENUM("internet", "ota", "both", name="station_type", create_type=True)
    station_type.create(op.get_bind(), checkfirst=True)

    play_source = postgresql.ENUM("scheduler", "manual", "ad", "fallback", name="play_source", create_type=True)
    play_source.create(op.get_bind(), checkfirst=True)

    # Users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, index=True, nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", user_role, nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Stations
    op.create_table(
        "stations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), unique=True, nullable=False),
        sa.Column("type", station_type, nullable=False, server_default="internet"),
        sa.Column("timezone", sa.String(50), nullable=False, server_default="UTC"),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("stream_url", sa.Text(), nullable=True),
        sa.Column("broadcast_config", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Channel Streams
    op.create_table(
        "channel_streams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("station_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel_name", sa.String(255), nullable=False),
        sa.Column("bitrate", sa.Integer(), nullable=False, server_default="128"),
        sa.Column("codec", sa.String(50), nullable=False, server_default="'aac'"),
        sa.Column("hls_manifest_path", sa.Text(), nullable=True),
        sa.Column("listeners_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Rule Sets
    op.create_table(
        "rule_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), unique=True, nullable=False),
        sa.Column("constraints", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Categories
    op.create_table(
        "categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), unique=True, nullable=False),
        sa.Column("ruleset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rule_sets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("allowed_transitions", postgresql.JSONB(), nullable=True),
        sa.Column("min_play_length", sa.Float(), nullable=True),
        sa.Column("fade_allowed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Assets
    op.create_table(
        "assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("artist", sa.String(255), nullable=True),
        sa.Column("album", sa.String(255), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("album_art_path", sa.Text(), nullable=True),
        sa.Column("metadata_extra", postgresql.JSONB(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Asset-Categories M2M
    op.create_table(
        "asset_categories",
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
    )

    # Sponsors
    op.create_table(
        "sponsors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("length_seconds", sa.Float(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("audio_file_path", sa.Text(), nullable=False),
        sa.Column("target_rules", postgresql.JSONB(), nullable=True),
        sa.Column("insertion_policy", sa.String(50), nullable=False, server_default="'between_tracks'"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Schedule Entries
    op.create_table(
        "schedule_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("station_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("recurrence_rule", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Holiday Windows
    op.create_table(
        "holiday_windows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("start_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_blackout", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("affected_stations", postgresql.JSONB(), nullable=True),
        sa.Column("replacement_content", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Play Logs
    op.create_table(
        "play_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("station_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", play_source, nullable=False, server_default="scheduler"),
        sa.Column("fade_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Indexes
    op.create_index("ix_play_logs_station_start", "play_logs", ["station_id", "start_utc"])
    op.create_index("ix_schedule_entries_station_time", "schedule_entries", ["station_id", "start_time", "end_time"])


def downgrade() -> None:
    op.drop_index("ix_schedule_entries_station_time")
    op.drop_index("ix_play_logs_station_start")
    op.drop_table("play_logs")
    op.drop_table("holiday_windows")
    op.drop_table("schedule_entries")
    op.drop_table("sponsors")
    op.drop_table("asset_categories")
    op.drop_table("assets")
    op.drop_table("categories")
    op.drop_table("rule_sets")
    op.drop_table("channel_streams")
    op.drop_table("stations")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS play_source")
    op.execute("DROP TYPE IF EXISTS station_type")
    op.execute("DROP TYPE IF EXISTS user_role")
