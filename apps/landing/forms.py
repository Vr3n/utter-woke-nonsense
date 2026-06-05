import hashlib

from django import forms

from .models import LandingUpload

INPUT_CLASS = (
    "w-full border border-zinc-800 bg-zinc-950 px-4 py-3 "
    "text-zinc-100 placeholder:text-zinc-600 outline-none "
    "focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/20"
)


class SnapshotUploadForm(forms.Form):
    snapshot_type = forms.CharField(widget=forms.HiddenInput())
    ingame_date = forms.DateField(
        widget=forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"})
    )
    season = forms.CharField(
        max_length=20,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "placeholder": "e.g. 2023-2024 or 1999-2000",
            }
        ),
    )
    data_label = forms.CharField(
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "placeholder": "e.g. winter_scouting, under_23_scouting",
            }
        ),
    )
    source_file = forms.FileField(allow_empty_file=False)

    def clean_source_file(self):
        file = self.cleaned_data["source_file"]
        content = file.read()
        file.seek(0)
        file_hash = hashlib.sha256(content).hexdigest()

        if LandingUpload.objects.filter(source_file_hash=file_hash).exists():
            raise forms.ValidationError("A file with identical content has already been uploaded.")

        self._file_hash = file_hash
        return file

    @property
    def file_hash(self):
        return self._file_hash
