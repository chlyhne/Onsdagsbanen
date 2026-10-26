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
.venv/bin/python -m cfd.trim_2d prepare --study cfd/cases/trim_2d_fixed_twist
.venv/bin/python -u -m cfd.trim_2d run --study cfd/cases/trim_2d_fixed_twist --max-evaluations 12 --max-hours 8 --export-to cfd/cases/trim_2d_fixed_twist/handoff
```

Skip `prepare` if that study already exists. Preparation only copies small
inputs and starts no solver. The initial jib chord has **9 degrees angle of attack** relative to the
undisturbed apparent wind at the section. The main starts **3 degrees tighter**
than the jib there. Foot angles compensate for the fixed 2-degree twist. These
are starting guesses; both sheeting angles remain free during the search, and
the solver calculates the actual wind rotation from their interaction.

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
.venv/bin/python -m cfd.trim_2d status --study cfd/cases/trim_2d_fixed_twist
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

## Transfer to the other PC

When the run finishes, `--export-to` packages the best accepted geometry,
atmospheric and mesh settings, and CFD code into a folder and adjacent ZIP.
Its README contains the commands for the other PC. The geometry retains the
fixed 2-degree twist through both complete sails. No old mesh or restart is
included; the other PC must generate a fresh 3D mesh.

A budget-limited best is explicitly provisional. If no candidate passes the
checks, no transfer package is created. Previous transfer folders and ZIPs are
preserved. When resuming after an export, omit `--export-to` or use a new name.
Manual export is also available:

```bash
.venv/bin/python -m cfd.trim_2d export --study cfd/cases/trim_2d_fixed_twist --destination cfd/cases/trim_2d_fixed_twist/handoff_v2
```

No optimisation command starts 3D work locally.
