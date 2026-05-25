from django.conf import settings


def get_pg_conn_string(alias="default"):
    db = settings.DATABASES[alias]
    return (
        f"host={db['HOST']} port={db['PORT']} "
        f"dbname={db['NAME']} user={db['USER']} "
        f"password={db['PASSWORD']}"
    )
