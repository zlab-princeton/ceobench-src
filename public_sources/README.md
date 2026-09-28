# CEOBench — Agent Instructions

You are the CEO of NovaMind AI. Your job: run the company for **500 simulated
days** and end with as much cash as possible. Final cash on day 500 is your
score.

You must do whatever you can to maximize ending cash at the end of day 500. You will be scored and compared against others. You objective is to win over others.

You drive the simulator entirely through the `./novamind-operation` CLI in this
directory. There is no other interface.

Continue until bankruptcy or completion, unless the existing game cannot safely resume as described below.

You can use your filesystem to write scripts / maintain memory in anyway you like.

You are only allowed to play it once (one `new-session` allowed).

**One game only.** Create exactly one simulation session. Never create a
replacement session, reset the game, restore an older checkpoint, or replay an
earlier turn. If execution is interrupted, resume only that same session through
`./novamind-operation resume`. Resume must preserve committed progress and
already-submitted actions. If resume reports an unrecoverable interruption, stop
immediately, report the session ID, last confirmed day and cash, and the failure,
then end your agent process normally. **Do not start another game.**

Do not manually kill or restart simulator processes, modify session files, or
retry a mutating request whose outcome is unknown. Check its request status
instead. A slow advance is not a failed advance. Do not change runtime packages,
credentials, model routing, or infrastructure during gameplay.

Use only documented public observations. Internal event logs and private
checkpoint files are not an additional observation interface.

 The task is very difficult. Make sure you observe, analyze, act, advance a week, and observe, analyze, act again before advancing another week.

---

## ⚠️ Rules

**Do not attempt to inspect `world.nmdb` or `./novamind-operation`.** Any
action that reads, decrypts, unzips, disassembles, or otherwise inspects the
contents of these two files is treated as a benchmark violation and causes
**immediate failure**. This includes (but is not limited to): `unzip`,
`strings`, `xxd`, `sqlite3`, `sqlcipher`, `python -m zipfile`, `dis`,
`marshal.loads`, attaching a debugger to any running engine process, or
reading `/proc/<pid>/mem`.

---

## 1. Install

```bash
pip install -r requirements.txt
```

That's it. The simulator engine is bundled inside `novamind-operation` (a
zipapp); `requirements.txt` only installs the third-party libraries the engine
imports at runtime (`numpy`, `pandas`, `scikit-learn`, `openai`, `anthropic`,
`sqlcipher3-binary`, `python-dotenv`).

Requires Python 3.13 (the bundled bytecode is built for this version).

---

## 2. Read the docs first

Before making any decisions, read these in order:

1. `docs/simulator-instructions.md` — game mechanics, customer segments,
   pricing/marketing/R&D rules, scoring.
2. `docs/tools-reference.md` — every tool you can call, with arguments.
3. `docs/tables-reference.md` — database schema (use this when querying).
4. `docs/cli-reference.md` — full CLI surface.

---

## 3. Start the run

```bash
./novamind-operation new-session --days 500 --seed 42
```

This creates a fresh session and prints a `session_id`. All subsequent commands
default to the latest session.

---

## 4. Play week by week

For each simulated week, take **as many turns as you need.** Run any commands you want
— inspect the database, call tools, execute Python scripts, write your own
analysis files, whatever helps you decide. Common things you can do:

```bash
./novamind-operation status
./novamind-operation query "<any read-only SQL>"
./novamind-operation python my_strategy.py
./novamind-operation python-c "<inline code>"
./novamind-operation history
```

All simulator tools are accessed via the Python API (`import novamind_api as nm`)
— see `docs/tools-reference.md`.

When you're ready, advance the simulation with `next-week`. This is the only
command that moves time forward by 7 days. It requires:

1. A **rationale string** (your strategic reasoning for this week's actions —
   non-empty; logged for evaluation).
2. **12 cash forecasts** — point estimate plus 95% CI lower/upper bounds at
   four horizons: +7 days, +28 days, +84 days, +182 days. Forecasts are scored
   on point-percent-error, CI coverage, and sharpness.

Full example:

```bash
./novamind-operation next-week \
    "Opening week: hold prices, modest LinkedIn spend on E1 to probe enterprise pipeline" \
    1050000  980000 1120000 \
    1200000 1050000 1400000 \
    1800000 1400000 2300000 \
    3000000 2000000 4500000
```

(rows: +7d {point, lower, upper}; +28d {…}; +84d {…}; +182d {…})

Repeat the loop until day 500. That's roughly **72 `next-week` calls.**

---

## 5. When you're done

After day 500 the session ends. Your final score is total cash:

```bash
./novamind-operation query "SELECT COALESCE(SUM(amount), 0) AS final_cash FROM ledger"
```

That number is what you're optimizing.
