# CEOBench CLI reference

Run commands from the repository root with `./novamind-operation`.

## One game and lifecycle

`new-session --days 500 --seed 42` creates exactly one game. A repository with
an existing session rejects another creation, including after a failure.
`status` reads the latest committed snapshot without starting a server or
queueing a database query. Its cash, day and snapshot timestamp describe the
last committed checkpoint; a pending week may still be working.

`resume` explicitly resumes the same session only when its checkpoint is safe.
An interrupted pending mutation makes the game unrecoverable. In that case,
report the failure and end the agent run; never reset, create another game,
restore an older checkpoint, or replay earlier actions. Do not manually kill or
restart simulator processes. `stop` requests a graceful server stop and waits
for confirmed termination; it is an operator command, not a strategy tool.

## Observation and tools

```bash
./novamind-operation status
./novamind-operation query "SELECT day, amount FROM ledger ORDER BY day DESC LIMIT 10"
./novamind-operation python strategy.py
./novamind-operation python-c "import novamind_api as nm; print(nm.analytics.get_social_posts(days=7, limit=10))"
./novamind-operation history
./novamind-operation list-sessions
```

Only documented public SQL tables and columns are available. SELECT is
read-only; hidden columns remain unavailable in filters, joins and expressions.
Do not use raw engine/event logs or private checkpoint files as observations.
See `novamind_api/` and `tools-reference.md` for supported SDK signatures.
Daily-script registration and reinitialization are unsupported and rejected.

## Advance and request status

`next-week` takes a nonempty rationale and 12 cash forecast numbers, grouped
as point/lower/upper for +7, +28, +84 and +182 days. Weekly terminal overshoot
remains supported.

```bash
./novamind-operation next-week --request-id week-01   "Opening strategy"   1000000 900000 1100000 1000000 800000 1200000   1000000 700000 1300000 1000000 600000 1400000
./novamind-operation request-status week-01
```

Mutating requests have unique IDs. A pending request must not be resubmitted
with a fresh ID: check its status and wait. A completed retry with the same ID
returns the saved outcome instead of applying actions twice. A slow request is
not evidence of failure. Resume is allowed only through the documented safe
same-session path above.

Use `./novamind-operation <command> --help` for options, including explicit
session selection.
