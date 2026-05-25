from django.db import models
from django.conf import settings
from django.utils.text import slugify


class ContinentMaster(models.Model):
    ingame_uid = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=255)

    class Meta:
        verbose_name = "Continent"
        verbose_name_plural = "Continents"

    def __str__(self):
        return self.name


class CountryMaster(models.Model):
    continent = models.ForeignKey(
        ContinentMaster, on_delete=models.CASCADE, related_name="countries"
    )
    ingame_uid = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=255)

    class Meta:
        verbose_name = "Country"
        verbose_name_plural = "Countries"

    def __str__(self):
        return self.name


class DivisionMaster(models.Model):
    country = models.ForeignKey(
        CountryMaster, on_delete=models.CASCADE, related_name="divisions"
    )
    ingame_uid = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=255)

    class Meta:
        verbose_name = "Division"
        verbose_name_plural = "Divisions"

    def __str__(self):
        return self.name


class TeamMaster(models.Model):
    ingame_uid = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=255)

    class Meta:
        verbose_name = "Team"
        verbose_name_plural = "Teams"

    def __str__(self):
        return self.name


class FootballManagerVersionMaster(models.Model):
    game_version = models.CharField(max_length=255, unique=True)

    class Meta:
        verbose_name = "Football Manager Version"
        verbose_name_plural = "Football Manager Versions"

    def __str__(self):
        return self.game_version


class SaveMaster(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="saves",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    start_date = models.DateField()
    description = models.TextField(blank=True, default="")
    game_version = models.ForeignKey(
        FootballManagerVersionMaster,
        on_delete=models.PROTECT,
        related_name="saves",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Save"
        verbose_name_plural = "Saves"
        unique_together = ("user", "name")

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class CurrentlyManagedTeam(models.Model):
    save_master = models.ForeignKey(
        SaveMaster, on_delete=models.CASCADE, related_name="managed_teams"
    )
    team = models.ForeignKey(
        TeamMaster, on_delete=models.CASCADE, related_name="managed_teams"
    )
    start_date = models.DateField(help_text="In-game date when management began")
    update_date = models.DateField(help_text="In-game date of last update")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Currently Managed Team"
        verbose_name_plural = "Currently Managed Teams"

    def __str__(self):
        return f"{self.team.name} ({self.save_master.name})"
