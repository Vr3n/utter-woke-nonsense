import os
from collections import OrderedDict

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django_htmx.http import trigger_client_event
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from prometheus_client.multiprocess import MultiProcessCollector

from .filters import SaveFilter
from .forms import SaveCreateForm
from .models import SaveMaster


def metrics_view(request):
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        MultiProcessCollector(REGISTRY)
    latest = generate_latest(REGISTRY)
    return HttpResponse(latest, content_type=CONTENT_TYPE_LATEST)


def home(request):
    saves = SaveMaster.objects.select_related("game_version").order_by("-updated_at")
    active_save = saves.first()
    return render(request, "pages/home.html", {"active_save": active_save, "saves": saves})


def save_detail(request, save_slug):
    save = get_object_or_404(
        SaveMaster.objects.select_related("game_version"), slug=save_slug
    )
    saves = SaveMaster.objects.select_related("game_version").order_by("-updated_at")
    active_team = save.managed_teams.filter(is_active=True).select_related("team").first()
    return render(request, "pages/save_detail.html", {"save": save, "saves": saves, "active_team": active_team})


def save_list_partial(request):
    qs = SaveMaster.objects.select_related("game_version").order_by("-updated_at")
    f = SaveFilter(request.GET, queryset=qs)
    saves = f.qs

    version_groups = OrderedDict()
    for save in saves:
        key = save.game_version.game_version
        if key not in version_groups:
            version_groups[key] = {"version": save.game_version, "saves": []}
        version_groups[key]["saves"].append(save)

    version_groups = OrderedDict(sorted(version_groups.items(), reverse=True))

    return render(request, "save/table/list.html", {"version_groups": version_groups})


def save_create_partial(request):
    if request.method == "POST":
        form = SaveCreateForm(request.POST)
        if form.is_valid():
            user = request.user if request.user.is_authenticated else None
            form.save(user=user)
            res = render(request, "save/form/fields.html", {"form": SaveCreateForm()})
            res = trigger_client_event(res, "message", {"level": "success", "message": "Save created!"})
            res = trigger_client_event(res, "save-created")
            return res
        res = render(request, "save/form/fields.html", {"form": form})
        res = trigger_client_event(res, "message", {"level": "error", "message": "Fix errors above."})
        return res
    form = SaveCreateForm()
    return render(request, "save/form/create.html", {"form": form})
