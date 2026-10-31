"""Formlabs PreForm API integration (official ``formlabs-sdk`` + PreFormServer).

Prepares the crumb-chamber STLs for an SLA print — orient, support, lay out, check, estimate,
save .form, render — and never prints without two explicit human confirmations.
See docs/formlabs_workflow.md.
"""

from .client import (
    DEFAULT_PORT,
    PreFormHandle,
    PreFormServerProcess,
    PreFormUnavailable,
    install_instructions,
    locate_preform_server,
    open_preform,
    server_version,
)
from .manifest import PITCH_CLAIM, SOURCE_LIVE, SOURCE_MOCK, build_manifest, check_manifest
from .prepare import (
    CONFIRM_ENV,
    PROFILES,
    PrintProfile,
    load_chamber_inputs,
    prepare_build,
    print_confirmation_status,
    submit_print,
)
from .replay import MockPreFormServer, RecordingProxy

__all__ = [
    "CONFIRM_ENV", "DEFAULT_PORT", "PITCH_CLAIM", "PROFILES", "SOURCE_LIVE", "SOURCE_MOCK",
    "MockPreFormServer", "PreFormHandle", "PreFormServerProcess", "PreFormUnavailable",
    "PrintProfile", "RecordingProxy", "build_manifest", "check_manifest", "install_instructions",
    "load_chamber_inputs", "locate_preform_server", "open_preform", "prepare_build",
    "print_confirmation_status", "server_version", "submit_print",
]
