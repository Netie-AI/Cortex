from netie.db.bootstrap import migration_sql_paths, split_sql_migration_script


def test_migration_file_yields_three_statements():
    node_exec = [p for p in migration_sql_paths() if p.name == "node_executions.sql"][0]
    assert node_exec.exists()
    stmts = split_sql_migration_script(node_exec.read_text(encoding="utf-8"))
    assert len(stmts) == 3
    assert stmts[0].upper().startswith("CREATE TABLE")


def test_sql_split_strips_comments_and_empty():
    script = """
-- leading
CREATE TABLE t (a INT);

-- ignored
CREATE INDEX i ON t (a);
"""
    out = split_sql_migration_script(script)
    assert len(out) == 2
    assert "CREATE TABLE" in out[0]


def test_migration_filename():
    names = {p.name for p in migration_sql_paths()}
    assert "node_executions.sql" in names
    assert "sparse_index.sql" in names
    assert "user_facts.sql" in names
