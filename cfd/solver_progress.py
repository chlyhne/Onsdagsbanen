"""Stream SU2 output to its log and report iteration-budget progress."""
import subprocess
import sys

from cfd.convergence_progress import ConvergenceProgress


def run_solver(command, *, cwd, env, iterations, stream=None):
    """Run a solver explicitly; never infer progress from elapsed time."""
    if iterations < 1:
        raise ValueError("iterations must be positive")
    stream = sys.stdout if stream is None else stream
    completed = 0
    previous = None
    columns = None
    monitor = ConvergenceProgress.from_case(cwd)
    last_diagnostic = None

    def progress():
        nonlocal previous
        value = 100. if completed == iterations else min(99.9, 100 * completed / iterations)
        percent = f"{value:.1f}"
        if percent != previous:
            print(f"Solveriterationer: {percent} % ({completed}/{iterations})",
                  file=stream, flush=True)
            previous = percent

    progress()
    print("Foreloebig konvergens hver 25. iteration: residualtal <=1x opfylder graensen; "
          "kraftvariation skal vaere <0.1% over 200 iterationer. "
          "Vindfeltets stabilitet kontrolleres foerst efter koerslen.", file=stream, flush=True)
    with (cwd / "solver.log").open("w", encoding="utf8") as log:
        with subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding="utf8",
                              errors="replace", bufsize=1) as process:
            try:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    parts = [part.strip() for part in line.strip().split("|")[1:-1]]
                    if parts and parts[0] == "Inner_Iter":
                        columns = parts
                    elif columns and len(parts) == len(columns) and parts[0].isdigit():
                        try:
                            # Reject unrelated tables such as mesh/output summaries.
                            row = dict(zip(columns, map(float, parts)))
                        except ValueError:
                            continue
                        completed = max(completed, min(int(parts[0]) + 1, iterations))
                        monitor.update(row)
                        progress()
                        iteration = monitor.iteration
                        if last_diagnostic is None or iteration - last_diagnostic >= 25:
                            print(monitor.summary(), file=stream, flush=True)
                            last_diagnostic = iteration
                    elif line.strip() and not line.lstrip().startswith(("|", "+")):
                        columns = None
                code = process.wait()
                if monitor.iteration is not None and monitor.iteration != last_diagnostic:
                    print(monitor.summary(), file=stream, flush=True)
                if code:
                    raise subprocess.CalledProcessError(code, command)
            except BaseException:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                raise
    print("Solver afsluttet; iterationsprocenten angiver det maksimale budget, "
          "og konvergens kan afslutte tidligere.", file=stream, flush=True)
