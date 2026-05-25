from django.contrib import admin

from .models import (
    ContinentMaster,
    CountryMaster,
    CurrentlyManagedTeam,
    DivisionMaster,
    FootballManagerVersionMaster,
    SaveMaster,
    TeamMaster,
)


@admin.register(ContinentMaster)
class ContinentMasterAdmin(admin.ModelAdmin):
    list_display = ("name", "ingame_uid")
    search_fields = ("name",)


@admin.register(CountryMaster)
class CountryMasterAdmin(admin.ModelAdmin):
    list_display = ("name", "continent", "ingame_uid")
    list_filter = ("continent",)
    search_fields = ("name",)


@admin.register(DivisionMaster)
class DivisionMasterAdmin(admin.ModelAdmin):
    list_display = ("name", "country", "ingame_uid")
    list_filter = ("country__continent", "country")
    search_fields = ("name",)


@admin.register(TeamMaster)
class TeamMasterAdmin(admin.ModelAdmin):
    list_display = ("name", "ingame_uid")
    search_fields = ("name",)


@admin.register(FootballManagerVersionMaster)
class FootballManagerVersionMasterAdmin(admin.ModelAdmin):
    list_display = ("game_version",)


@admin.register(SaveMaster)
class SaveMasterAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "start_date", "game_version", "updated_at")
    list_filter = ("game_version",)
    search_fields = ("name", "user__username")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(CurrentlyManagedTeam)
class CurrentlyManagedTeamAdmin(admin.ModelAdmin):
    list_display = ("team", "save_master", "start_date", "update_date", "is_active")
    list_filter = ("is_active",)
