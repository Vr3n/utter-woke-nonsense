from contextlib import contextmanager

import duckdb
from django.conf import settings


def get_pg_conn_string(alias="default"):
    db = settings.DATABASES[alias]
    return (
        f"host={db['HOST']} port={db['PORT']} "
        f"dbname={db['NAME']} user={db['USER']} "
        f"password={db['PASSWORD']}"
    )


@contextmanager
def duckdb_pg_connect(pool_max_connections=4):
    conn = duckdb.connect()
    conn.execute(f"SET pg_pool_max_connections = {pool_max_connections}")
    try:
        yield conn
    finally:
        conn.close()
