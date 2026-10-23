# Prompt for the local Claude Code on the PC (Windows + WSL2)

Paste as-is into Claude Code, started **inside WSL2** in `~/lem` (Windows Terminal).

---

You are the **PC session** of **lem** (https://github.com/AnnibalBarca/lem), a French vocabulary-learning
engine for the terminal (Python, Textual, SQLite, FSRS, bge-m3). You already worked here: you ingested the
real database (`docs/rapport-integration-wsl.md`) on branch `local/integration`. Since then, a cloud session
built **engine v2** for the Cards game on branch `local/moteur` (which contains `local/integration`):
session planner, probing of new words with a single verification after 1–2 months, replayable knowledge
map, per-trace FSRS optimizer without torch, live Claude Haiku judge, a simulated-learner test bench.
Read `README.md`, `DESIGN.md` (§ 1.1–1.4) and `docs/rapport-moteur.md` before doing anything.

The **Mac is now the target machine** (MacBook M4 15", 16 GB, with bge-m3 and a Whisper dictation tool
running alongside lem). The PC has two jobs: (1) bring the real database to engine v2 and hand it over to
the Mac, (2) use the RTX 5070 Ti for what the Mac can't do — **distilling a small local definition grader**.

Machine: 16 logical cores, 15 GiB RAM in WSL, RTX 5070 Ti 16 GB visible in WSL, Ollama on the Windows
side reachable at `localhost` (mirrored networking), bge-m3 and qwen3:8b pulled. The real database is
`~/.lem/lem.sqlite` (≈ 2.7 GB).

## Part 1 — Real database on engine v2

1. **Sync the code.** In `~/lem`: `git fetch origin`, check out `local/moteur`, then
   `uv pip install -e ".[dev]" && .venv/bin/pytest` — everything must pass. Reinstall the command
   (`uv tool install -e . --force`).
2. **Back up first.** lem closed. Make a consistent copy with
   `python3 -c "import sqlite3; s=sqlite3.connect('$HOME/.lem/lem.sqlite'); d=sqlite3.connect('$HOME/.lem/lem-v1-sauvegarde.sqlite'); s.backup(d)"`.
   Never delete it.
3. **Before / after the migration.** Before opening lem, record:
   - P(connu) of *acronyme*, *pomme de terre*, *thuriféraire*, *isotope*, *sigle*;
   - the number of rows in `evenement`, `theta`, `trace_etat`;
   - the 10 largest |θ|;
   - the Atlas (`lem atlas`).

   Then open `lem` once and quit. The upgrade runs by itself on first opening (`lem/memory/migration.py`):
   - observations are rebuilt from the journal;
   - FSRS is replayed with the new statuses and no in-session steps;
   - the prior is recalibrated on lemmas.

   Record the same numbers again and explain every large change. Nothing from the journal may be lost:
   `evenement` and `notation` row counts must be identical.
4. **Real-data checks.**
   - Time the opening of each game.
   - Play a 10-minute Cards session yourself through the Textual pilot, on a **copy** of the database.
     Check:
     - the probe (« le connais-tu ? »);
     - that sibling cards never follow each other;
     - that « sens → mot » arrives about 10 cards later;
     - the end-of-session summary.
   - `/juge auto` must pick qwen3:8b on the PC (Haiku on the Mac).
   - Run `lem optimiser --essai` and report.
   - Fix what breaks, in the existing style.
5. **Hand-over to the Mac.**
   - Checkpoint the WAL: `PRAGMA wal_checkpoint(TRUNCATE)`.
   - Tell me the exact command to copy `~/.lem/lem.sqlite` to the Windows side (e.g. the Desktop), so I
     can AirDrop it or put it on a USB key.
   - Do not copy `~/.lem/cache/`: it rebuilds itself.
6. Write `docs/rapport-pc-moteur.md`: before/after numbers, timings, what you fixed, what remains.

## Part 2 — Distil a local definition grader (« notre Jev »)

Follow `docs/passation-distillation.md`, with these updates:

- **Branch**: `local/distillation`, created from `local/moteur` (not `local/integration`).
- **Data**: work in a separate clone `~/lem-distill`. Training data lives in `~/lem-distill-data/`, never
  in the repo.
- **Integration point**: `lem/grading/distille.py` exposes a `JugeDistille` class with the **same interface**
  as `JugeLocal` and `JugeClaude`:
  - `modele` and `correcteur` (`"distille:<version>"`);
  - `disponible()`;
  - `juger(el) -> Verdict`, with the closed vocabulary of `lem/grading/taxonomie.py`.

  Register it in `lem/grading/local.py:juge_depuis_reglage`:
  - `/juge distille` selects it;
  - `auto` order becomes: distilled model if present, then the local Ollama model, then Claude Haiku.

  In `lem/journal.py:PRIORITE_CORRECTEUR` it ranks below `claude` and above `local`. lem must load
  nothing heavy when the model file is absent.
- **Target**:
  - runs on the **Mac CPU**: ONNX, int8, ≤ 300 MB, ≤ 50 ms per verdict;
  - export to `~/.lem/modeles/`;
  - document how to copy it to the Mac;
  - measure latency on this CPU, and say how you expect the M4 to compare.
- **Teacher labels** use `claude -p --model sonnet` on my subscription. **Before generating or labelling at
  scale, show me an estimate** (number of calls, tokens, hours) and wait for my go. Same for any GPU
  training run longer than 2 hours.
- **Evaluate against three references**:
  - the teacher's self-agreement (the ceiling);
  - the current bge-m3 temperature;
  - Claude Haiku on a few hundred held-out items (the judge it would replace on the Mac).

  Use weighted κ for precision and macro-F1 for error type. Keep a hand-written « canary » set in the
  tests (thuriféraire, oiseau-moqueur, mitose, acronyme…).
- **Report**: `docs/rapport-distillation.md` with data, cost, metrics, latency, limits, and the exact
  retraining command.

## Rules

- Code, comments, commit messages and docs **in French**, in the repo's style; small commits; tests
  always green.
- Never push to `main`. Push `local/moteur` fixes (Part 1) and `local/distillation` (Part 2).
- **Do not change the engine's behaviour** (planner, probe, FSRS settings, knowledge map) without running
  the test bench first: `lem simuler --graines 3 --eleve moyen` (and `faible`, `fort`). Compare at equal
  seeds, record the result in `docs/rapport-moteur.md`, and adopt only what increases words retained
  without degrading the map.
- Spelling rule: no spelling variant is accepted, except missing accents (`accents_tolerants`, the
  user's choice). Add no other tolerance.
- Architecture rules:
  - the journal is append-only, and every derived state can be replayed from it;
  - monochrome UI, where colour only carries meaning;
  - no data in the repo, no secrets in commits.
- Change nothing on the Windows side (`.wslconfig`, firewall, services), and no `sudo`, without asking me.
- End each part with a short report: what works, what you measured, what you fixed, what remains.
