# Single-section sail trim

The study fixes both sails at **12% camber**, maximum depth at **45% chord**,
and **2 degrees of total opening twist** from foot to head. It optimises just
the main and jib sheeting angles, each bounded to **0–30 degrees at the foot**.
The 2-degree twist is distributed linearly over each sail's own height.

Each candidate runs **one 2D RANS calculation with the main, jib and mast
present together**. The default section height is 4.52 m, the sail-area
centroid of this rig. Local apparent wind and atmospheric turbulence are used.
The objective is maximum forward force, including mast drag, in **N/metre of
span**. It does not estimate a whole-rig force or optimise boat speed/VMG.

## Run locally

From the repository root, using the Python environment with CFD dependencies:

```bash
.venv/bin/python -m cfd.trim_2d prepare --study cfd/cases/trim_2d_next
.venv/bin/python -u -m cfd.trim_2d run --study cfd/cases/trim_2d_next --max-evaluations 12 --max-hours 8 --export-to cfd/trimmed_reference_v2
```

Skip `prepare` if that study already exists. Preparation only copies small
inputs and starts no solver. The initial jib chord has **9 degrees angle of attack** relative to the
undisturbed apparent wind at the section. The main starts **3 degrees tighter**
than the jib there. Foot angles compensate for the fixed 2-degree twist. These
are starting guesses; both sheeting angles remain free during the search, and
the solver calculates the actual wind rotation from their interaction.

To start a new search from the previously selected trim instead of these
default angles, pass `--initial-trim` during preparation:

```powershell
python -m cfd.trim_2d prepare --study cfd/cases/trim_2d_100_seeded --initial-trim cfd/trimmed_reference
python -u -m cfd.trim_2d run --study cfd/cases/trim_2d_100_seeded --max-evaluations 100 --max-hours 24
```

This seeds main and jib foot angles from the saved geometry (10.69° and
22.54° respectively), with the initial simplex around that trim. Fixed shape,
twist and numerical settings remain as specified for the new study. Existing
studies are not modified; use a fresh study directory when changing the seed.

The run uses two threads and calculates one candidate at a time. The budget is
12 **total** evaluations, including any completed before resuming. Wall time
is checked between solver invocations; a calculation already running may
finish. Increase `--max-evaluations` to continue a budget-limited search.

Press Ctrl+C to interrupt. Repeating the command reuses completed calculations
and restarts unfinished work from a checkpoint where possible. After a machine
or process crash, check `search.lock/owner.json` before removing a stale lock.
Do not run two writers against one study.

For a short status report:

```bash
.venv/bin/python -m cfd.trim_2d status --study cfd/cases/trim_2d_next
```

The study saves frozen settings in `study.json`, resumable state in
`search.json`, a table in `evaluations.csv`, and the best accepted geometry in
`best_geometry.json`. Individual cases, force results and detailed
`progress.log` files are under `sections/`.

## Search and checks

A bounded **Nelder–Mead** search starts with three trims about 3 degrees apart
and adjusts both sheeting angles against the combined driving force. It uses
coarse **1-degree angle and 1% force tolerances**, aiming for a useful ballpark
with few expensive calculations. The default cap is **12 distinct trim
evaluations**, including failed ones; each may require a solver continuation.
`ballpark_found` means the coarse stopping criteria were met, not that an exact
or global optimum was established. A budget-limited run retains its best trim.

Resuming deterministically replays the search using cached forces, so previous
trim evaluations do not require new CFD runs.

A candidate is accepted only when it passes the existing convergence,
field-stability and wall-resolution checks, and an independent pressure/friction
integration agrees with SU2's force output within 1%. Side force and reverse
wall shear are also recorded. There is no height integration or chord weighting.

Defaults are 3,500 iterations per invocation, one continuation if needed, and
0.06 m nominal surface resolution. To change section height or numerical
settings, prepare a new study, for example:

```bash
.venv/bin/python -m cfd.trim_2d prepare --study cfd/cases/trim_2d_finer --height 4.52 --iterations 5000 --sail-size 0.035
```

The height must intersect both sails. Changed inputs, settings or code cannot
be mixed with previous evaluations. The older four-parameter studies remain
preserved, but their results cannot be reused with this single-section objective.

The representative-section assumption deliberately omits variation through the
rig. Spanwise flow and tip vortices await the final 3D check; mesh independence
is not established by one resolution.

## Run the selected trim on the other PC

The accepted result of the 12-trial search is committed in
[`trimmed_reference/`](trimmed_reference/README.md). It contains the chosen
geometry (main foot angle 10.69 degrees, jib 22.54 degrees), 2-degree total
twist, wind and mesh settings, and the 2D check reports.

From the repository root on the other PC:

```powershell
git pull
python -m pip install -r cfd/requirements.txt
python -m cfd.solver --install
python -m cfd.prepare_compact --source cfd/trimmed_reference --case cfd/output/trimmed_3d --threads 8
python -m cfd.marine_rans run --case cfd/output/trimmed_3d --threads 8 --iterations 3000
```

Adjust the thread count for that PC. Use a new case path if trimmed_3d already
exists. The first CFD command generates a fresh mesh; the second starts 3D RANS.
The 2D result is a ballpark candidate pending that 3D check.

## Save future optimisation results in the repository

`--export-to` writes small input files and reports to the chosen folder. Add,
commit and push that folder, then pull on the other PC. Code is used directly
from the repository. Meshes, checkpoints and bulky calculation files stay local.

The existing selected inputs are preserved, so export a later result to a new
folder, for example:

```bash
.venv/bin/python -m cfd.trim_2d export --study cfd/cases/trim_2d_next --destination cfd/trimmed_reference_v3
```

If no candidate passes the checks, no input folder is created. No optimisation
or export command starts 3D work locally.
