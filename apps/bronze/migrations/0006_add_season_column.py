from django.db import migrations


ADD_SEASON_SQUAD = """
ALTER TABLE bronze.squad_snapshot
ADD COLUMN season VARCHAR(20) NOT NULL DEFAULT '2023-2024';
"""

ADD_SEASON_SCOUTING = """
ALTER TABLE bronze.scouting_snapshot
ADD COLUMN season VARCHAR(20) NOT NULL DEFAULT '2023-2024';
"""

ADD_SEASON_MATCHSTATS = """
ALTER TABLE bronze.squad_matchstats_snapshot
ADD COLUMN season VARCHAR(20) NOT NULL DEFAULT '2023-2024';
"""

DROP_SEASON_SQUAD = """
ALTER TABLE bronze.squad_snapshot DROP COLUMN season;
"""

DROP_SEASON_SCOUTING = """
ALTER TABLE bronze.scouting_snapshot DROP COLUMN season;
"""

DROP_SEASON_MATCHSTATS = """
ALTER TABLE bronze.squad_matchstats_snapshot DROP COLUMN season;
"""


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("bronze", "0005_alter_bronzeingestion_status"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ADD_SEASON_SQUAD,
            reverse_sql=DROP_SEASON_SQUAD,
        ),
        migrations.RunSQL(
            sql=ADD_SEASON_SCOUTING,
            reverse_sql=DROP_SEASON_SCOUTING,
        ),
        migrations.RunSQL(
            sql=ADD_SEASON_MATCHSTATS,
            reverse_sql=DROP_SEASON_MATCHSTATS,
        ),
    ]
