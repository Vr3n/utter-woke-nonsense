from django.db.models import Avg

from apps.bronze.models import BronzeIngestion
from apps.core.choices import PipelineStatus


def get_historical_avg_row_count(snapshot_type, simulation_source):
    """Returns average source_row_count for completed ingestions.
    Replace with materialized/cached query when volume grows."""
    return BronzeIngestion.objects.filter(
        snapshot_type=snapshot_type,
        simulation_source=simulation_source,
        status=PipelineStatus.COMPLETED,
    ).aggregate(avg_rows=Avg("source_row_count"))["avg_rows"]
