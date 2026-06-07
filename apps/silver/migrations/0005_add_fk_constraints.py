from django.db import migrations


ADD_SQUAD_FK = """
ALTER TABLE silver.squad_snapshot
    ADD CONSTRAINT fk_silver_squad_snapshot_ingestion
    FOREIGN KEY (ingestion_id)
    REFERENCES public.silver_silveringestion (id)
    ON DELETE CASCADE;
"""

DROP_SQUAD_FK = """
ALTER TABLE silver.squad_snapshot
    DROP CONSTRAINT IF EXISTS fk_silver_squad_snapshot_ingestion;
"""

ADD_SCOUTING_FK = """
ALTER TABLE silver.scouting_snapshot
    ADD CONSTRAINT fk_silver_scouting_snapshot_ingestion
    FOREIGN KEY (ingestion_id)
    REFERENCES public.silver_silveringestion (id)
    ON DELETE CASCADE;
"""

DROP_SCOUTING_FK = """
ALTER TABLE silver.scouting_snapshot
    DROP CONSTRAINT IF EXISTS fk_silver_scouting_snapshot_ingestion;
"""

ADD_MATCHSTATS_FK = """
ALTER TABLE silver.squad_matchstats_snapshot
    ADD CONSTRAINT fk_silver_matchstats_snapshot_ingestion
    FOREIGN KEY (ingestion_id)
    REFERENCES public.silver_silveringestion (id)
    ON DELETE CASCADE;
"""

DROP_MATCHSTATS_FK = """
ALTER TABLE silver.squad_matchstats_snapshot
    DROP CONSTRAINT IF EXISTS fk_silver_matchstats_snapshot_ingestion;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('silver', '0004_progress_steps'),
    ]

    operations = [
        migrations.RunSQL(ADD_SQUAD_FK, DROP_SQUAD_FK),
        migrations.RunSQL(ADD_SCOUTING_FK, DROP_SCOUTING_FK),
        migrations.RunSQL(ADD_MATCHSTATS_FK, DROP_MATCHSTATS_FK),
    ]
