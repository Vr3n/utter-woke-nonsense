from django import forms
from django.utils.text import slugify

from .models import (
    CurrentlyManagedTeam,
    FootballManagerVersionMaster,
    SaveMaster,
    TeamMaster,
)

INPUT_CLASS = (
    "w-full border border-zinc-800 bg-zinc-950 px-4 py-3 "
    "text-zinc-100 placeholder:text-zinc-600 outline-none "
    "focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/20"
)
SELECT_CLASS = (
    "w-full border border-zinc-800 bg-zinc-950 px-4 py-3 "
    "text-zinc-100 outline-none "
    "focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/20"
)
TEXTAREA_CLASS = INPUT_CLASS + " resize-y min-h-[80px]"


class SaveCreateForm(forms.Form):
    name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "placeholder": "e.g. malaga_rtg",
            }
        ),
    )
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"})
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": TEXTAREA_CLASS, "rows": 3}),
    )
    game_version = forms.ModelChoiceField(
        queryset=FootballManagerVersionMaster.objects.all(),
        required=True,
        empty_label=None,
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )
    team = forms.ModelChoiceField(
        queryset=TeamMaster.objects.all(),
        required=False,
        empty_label="Select existing team",
        widget=forms.Select(attrs={"class": SELECT_CLASS}),
    )
    new_team_name = forms.CharField(
        max_length=255,
        required=False,
        label="Or add a new team",
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "placeholder": "e.g. Malaga CF",
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        team = cleaned_data.get("team")
        new_team_name = cleaned_data.get("new_team_name")

        if not team and not new_team_name:
            raise forms.ValidationError(
                "Select an existing team or enter a name for a new one."
            )

        return cleaned_data

    def save(self, user=None):
        data = self.cleaned_data

        if data.get("new_team_name"):
            team, _ = TeamMaster.objects.get_or_create(
                name=data["new_team_name"],
                defaults={"ingame_uid": slugify(data["new_team_name"])},
            )
        else:
            team = data["team"]

        save = SaveMaster.objects.create(
            user=user,
            name=data["name"],
            slug=slugify(data["name"]),
            start_date=data["start_date"],
            description=data.get("description", ""),
            game_version=data.get("game_version"),
        )

        CurrentlyManagedTeam.objects.create(
            save_master=save,
            team=team,
            start_date=data["start_date"],
            update_date=data["start_date"],
            is_active=True,
        )

        return save
