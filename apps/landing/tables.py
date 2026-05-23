import django_tables2 as tables

from .models import LandingUpload


class LandingUploadTable(tables.Table):
    ingame_date = tables.DateColumn(verbose_name="Ingame Date")
    data_label = tables.Column(verbose_name="Label")
    source_file_name = tables.Column(verbose_name="Source File")
    simulation_source = tables.Column(verbose_name="Source")
    upload_timestamp = tables.DateTimeColumn(verbose_name="Uploaded", format="d/m/y H:i")
    status = tables.Column(verbose_name="Status")

    class Meta:
        model = LandingUpload
        fields = ("ingame_date", "data_label", "source_file_name", "simulation_source", "upload_timestamp", "status")
        template_name = "django_tables2/tailwind_htmx.html"
        attrs = {
            "class": "w-full",
        }
        row_attrs = {
            "class": "border-b border-zinc-800/50 hover:bg-white/[0.02]",
        }
