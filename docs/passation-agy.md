# Harness — taking over lem in Antigravity (AGY)

Paste this whole file as the first message of the Claude session in Antigravity, or point it at this path.
The previous sessions were Claude Code on a Windows PC (WSL2, Ubuntu). Everything below is what a session that
has not seen the conversation needs. **Read §0–§3 before touching anything; §5 is the work, in priority order.**

## 0. What lem is

A terminal app (Python 3.12+, Textual, SQLite, FSRS, bge-m3 vectors through Ollama) to learn the whole French
lexicon and train for word-bomb (JKLM BombParty): Cards (spaced repetition with a probe, duels, sense → word),
Bomb (standard / conscious / confusables modes, sub-N prompt difficulty, `/d` hint and lookup), Syllabary (rare
sequences by heart), Verbal Memory, Word-or-not, Calibration, Atlas. Every answer goes into an **append-only
journal**; everything else (FSRS state, knowledge map θ/P(known), profile) is derived and replayable. The user is
a French native; the **data is French** (words, definitions), the user interface is being moved to English (§5.3).

## 1. Machines, paths, branches — do not mix them up

| Where | What |
|---|---|
| WSL2 Ubuntu on the PC (`DESKTOP-ALEX`), user `wsl` | everything below; open a **WSL** terminal in Antigravity (remote-WSL), never run the app from `/mnt/c` |
| `~/.lem/lem.sqlite` (2.7 GB) | **the user's real database** (journal, map, vectors). Never delete, never run experiments on it. Copy it first: `python3 -c "import sqlite3,os; s=sqlite3.connect(os.path.expanduser('~/.lem/lem.sqlite')); d=sqlite3.connect('/tmp/copy.sqlite'); s.backup(d)"` |
| `~/.lem/lem-avant-nettoyage.sqlite`, `lem-avant-recalibrage.sqlite`, `lem-v1-sauvegarde.sqlite` | backups (2.7 GB each). Keep until the user says otherwise |
| `~/.lem/cache/<hash>/` | rebuilt automatically (lexicon index, vector fingerprints, sequence counts). Safe to delete |
| `~/lem` | **the user's runtime checkout** (editable install behind the `lem` command), branch `local/jeux`. **If `lem` is running (`pgrep -af bin/lem`), do not change files there**: modules are imported lazily and a half-updated tree crashed a live session once |
| `~/lem-dev` | git worktree for development, branch `local/jeux2` → pushed to remote `local/jeux`. Work here |
| `~/lem-distill`, `~/lem-distill-data` | paused distillation project (see its report); leave `~/lem-distill-data` untouched |
| `/tmp/...` | wiped by a power cut. Keep nothing precious there |
| GitHub `AnnibalBarca/lem` | push **over SSH only**: `git push git@github.com:AnnibalBarca/lem.git <local>:<remote>`. There is no HTTPS credential and no `gh`/API token |

Branches on GitHub: `main` (old, v0 — to be replaced, §5.4) · `local/integration` (first real-data port) ·
`local/moteur` (engine v2, owned by a **cloud Claude session**: never push to it) · `local/pc`, `local/jeux`
(this line of work, based on `local/moteur`) · `local/distillation` (paused) · `local/v1`.
Before every push: `git fetch origin && git rebase origin/local/moteur`, run the tests, then push.

## 2. Rules of this repo (the user's, learned the hard way)

- **Architecture**: journal append-only; every derived state replayable (`lem rebuild`, `CarteConnaissance.rejouer`).
  Do not change the schema except additively (`CREATE TABLE IF NOT EXISTS`, `MIGRATIONS`).
- **Engine changes go through the bench first**: `lem simuler --graines 3 --eleve {faible,moyen,fort}` (≈ 12 s per
  run), compare at equal seeds, record the result in `docs/rapport-moteur.md`. Adopt only what raises retained words
  without degrading the map (Brier). One exception was taken knowingly (variety of new words, §5.1) and documented.
- **Spelling**: no variant accepted, except missing accents (`accents_tolerants`, the user's choice). No other tolerance.
- **Design**: monochrome; colour only carries meaning.
- **Words only**: no expressions, no multi-word entries, no proper nouns/acronyms/capitalised forms in the games.
- No data in the repo, no secrets in commits. Ask before touching anything on the **Windows** side (VoxCLI dictation,
  firewall, `.wslconfig`, services), and before any `sudo`.
- Commits: small, tests green (`.venv/bin/pytest -q`, ≈ 60 s, 134 tests). End each commit message with the two
  trailer lines the harness gives you (Co-Authored-By + Claude-Session) if it provides them.
- **Shell pitfalls**: the shell is zsh. `VAR=x time cmd` is a parse error (use `env VAR=x cmd`); `cmd1 && cmd2 &`
  backgrounding in a one-liner can leave nothing running — launch long jobs with a small `.sh` and `nohup`;
  heredocs inside long chained commands have swallowed whole commands before: write files with the editor tool.

## 3. Set-up in 2 minutes

```sh
cd ~/lem-dev && git status -sb            # on local/jeux2, clean
export PATH=$HOME/.local/bin:$PATH        # uv lives here
uv pip install -e ".[dev]"                # into ~/lem-dev/.venv
.venv/bin/pytest -q                       # 134 passed, 1 skipped
.venv/bin/lem --help
```
A copy of the real database for experiments: see §1. Ollama runs on the Windows side and answers on `localhost:11434`
(bge-m3 is needed for vectors; qwen3:8b is the local judge). The GPU is the user's gaming GPU: pause or unload
(`curl localhost:11434/api/generate -d '{"model":"qwen3:8b","keep_alive":0}'`) when asked.

## 4. State of the work (what is done, so you do not redo it)

Done and pushed (`local/jeux`): words-only lexicon (`lem nettoyer` removed 104 k expressions from the real base),
Grammalecte forms, blank-definition cleanup, accents tolerated by default, compact lexicon index (marisa-trie) and
vector fingerprints (RAM 4.4 → 1.6 GB), Bomb standard mode with `sub-500` difficulty / 10–15 s bomb / `/d` hint /
`/d <sequence>` and `/d <description>` lookup (`lem/recherche.py`) during and after the game, Cards balanced by
default with `diversite` and session-only `/intention`, home total counted in lemmas with the adult reference
(45 000), prior recalibration in `lem nettoyer`, `lem mots` (every word seen + six memories), keys 1–6 on the
home screen, local judge (qwen3:8b via Ollama, `/juge`). Distillation is **paused** (`docs/rapport-distillation.md`).

## 5. The work left, in priority order

### 5.1 Finish the word-bomb trainer (the user's top request)

Goal: *identify the valid words the user does not know that stem from the ideal word-bomb words* (long,
multisyllabic: *électroencéphalographie*, *anticonstitutionnalisassions*, *pseudo…parahypothyroïdies*) and build
the perfect trainer from it. Started in `lem/trainer.py` (gap analysis + greedy set cover; test
`tests/test_jeux.py::test_trainer_runs_and_orders`).

- **Problem found on the real data**: with `need(s) = w(s)·exp(−k(s)/K0)` the top words are rare foreign words
  (*sovkhoze*, *szlachta*, *kjeldahl*) closing 1–2 extremely rare sequences. That is correct for rare prompts but
  it is **not** what the user pictures. Add a **breadth** term: favour long words that answer many distinct drawable
  sequences (`IdealWord.prompts`), e.g. score = Σ need over its sequences **+ λ·(number of drawable sequences it
  answers that the user cannot answer in under N seconds)**, and rank by value per letter learned. Show both lists:
  "usable words" (attested) and "monsters" (`attested_only=False`).
- **Measure the user's usage, not just recognition**: the journal has the words typed at the Bomb (`jeu='bombe'`,
  `type='reponse'`, `correct=1`, `data.aide` absent) with latencies; build a per-sequence *answer time* profile and
  define a gap as "no known word comes out in under ~5 s". Recognition (P(known)) over-credits (see 5.2).
- **Wire it**: `lem gaps [--sub 500] [--top 25] [--words 40] [--monsters] [--csv]` (prints the weakest sequences and
  the words to learn, with definition, P(known), sequences closed); Cards intention `bombe` whose new words come
  from `trainer.ideal_entry_ids` (`_classer_candidats` in `lem/games/cartes.py`); a Bomb end-of-game line
  "words you could have played".
- Cost: `analyse` ≈ 22 s on the real base; cache the per-norm arrays on disk next to the lexicon index
  (`lem/lexique.py` shows how) so the CLI answers in a second.
- **Engine rule**: a new Cards intention does not change the planner; still run the bench before adopting anything
  that alters the default choice of new words.

### 5.2 A typed word must be *known* in the database, even in standard mode (small, do it first)

Today a spontaneous Bomb word writes an observation with weight 0.5, which moves a rare word from P = 0.18 to
0.27 — it is not "known". Producing a word on a timer is the strongest evidence of knowing it. Raise the weight
to ≈ 4 (`POIDS_PRODUIT`) in `lem/games/bombe.py` (spontaneous path, `propager=False` stays: selection bias) and in
`lem/games/syllabaire.py:269`; a quick check showed weight 4 → P ≈ 0.92. Keep `/d` hints out (`aide` words give no
evidence). Test: P before/after for a rare word; `lem mots` must show it as recognised. Bench it (map Brier).

### 5.3 Make the app English (user chose: UI, commands, docs, code; database stays French)

Translate to English: every screen string, message and placeholder (`lem/tui/*.py`, `lem/games/*.py`), `lem --help`
and command names (`lem download`, `lem ingest`, `lem words`, `lem clean`… keep the old French names as aliases),
docs, comments, docstrings, the grading prompts (`lem/grading/claude_headless.consignes`: English instructions,
French content — re-check the judge on the canary set `tests/test_distille.py::CANARIS` and `scripts` bench).
**Keep French**: table and column names, stored values (`type` = mot/compose/expression/nom_propre/sigle, traces
recep/prod/ortho, error labels like `juste_imprecise`, game ids), and the **saved setting keys** (`vies`, `temps_min`,
`sub`, `intention`…, stored in `reglage`) — renaming them would need a migration of the user's journal.
Slash commands are the setting keys (`/vies`, `/mode`…): introduce English **aliases** (`Param.alias`) rather than
renaming. Update every test that asserts a French UI string. The cloud session edits `lem/tui/app.py`,
`lem/games/bombe.py`, `lem/memory/*`: coordinate (tell the user) and do the translation as **one big commit per
area** right after a rebase to keep conflicts small.

### 5.4 Make the default GitHub branch a clean, downloadable product (Mac first)

Target: on a Mac, `git clone` → one command → playing, no manual data step.
1. Back up the old default (`git branch archive/main-v0 origin/main && git push … archive/main-v0`), then make
   `main` the product: current code of `local/jeux`, **only `README.md` (English, 30 lines: install, play, keys)
   and a LICENSE file**. Move `DESIGN.md`, `docs/*.md` out of the tracked tree of `main` (they stay on the working
   branches and in `~/lem-docs/`); the user asked for most Markdown to be removed from the default branch.
2. **The user must choose the code licence** (none exists yet) — ask, do not pick. The data are CC BY-SA 4.0
   (Wiktionary via kaikki.org, Lexique 3.83) and MPL 2.0 (Grammalecte dictionary): the README needs a short
   "Data and licences" paragraph with attribution.
3. `lem setup` (new): checks `uv`/Ollama, `ollama pull bge-m3`, downloads the **prebuilt lexicon** from the latest
   GitHub release (`…/releases/latest/download/lem-lexicon.sqlite.zst`), verifies a SHA-256, decompresses to
   `~/.lem/lem.sqlite` (refuse to overwrite an existing database), builds the caches, runs a 5-second self-test.
   Fallback when no release exists: `lem download lexique wiktionnaire grammalecte --ingest`, then `lem index`.
4. One-liner in the README: `brew install uv ollama && uv tool install git+https://github.com/AnnibalBarca/lem && lem setup && lem`.
5. **Test on the Mac** (the user's M4, 16 GB): fresh clone, empty `~/.lem`, whole flow, RAM under 4 GB.

### 5.5 The lexicon database as a release file (user chose "lexicon only")

`lem export-lexicon` (new): copy the database, drop the journal and the user's state (`evenement, notation, session,
mnemo, observation, trace_etat, theta, connaissance (keep p_prior, reset p_connu), reglage, relation 'confusion_perso'`),
keep entries, senses, forms, domains, morphemes, relations, vectors (`embedding`, float16), `VACUUM`, compress with
zstd (`zstd -19 --long`), print size and SHA-256. Expected: a few hundred MB. GitHub allows 2 GB per release file.
There is no API token here: produce the file and give the user the exact `gh release create v0.1 lem-lexicon.sqlite.zst
--notes …` command. Do **not** publish the user's own journal.

### 5.6 Native Claude Haiku judge (instead of a local model, "not everyone has compute")

Today `auto` picks the distilled model, then qwen3:8b through Ollama, then Haiku through `claude -p`
(`lem/grading/local.py:juge_depuis_reglage`, `lem/grading/claude_headless.py:JugeClaude`). Make Haiku the default
and the local model the fallback. Two honest options, implement both behind the same interface:
(a) `JugeAnthropic`: the official `anthropic` SDK with `ANTHROPIC_API_KEY`, model `claude-haiku-4-5-20251001`,
JSON-schema/tool output for the closed verdict (precision 1–5, error label), batches of 10–20 definitions per call,
prompt caching for the fixed instructions; (b) the existing `claude -p` path, which is how a **Claude subscription
login** is used (the CLI does the OAuth). **Do not read Claude Code's OAuth token file and call the API with it**:
subscription tokens are not meant for third-party apps; say so in the README and offer the API key as the "native"
route. There is no "Haiku 5.5" in the model list known here (Haiku 4.5 is the latest): use the model id from the
Anthropic docs and make it a setting. Add `/juge haiku-api`. Measure latency and cost per 100 definitions.

### 5.7 Smaller items, in order

1. Apply everything to the user's runtime when `lem` is closed: `cd ~/lem && git checkout -B local/jeux <sha>` then
   `uv tool install -e . --force`; run `lem nettoyer` is idempotent; rebuild caches by opening each game once.
2. Haiku-vs-Sonnet agreement on ~400 labelled answers (`scripts/distillation/evaluer.py --haiku 400`, data in
   `~/lem-distill-data`) — needs the user's Claude quota; decides whether distillation is ever needed.
3. Judge feedback the user asked about: add a "concision" remark and a suggested shorter definition to the verdict
   (extend the closed vocabulary in `lem/grading/taxonomie.py`; the user wrote correct but long definitions and got 4/5).
4. Cards: more than reviews — add the odd-one-out (intrus) and a DAT-style game as card types; Doom-like typing game
   (own raycaster in numpy, enemies carry sequences from `lem/suites.py` by difficulty band, no database access per
   frame — estimate: prototype in a day); ASCII mnemonic drawings (`mnemo` table exists, 0 rows, no design yet).
5. Phone/sync: `docs/passation-mobile-sync.md` (Termux, `lem sync`); parked by the user until the Mac is perfect.
6. VoxCLI (Windows dictation) restart script `C:\voxcli\restart-voxcli.cmd` leaves two copies running (its kill step
   quotes badly). It is the user's file: propose the fix, do not apply it unasked.

## 6. How to report

Short, honest, with numbers: what works, what you measured, what you fixed, what is left, and anything you could
not verify (the interface was only driven headless through Textual's pilot; colours and layout are the user's to
judge). When the user is gaming, ask before using the GPU. Never claim a step is done without running it.
