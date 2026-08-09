"""add study language result metadata

Revision ID: c63a9b14e2f0
Revises: a17e52c4d908
Create Date: 2026-08-09 08:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c63a9b14e2f0"
down_revision: str | None = "a17e52c4d908"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "study_language",
            sa.String(length=8),
            server_default="pt-BR",
            nullable=False,
        ),
        schema="mangasensei",
    )
    op.create_check_constraint(
        op.f("ck_jobs_study_language"),
        "jobs",
        "study_language IN ('pt-BR','en')",
        schema="mangasensei",
    )
    op.add_column(
        "linguistic_runs",
        sa.Column(
            "dictionary_language",
            sa.String(length=8),
            server_default="en",
            nullable=False,
        ),
        schema="mangasensei",
    )
    op.create_check_constraint(
        op.f("ck_linguistic_runs_dictionary_language"),
        "linguistic_runs",
        "dictionary_language = 'en'",
        schema="mangasensei",
    )
    op.create_table(
        "study_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("linguistic_run_id", sa.BigInteger(), nullable=False),
        sa.Column("content_language", sa.String(length=8), nullable=False),
        sa.Column("study_language", sa.String(length=8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_language = 'ja'",
            name=op.f("ck_study_results_content_language"),
        ),
        sa.CheckConstraint(
            "study_language IN ('pt-BR','en')",
            name=op.f("ck_study_results_study_language"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["mangasensei.jobs.id"],
            name=op.f("fk_study_results_job_id_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["linguistic_run_id"],
            ["mangasensei.linguistic_runs.id"],
            name=op.f("fk_study_results_linguistic_run_id_linguistic_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_study_results")),
        sa.UniqueConstraint("job_id", name=op.f("uq_study_results_job_id")),
        schema="mangasensei",
    )
    op.execute(
        """
        INSERT INTO mangasensei.study_results
            (job_id, linguistic_run_id, content_language, study_language, created_at)
        SELECT DISTINCT ON (jobs.id)
            jobs.id,
            linguistic_runs.id,
            'ja',
            'pt-BR',
            COALESCE(jobs.finished_at, jobs.updated_at, jobs.created_at)
        FROM mangasensei.jobs AS jobs
        JOIN mangasensei.linguistic_runs AS linguistic_runs
          ON linguistic_runs.job_id = jobs.id
        WHERE jobs.status = 'completed'
        ORDER BY jobs.id, linguistic_runs.fencing_token DESC, linguistic_runs.id DESC
        """
    )


def downgrade() -> None:
    op.drop_table("study_results", schema="mangasensei")
    op.drop_constraint(
        op.f("ck_linguistic_runs_dictionary_language"),
        "linguistic_runs",
        type_="check",
        schema="mangasensei",
    )
    op.drop_column("linguistic_runs", "dictionary_language", schema="mangasensei")
    op.drop_constraint(
        op.f("ck_jobs_study_language"),
        "jobs",
        type_="check",
        schema="mangasensei",
    )
    op.drop_column("jobs", "study_language", schema="mangasensei")
