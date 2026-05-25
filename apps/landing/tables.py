from django.urls import reverse
from django.utils.safestring import mark_safe
from django_tables2 import Column, DateColumn, DateTimeColumn, Table

from .models import LandingUpload


class LandingUploadTable(Table):
    ingame_date = DateColumn(verbose_name="Ingame Date")
    data_label = Column(verbose_name="Label")
    source_file_name = Column(verbose_name="Source File")
    simulation_source = Column(verbose_name="Source")
    upload_timestamp = DateTimeColumn(verbose_name="Uploaded", format="d/m/y H:i")
    status = Column(verbose_name="Status")

    class Meta:
        model = LandingUpload
        fields = ("ingame_date", "data_label", "source_file_name", "simulation_source", "upload_timestamp", "status")
        template_name = "django_tables2/tailwind_htmx.html"
        attrs = {
            "class": "w-full",
        }
        row_attrs = {
            "id": lambda record: f"row-{record.pk}",
            "class": "border-b border-zinc-800/50 hover:bg-white/[0.02]",
        }

    def render_status(self, value, record):
        dot_text, text_color = {
            "completed": ("bg-green-400", "text-green-400"),
            "processing": ("bg-amber-400 animate-pulse", "text-amber-400"),
            "retrying": ("bg-amber-400 animate-pulse", "text-amber-400"),
            "failed": ("bg-red-400", "text-red-400"),
            "pending": ("bg-zinc-600", "text-zinc-500"),
        }.get(value, ("bg-zinc-600", "text-zinc-500"))

        label = record.get_status_display()
        dot = f'<div class="h-2 w-2 rounded-full {dot_text}"></div>'
        text = f'<span class="text-xs uppercase tracking-widest {text_color}">{label}</span>'

        if value == "failed":
            retry_url = reverse("upload_retry", kwargs={
                "save_slug": record.save_master.slug,
                "upload_id": record.pk,
            })
            button = (
                f'<button '
                f'  class="ml-2 border border-zinc-700 bg-zinc-950 px-2 py-1 text-[10px] uppercase tracking-widest '
                f'  text-zinc-400 transition-all hover:border-zinc-500 hover:text-zinc-200 active:scale-[0.98]" '
                f'  hx-post="{retry_url}" '
                f'  hx-target="#row-{record.pk}" '
                f'  hx-swap="outerHTML" '
                f'>'
                f'  Retry'
                f'</button>'
            )
            return mark_safe(f'{dot} {text} {button}')

        return mark_safe(f'{dot} {text}')
