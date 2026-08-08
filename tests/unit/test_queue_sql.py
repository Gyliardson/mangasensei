from sqlalchemy.dialects import postgresql

from mangasensei.infrastructure.database.queue import build_claim_statement


def test_claim_statement_uses_postgresql_skip_locked_and_ordering() -> None:
    statement = build_claim_statement(worker_id="worker-a", lease_seconds=300)

    sql = str(statement.compile(dialect=postgresql.dialect())).upper()  # type: ignore[no-untyped-call]
    unqualified_sql = sql.replace("MANGASENSEI.", "")

    assert "FOR UPDATE OF JOBS SKIP LOCKED" in unqualified_sql
    assert "ORDER BY JOBS.AVAILABLE_AT, JOBS.ID" in unqualified_sql
    assert "RETURNING" in sql
    assert "RETRYABLE_FAILURE" in sql
