# Campaign 2: instance NOT yet released (SSH gateway unreachable)

- From about 13:20Z the Brev SSH gateway for fermentai-gpu (vjdcojwux, hyperstack_A6000) timed out
  on every attempt. This includes `ssh fermentai-gpu`, `brev exec` (3 retries) and repeated
  `brev refresh`, which all failed with "Connection timed out during banner exchange" between 13:20Z
  and 13:49Z. `brev ls` shows the instance as RUNNING, with its GPU column blank.
- The required zero-diff `rsync --dry-run` could therefore not be executed.
  `c2_rsync_dryrun.txt` is empty only because the connection failed.
- `brev delete` was NOT executed. It was blocked by the session's permission policy because the
  dry-run precondition could not be met. The instance is still running and billing about $0.60/h
  until a human deletes it.
- Evidence that the Mac copy is complete:
  1. Every remote job was launched via scripts/run_remote_alchemi.sh, which collects after the remote
     process exits.
  2. The last remote file written was logs/vib_v10b_1_20261031T131210Z.log, with mtime 13:13:41Z
     (rsync preserves mtimes). It is present locally, including its final `exit_code=0` line.
  3. No remote job was started after 13:12:21Z, and all launched jobs' logs end in exit_code=0.
  4. Local counts match the job lists: 907 campaign-2 optimisation records (607 seeded + 300
     anneal-quench), 79 vibrational analyses, 8 long-MD runs.
  5. The only thing intentionally not mirrored is the AIMNet2 checkpoint *.pt. It is publicly
     downloadable, and its sha256 is recorded in environment/remote_environment.json.
- To release:
  * If SSH comes back, run `scripts/collect_brev_results.sh`, then a dry-run
    (`rsync -azn --itemize-changes -e "ssh -T" --exclude '*.pt' fermentai-gpu:fermentai/results/ data/alchemi/`),
    then `brev delete fermentai-gpu`.
  * If SSH does not come back, decide whether to `brev delete fermentai-gpu` on the evidence above.

> **Update (2026-10-31 ~14:05 UTC):** the campaign-2 instance `fermentai-gpu` (vjdcojwux) was deleted by the orchestrator with user approval after the SSH gateway became unreachable; `brev ls` reports no instances. Final campaign-2 time ≈ 9.1 h (≈ $5.5); both campaigns ≈ $6.2 total. See environment/brev_stop.log.
