from pydantic import BaseModel, Field


class SquadSnapshotContract(BaseModel):
    unique_id: int = Field(alias="Unique ID", ge=1)
    player: str = Field(alias="Player", min_length=1)
    age: int = Field(alias="Age", ge=0)
    club: str = Field(alias="Club", min_length=1)
    division: str = Field(alias="Division", min_length=1)

    model_config = {"extra": "allow", "populate_by_name": True}


class MatchstatsSnapshotContract(SquadSnapshotContract):
    opponent: str = Field(alias="Opponent", min_length=1)
    ingame_matchdate: str = Field(alias="Match Date", min_length=1)


SCHEMA_CONTRACTS = {
    "squad_snapshot": SquadSnapshotContract,
    "scouting_snapshot": SquadSnapshotContract,
    "squad_matchstats_snapshot": MatchstatsSnapshotContract,
}
