from django.db import migrations


ADD_SQUAD_FK = """
ALTER TABLE bronze.squad_snapshot
    ADD CONSTRAINT fk_squad_snapshot_ingestion
    FOREIGN KEY (ingestion_id)
    REFERENCES public.bronze_bronzeingestion (id)
    ON DELETE CASCADE;
"""

DROP_SQUAD_FK = """
ALTER TABLE bronze.squad_snapshot
    DROP CONSTRAINT IF EXISTS fk_squad_snapshot_ingestion;
"""

ADD_SCOUTING_FK = """
ALTER TABLE bronze.scouting_snapshot
    ADD CONSTRAINT fk_scouting_snapshot_ingestion
    FOREIGN KEY (ingestion_id)
    REFERENCES public.bronze_bronzeingestion (id)
    ON DELETE CASCADE;
"""

DROP_SCOUTING_FK = """
ALTER TABLE bronze.scouting_snapshot
    DROP CONSTRAINT IF EXISTS fk_scouting_snapshot_ingestion;
"""

ADD_MATCHSTATS_FK = """
ALTER TABLE bronze.squad_matchstats_snapshot
    ADD CONSTRAINT fk_matchstats_snapshot_ingestion
    FOREIGN KEY (ingestion_id)
    REFERENCES public.bronze_bronzeingestion (id)
    ON DELETE CASCADE;
"""

DROP_MATCHSTATS_FK = """
ALTER TABLE bronze.squad_matchstats_snapshot
    DROP CONSTRAINT IF EXISTS fk_matchstats_snapshot_ingestion;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('bronze', '0003_bronzeingestion_parse_progress_current_and_more'),
    ]

    operations = [
        migrations.RunSQL(ADD_SQUAD_FK, DROP_SQUAD_FK),
        migrations.RunSQL(ADD_SCOUTING_FK, DROP_SCOUTING_FK),
        migrations.RunSQL(ADD_MATCHSTATS_FK, DROP_MATCHSTATS_FK),
    ]
