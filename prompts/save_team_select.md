# Save Team Select OTLP Django

These models will make it easy for us to manage meta-data as These
will be selected by default by users everytime.

The users won't have to select these things everytime they ingest data.
This will also improve user experience in the application level.

## The Purpose.

Whenever a `Manager` (We will call the user here `Manager`) logs in,
They will have to select / create a save file.
and also select a team when creating a save file. This will make it easier to
manage some meta-data when ingesting data, and will also make easier to navigate the application.

Make it personal for Managers as we will also view their timelines, and also how they did in their saves.
Data Ingestion will make it logical, and Managers timeline will make it emotional.

## Models

`ContinentMaster`

- ingame_uid
- name

`CountryMaster`

- continent_uid
- ingame_uid
- name

`DivisionMaster`

- country_uid
- ingame_uid
- name

`TeamMaster`

- ingame_uid
- name

`FootballManagerVersionMaster`

- game_version

`SaveMaster`

- name
- start_date
- description

`CurrentlyManagedTeam`

- team_uid
- save_uid
- start_date
- update_date
- is_active

## The flow

Landing Page (Select / Create save) -> dashboard

The urls will be:

`/` -> The landing page (list the saves).
`/<save_name>/` -> The save ingestion dashboard we made previously.
