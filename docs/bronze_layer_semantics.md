# Bronze Layer Semantics.

## What one Upload Means?

I got this wrong initially.

- Thinking a row means analytics. But it should be an observation.
- Analytics are derived later. Row is just raw observation.
- I have put the corrected measures in their respective sections.

### squad

❌ Snapshot of squad's transfer-value, wage, and statistics after a decided interval.
✅ Observed squad player's snapshot at a specific simulation time (ingame_date, save_name).

### scouting

❌ Snapshot of player's who are being scouted. Their transfer-value, wage, and statistics after a decided interval.
✅ Observed Snapshot of scouted player's at a specific simulation time for a save (ingame_date, save_name).

### squad_matchstat

❌ Snapshot of player's statistics after a match has been played.
✅ Observed snapshot of player's after a specific match has been played (ingame_matchdate, opponent).

## What one row means?

The below are wrong actually.

- One row means the raw observation of the players during that specific interval.
- What I stated below is the analytics, and opinion that will be given after analysis.
- For DE at bronze level `row` means `raw observation`.

### squad

Player status after an interval:

- Has their transfer value / wage increased, decreased or still the same
- Have their performance improved, declined, or still the same.

One row shows overall status of the player financialy, and performance.

### scouting

Scouted Player status after an interval:

- Has their transfer value / wage increased, decreased or still the same
- Have their performance improved, declined, or still the same.

One row shows overall status of the player financialy, and performance.

### squad_matchstat

Performance of the player in the match.

## How snapshots are identified?

I actually don't understand this question. Can you elaborate this question \
I am a beginner in DE so, I don't have much intuition but based on my understanding \
you are asking that: "When stakeholder asks for historical snapshots, how are the snapshots identified so that we can pull them up?"

- Save the `upload_date`, `ingame_date`, and `save_name`.
- We can ask question then: "So you want data from 'malaga_rtg' save ranging from `upload_date` to `upload_date` or should be from `ingame_date` to `ingame_date`?".

## How lineage is preserved?

- We save the snapshot with the `upload_date`, `ingame_date`, and `save_name`.
- There are no overwrites in the table only append.
- In `squad_matchstat` we also save `opponent`, and `ingame_matchdate`.

The `ingame_date` is the date in the simulation when we exported the data.

## How duplicate uploads are prevented?

Hashing the file contents, if hash change then continue further Else tell user that it's a duplicate upload.
