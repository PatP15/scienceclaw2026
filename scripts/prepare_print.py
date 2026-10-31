"""Prepare the crumb-chamber STLs for a Formlabs print with the official PreForm API.

  uv run python scripts/prepare_print.py status                 # live PreFormServer available?
  uv run python scripts/prepare_print.py prepare                # live if possible, else mock
  uv run python scripts/prepare_print.py prepare --backend live # require the real server
  uv run python scripts/prepare_print.py prepare --backend mock # replay recorded session
  uv run python scripts/prepare_print.py submit --form F.form --printer NAME   # refuses unless
        --i-confirm-print AND FERMENTAI_FORMLABS_CONFIRM_PRINT=yes (human gate)

Outputs (live): data/stl/print/*.form, *_iso.png, *_top.png, print_manifest.json,
preform_api_transcript.json, preformserver.log. Mock outputs go to data/stl/print/mock/.
See docs/formlabs_workflow.md.
"""

from fermentai.formlabs.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
