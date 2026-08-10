"""add canonical lexical match persistence

Revision ID: 4b913c2a7e56
Revises: b7d2f4a91c63
Create Date: 2026-08-09 22:15:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4b913c2a7e56"
down_revision: str | None = "b7d2f4a91c63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KATAKANA = (
    "ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾ"
    "タダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポ"
    "マミムメモャヤュユョヨラリルレロヮワヰヱヲンヴヵヶ"
)
_HIRAGANA = (
    "ぁあぃいぅうぇえぉおかがきぎくぐけげこござざしじすずせぜそぞ"
    "ただちぢっつづてでとどなにぬねのはばぱひびぴふぶぷへべぺほぼぽ"
    "まみむめもゃやゅゆょよらりるれろゎわゐゑをんゔゕゖ"
)


def upgrade() -> None:
    op.create_table(
        "lexical_matches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("linguistic_run_id", sa.BigInteger(), nullable=False),
        sa.Column("region_id", sa.BigInteger(), nullable=False),
        sa.Column("stable_key", sa.LargeBinary(length=32), nullable=False),
        sa.Column("start_token_ordinal", sa.Integer(), nullable=False),
        sa.Column("end_token_ordinal", sa.Integer(), nullable=False),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("display_lemma", sa.Text(), nullable=False),
        sa.Column("display_reading", sa.Text(), nullable=False),
        sa.Column("dictionary_namespace", sa.String(length=32), nullable=False),
        sa.Column("dictionary_entry_id", sa.String(length=128), nullable=False),
        sa.Column("form_lemma", sa.Text(), nullable=False),
        sa.Column("form_reading", sa.Text(), nullable=False),
        sa.Column("dictionary_source", sa.String(length=64), nullable=False),
        sa.Column("jlpt_level", sa.String(length=2), nullable=True),
        sa.Column("jlpt_official", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "octet_length(stable_key) = 32",
            name=op.f("ck_lexical_matches_stable_key_length"),
        ),
        sa.CheckConstraint(
            "start_token_ordinal >= 0 AND end_token_ordinal > start_token_ordinal",
            name=op.f("ck_lexical_matches_token_span"),
        ),
        sa.CheckConstraint(
            "char_length(dictionary_namespace) BETWEEN 1 AND 32",
            name=op.f("ck_lexical_matches_dictionary_namespace_length"),
        ),
        sa.CheckConstraint(
            "jlpt_level IS NULL OR jlpt_level IN ('N1','N2','N3','N4','N5')",
            name=op.f("ck_lexical_matches_jlpt"),
        ),
        sa.ForeignKeyConstraint(
            ["linguistic_run_id"],
            ["mangasensei.linguistic_runs.id"],
            name=op.f("fk_lexical_matches_linguistic_run_id_linguistic_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["region_id"],
            ["mangasensei.ocr_regions.id"],
            name=op.f("fk_lexical_matches_region_id_ocr_regions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lexical_matches")),
        sa.UniqueConstraint(
            "linguistic_run_id",
            "stable_key",
            name=op.f("uq_lexical_matches_linguistic_run_id_stable_key"),
        ),
        sa.UniqueConstraint(
            "linguistic_run_id",
            "region_id",
            "start_token_ordinal",
            "end_token_ordinal",
            "dictionary_namespace",
            "dictionary_entry_id",
            "form_lemma",
            "form_reading",
            name="uq_lexical_matches_occurrence_identity",
        ),
        schema="mangasensei",
    )
    op.create_index(
        "ix_lexical_matches_run_region_start",
        "lexical_matches",
        ["linguistic_run_id", "region_id", "start_token_ordinal"],
        unique=False,
        schema="mangasensei",
    )
    op.create_table(
        "lexical_meanings",
        sa.Column("lexical_match_id", sa.BigInteger(), nullable=False),
        sa.Column("meaning_ordinal", sa.Integer(), nullable=False),
        sa.Column("meaning", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["lexical_match_id"],
            ["mangasensei.lexical_matches.id"],
            name=op.f("fk_lexical_meanings_lexical_match_id_lexical_matches"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "lexical_match_id",
            "meaning_ordinal",
            name=op.f("pk_lexical_meanings"),
        ),
        schema="mangasensei",
    )
    op.create_table(
        "gemini_lexical_vocabulary_links",
        sa.Column("region_analysis_id", sa.BigInteger(), nullable=False),
        sa.Column("lexical_match_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["region_analysis_id"],
            ["mangasensei.gemini_region_analyses.id"],
            name=op.f(
                "fk_gemini_lexical_vocabulary_links_region_analysis_id_gemini_region_analyses"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["lexical_match_id"],
            ["mangasensei.lexical_matches.id"],
            name=op.f(
                "fk_gemini_lexical_vocabulary_links_lexical_match_id_lexical_matches"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "region_analysis_id",
            "lexical_match_id",
            name=op.f("pk_gemini_lexical_vocabulary_links"),
        ),
        schema="mangasensei",
    )

    # A legacy non-null dictionary_entry_id proves the old runtime found exactly one
    # entry at (token.lemma, hiragana(token.reading)). Therefore that exact lookup key
    # is the normalized v3 form key of the already-resolved entry; no homograph is
    # re-selected during backfill.
    op.execute(
        sa.text(
            """
            INSERT INTO mangasensei.lexical_matches (
                linguistic_run_id,
                region_id,
                stable_key,
                start_token_ordinal,
                end_token_ordinal,
                surface,
                display_lemma,
                display_reading,
                dictionary_namespace,
                dictionary_entry_id,
                form_lemma,
                form_reading,
                dictionary_source,
                jlpt_level,
                jlpt_official
            )
            SELECT
                linguistic_run_id,
                region_id,
                stable_key,
                token_ordinal,
                token_ordinal + 1,
                surface,
                lemma,
                reading,
                'JMdict',
                dictionary_entry_id,
                lemma,
                translate(reading, :katakana, :hiragana),
                COALESCE(dictionary_source, 'JMdict'),
                jlpt_level,
                COALESCE(jlpt_official, false)
            FROM mangasensei.linguistic_tokens
            WHERE dictionary_entry_id IS NOT NULL
            """
        ).bindparams(katakana=_KATAKANA, hiragana=_HIRAGANA)
    )
    op.execute(
        """
        INSERT INTO mangasensei.lexical_meanings (lexical_match_id, meaning_ordinal, meaning)
        SELECT lm.id, legacy.meaning_ordinal, legacy.meaning
        FROM mangasensei.linguistic_meanings AS legacy
        JOIN mangasensei.linguistic_tokens AS token ON token.id = legacy.token_id
        JOIN mangasensei.lexical_matches AS lm
          ON lm.linguistic_run_id = token.linguistic_run_id
         AND lm.stable_key = token.stable_key
        """
    )
    op.execute(
        """
        INSERT INTO mangasensei.gemini_lexical_vocabulary_links
            (region_analysis_id, lexical_match_id)
        SELECT legacy.region_analysis_id, lm.id
        FROM mangasensei.gemini_vocabulary_links AS legacy
        JOIN mangasensei.linguistic_tokens AS token ON token.id = legacy.token_id
        JOIN mangasensei.lexical_matches AS lm
          ON lm.linguistic_run_id = token.linguistic_run_id
         AND lm.stable_key = token.stable_key
        """
    )


def downgrade() -> None:
    op.drop_table("gemini_lexical_vocabulary_links", schema="mangasensei")
    op.drop_table("lexical_meanings", schema="mangasensei")
    op.drop_index(
        "ix_lexical_matches_run_region_start",
        table_name="lexical_matches",
        schema="mangasensei",
    )
    op.drop_table("lexical_matches", schema="mangasensei")
