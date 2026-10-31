"""Command line for the Formlabs PreForm integration (used by scripts/prepare_print.py).

  prepare   run the PreForm API pipeline on crumb_chamber_{A,B}.stl and write .form files,
            previews, the API transcript and print_manifest.json (never prints)
  status    show whether a live PreFormServer / the SDK are available, and how to get them
  submit    human-gated print upload; refuses unless --i-confirm-print AND
            FERMENTAI_FORMLABS_CONFIRM_PRINT=yes
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .client import (
    DEFAULT_PORT,
    DOWNLOAD_URLS,
    PreFormUnavailable,
    install_instructions,
    locate_preform_server,
    open_preform,
    sdk_versions,
    server_version,
)
from .manifest import PITCH_CLAIM, build_manifest, rel, write_manifest
from .prepare import (
    CHAIN_SUMMARY,
    CONFIRM_ENV,
    CONFIRM_VALUE,
    DEFAULT_OUT_DIR,
    DEFAULT_PROFILE,
    FIXTURE,
    PROFILES,
    REPO_ROOT,
    load_chamber_inputs,
    prepare_build,
    submit_print,
)
from .replay import endpoints_used, scrub_paths


def _codesign(exe: Path | None) -> str | None:
    if exe is None or sys.platform != "darwin" or not shutil.which("codesign"):
        return None
    app = next((p for p in exe.parents if p.suffix == ".app"), exe)
    try:
        out = subprocess.run(["codesign", "-dv", "--verbose=2", str(app)], capture_output=True, check=False,
                             text=True, timeout=30).stderr
    except (OSError, subprocess.SubprocessError):
        return None
    auth = [ln.split("=", 1)[1] for ln in out.splitlines() if ln.startswith("Authority=")]
    return auth[0] if auth else None


def _home_rel(p: Path | None) -> str | None:
    if p is None:
        return None
    try:
        return "~/" + str(Path(p).relative_to(Path.home()))
    except ValueError:
        return str(p)


def cmd_prepare(args: argparse.Namespace) -> int:
    profile = PROFILES[args.profile]
    chambers = load_chamber_inputs(Path(args.chain_summary), tuple(args.chambers))
    for c in chambers:
        if not c.stl.is_file():
            print(f"ERROR: {c.stl} not found (run scripts/generate_stls.py)", file=sys.stderr)
            return 1
        flag = {True: "matches", False: "DIFFERS FROM", None: "not in"}[c.sha256_matches]
        print(f"input {c.name}: {rel(c.stl)} sha256 {c.sha256_actual[:12]}... "
              f"({flag} chain_summary.json)")
    live_out = Path(args.out) if args.out else DEFAULT_OUT_DIR
    server_log = live_out / "preformserver.log"
    try:
        ctx = open_preform(args.backend, port=args.port, server_exe=args.server_exe,
                           server_log=server_log, data_dir=Path(args.data_dir).expanduser(),
                           fixture=Path(args.fixture), keep_server=args.keep_server)
        with ctx as h:
            out_dir = Path(args.out) if args.out else (
                DEFAULT_OUT_DIR if h.backend == "live" else DEFAULT_OUT_DIR / "mock")
            print(f"backend: {h.backend}  PreForm {h.server_version} at {h.base_url}")
            for n in h.notes:
                print(f"  note: {n}")
            names = "".join(c.name for c in chambers)
            print(f"\n== build: chambers {names} on one {profile.printer_label} platform, "
                  f"{profile.material_label} @ {profile.layer_thickness_mm} mm ==")
            build = prepare_build(h.api, profile, chambers, out_dir,
                                  f"crumb_chambers_{names}_{profile.key}",
                                  orientation=args.orientation,
                                  model_spacing_mm=args.spacing, echo=print)
            per: dict[str, dict] = {}
            if args.per_chamber and len(chambers) > 1:
                for c in chambers:
                    print(f"\n== build: chamber {c.name} alone ==")
                    per[c.name] = prepare_build(h.api, profile, [c], out_dir,
                                                f"crumb_chamber_{c.name}_{profile.key}",
                                                orientation=args.orientation,
                                                model_spacing_mm=args.spacing, views=("iso",),
                                                echo=print)
            backend = h.backend
            transcript = h.transcript
            mock_requests = list(h.mock.requests) if h.mock is not None else []
            server = {
                "version": h.server_version,
                "base_url": h.base_url,
                "executable": _home_rel(h.server_exe),
                "code_signature": _codesign(h.server_exe) if backend == "live" else None,
                "started_by_fermentai": h.server_started_by_us,
                "ready_after_s": h.server_ready_after_s,
                "download_url": DOWNLOAD_URLS.get("mac-arm64"),
            }
            notes = list(h.notes)
            if h.mock is not None and h.mock.unmatched:
                notes.append(f"mock had no recording for: {sorted(set(h.mock.unmatched))}")
    except PreFormUnavailable as exc:
        print(f"\nPreForm API unavailable: {exc}", file=sys.stderr)
        return 3

    replacements = {str(REPO_ROOT): "<REPO>", str(Path.home()): "~"}
    transcript_path = None
    if backend == "live":
        transcript_path = out_dir / "preform_api_transcript.json"
        doc = {"provenance": f"real: verbatim wire transcript of live PreForm API calls to "
                             f"PreFormServer {server['version']} (recorded by RecordingProxy)",
               "recorded_by": "fermentai.formlabs.replay.RecordingProxy",
               "server_version": server["version"], "sdk": sdk_versions(),
               "endpoints_used": endpoints_used(transcript),
               "entries": scrub_paths(transcript, replacements)}
        transcript_path.write_text(json.dumps(doc, indent=1, default=str) + "\n")
        if args.update_fixture:
            FIXTURE.parent.mkdir(parents=True, exist_ok=True)
            FIXTURE.write_text(json.dumps(doc, indent=1, default=str) + "\n")
            print(f"updated mock fixture {rel(FIXTURE)}")
        used = endpoints_used(transcript)
    else:
        used = endpoints_used(mock_requests)
    api = {"sdk": sdk_versions(), "docs": "https://developer.formlabs.com/api-reference/",
           "endpoints_used": used, "http_requests": len(transcript or mock_requests)}
    if backend == "live" and server_log.is_file():
        server["log_lines"] = len(server_log.read_text(errors="replace").splitlines())
    manifest = build_manifest(
        backend=backend, profile=profile, chambers=chambers, build=build, per_chamber=per,
        server=server, api=api, transcript_path=transcript_path,
        server_log_path=server_log if backend == "live" and server_log.is_file() else None,
        mock_reason=None if backend == "live" or args.backend == "auto" else
        "replay of the recorded PreForm 3.64.2 session (--backend mock requested)",
        notes=notes)
    manifest = scrub_paths(manifest, replacements)
    mpath = write_manifest(out_dir / "print_manifest.json", manifest)

    est = build["estimates"]
    print(f"\n== {manifest['source']} ==")
    print(f"printer {profile.printer_label} ({build['scene_settings'].get('machine_type')}), "
          f"{manifest['material']['label']} ({build['scene_settings'].get('material_code')}), "
          f"{build['scene_settings'].get('layer_thickness_mm')} mm layers")
    print(f"estimated print time {est['print_time_h']} h ({est['print_time_s']} s), resin "
          f"{est['material_volume_ml']} mL (models {est['models_volume_ml']}, raft "
          f"{est['raft_volume_ml']}, supports {est['supports_volume_ml_derived']}), "
          f"{est['layer_count']} layers")
    for name, b in per.items():
        e = b["estimates"]
        print(f"  chamber {name} alone: {e['print_time_h']} h, {e['material_volume_ml']} mL")
    print(f"open crumb face up: "
          f"{ {k: v['open_face_up'] for k, v in build['models_final'].items()} }")
    for w in build.get("warnings", []):
        print(f"WARNING (PreForm printability check): {w}")
    for k, v in build["outputs"].items():
        print(f"{k:>14}: {rel(v)}")
    print(f"  .form reopen: {build['form_roundtrip']}")
    print(f"      manifest: {rel(mpath)}")
    if transcript_path:
        print(f"    transcript: {rel(transcript_path)} ({len(transcript)} HTTP requests)")
    print("job_submitted: false  -- " + PITCH_CLAIM)
    if backend != "live":
        if locate_preform_server(args.server_exe):
            print("\nThis was a MOCK replay (no PreForm ran). PreFormServer is installed: re-run "
                  "with --backend live.")
        else:
            print("\nThis was a MOCK replay (no PreForm ran). To make it live:\n"
                  + install_instructions())
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    exe = locate_preform_server(args.server_exe)
    running = server_version(f"http://localhost:{args.port}")
    print(f"SDK: {sdk_versions()}")
    print(f"PreFormServer executable: {exe or 'not found'}")
    print(f"PreFormServer on port {args.port}: {running or 'not running'}")
    if exe or running:
        print("Live PreForm API available.")
        return 0
    print(install_instructions())
    return 1


def cmd_submit(args: argparse.Namespace) -> int:
    manifest = None
    if args.manifest and Path(args.manifest).is_file():
        manifest = json.loads(Path(args.manifest).read_text())
    return submit_print(args.form, args.printer, i_confirm_print=args.i_confirm_print,
                        manifest=manifest)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="prepare_print.py",
        description="Prepare the crumb-chamber STLs for a Formlabs print with the official "
                    "PreForm API (formlabs-sdk + PreFormServer). Never prints: " + PITCH_CLAIM,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Live mode needs PreFormServer; run `status` to see how to get it.")
    sub = p.add_subparsers(dest="command")

    pp = sub.add_parser("prepare", help="orient, support, lay out, estimate and save .form")
    pp.add_argument("--backend", choices=("auto", "live", "mock"), default="auto",
                    help="live PreFormServer, mock replay of a recorded session, or auto "
                         "(live if available) [auto]")
    pp.add_argument("--profile", choices=sorted(PROFILES), default=DEFAULT_PROFILE,
                    help=f"printer/material/layer profile [{DEFAULT_PROFILE}]")
    pp.add_argument("--chambers", nargs="+", default=["A", "B"], choices=["A", "B"])
    pp.add_argument("--orientation", choices=("open_face_up", "preform_auto"),
                    default="open_face_up",
                    help="open_face_up: crumb face away from the platform, supports only on "
                         "the flat floor [default]; preform_auto: keep PreForm's auto-orient")
    pp.add_argument("--spacing", type=float, default=5.0, help="layout spacing, mm [5]")
    pp.add_argument("--no-per-chamber", dest="per_chamber", action="store_false",
                    help="skip the single-chamber builds")
    pp.add_argument("--out", help=f"output dir [{rel(DEFAULT_OUT_DIR)}, or .../mock for mock]")
    pp.add_argument("--chain-summary", default=str(CHAIN_SUMMARY))
    pp.add_argument("--port", type=int, default=DEFAULT_PORT)
    pp.add_argument("--server-exe", help="PreFormServer executable or .app "
                                         "(else $FERMENTAI_PREFORM_SERVER, cache, /Applications)")
    pp.add_argument("--data-dir", default="~/.cache/fermentai/preform-server/data",
                    help="PREFORM_ENV_DATA_DIRECTORY for a server we start")
    pp.add_argument("--fixture", default=str(FIXTURE), help="recorded session for --backend mock")
    pp.add_argument("--keep-server", action="store_true", help="leave a started server running")
    pp.add_argument("--update-fixture", action="store_true",
                    help="(live) also store the transcript as the mock fixture")
    pp.set_defaults(func=cmd_prepare)

    ps = sub.add_parser("status", help="is a live PreFormServer available? how to get one")
    ps.add_argument("--port", type=int, default=DEFAULT_PORT)
    ps.add_argument("--server-exe")
    ps.set_defaults(func=cmd_status)

    pu = sub.add_parser(
        "submit", help="HUMAN-GATED print upload (refuses without both confirmations)",
        description=f"Refuses (exit 2) unless BOTH --i-confirm-print and {CONFIRM_ENV}="
                    f"{CONFIRM_VALUE} are given. Even then it only uploads to the printer queue "
                    "(print_now=False); a person starts the print on the printer.")
    pu.add_argument("--form", required=True, help="prepared .form file")
    pu.add_argument("--printer", help="printer serial name, IP, or Fleet Control queue id")
    pu.add_argument("--manifest", default=str(DEFAULT_OUT_DIR / "print_manifest.json"))
    pu.add_argument("--i-confirm-print", action="store_true",
                    help="a human confirms a physical print may be queued")
    pu.set_defaults(func=cmd_submit)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
