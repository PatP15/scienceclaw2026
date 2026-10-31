"""Runtime settings for the FermentAI API (env vars + configs/hardware.yaml)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("fermentai.api")

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TOKEN = "change-me-local-device-token"


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (KEY=VALUE, '#' comments). Never overrides existing env vars."""
    path = path or REPO_ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].split("\t#", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def _resolve(p: str | os.PathLike) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


# --------------------------------------------------------------------------- hardware.yaml


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ChamberCfg(_Cfg):
    label: str = ""
    device_id: str = ""
    # Every device id allowed to write this chamber (real + simulated stand-ins).
    device_ids: list[str] = Field(default_factory=list)
    initial_setpoint_c: float = 22.0

    def all_device_ids(self) -> list[str]:
        return list(dict.fromkeys([d for d in [self.device_id, *self.device_ids] if d]))


class TemperatureCfg(_Cfg):
    min_setpoint_c: float = 4.0
    max_setpoint_c: float = 35.0
    max_step_c_per_update: float = 3.0
    min_update_interval_s: float = 5.0
    hint_deadband_c: float = 0.5


class CommandsCfg(_Cfg):
    ttl_s: int = 90
    reissue_when_remaining_s: int = 45


class MotorCfg(_Cfg):
    max_duration_s: float = 10.0
    default_duration_s: float = 5.0
    ttl_s: int = 60
    stop_directive_window_s: int = 30
    patterns: list[str] = Field(default_factory=lambda: ["wiggle", "nod", "spin"])


class TelemetryCfg(_Cfg):
    recommended_interval_s: int = 10
    stale_after_s: int = 60
    clock_skew_flag_s: int = 300


class PollingCfg(_Cfg):
    desired_control_interval_s: int = 5


class ImagesCfg(_Cfg):
    max_bytes: int = 2_000_000
    recommended_interval_s: int = 300


class ThinkingPulsesCfg(_Cfg):
    motor_on_thinking: bool = True
    pulse_ms: int = 2500
    min_gap_s: float = 1.0
    max_pulses_per_cycle: int = 4
    pattern: str = "nod"


class RequestLimitsCfg(_Cfg):
    telemetry_max_bytes: int = 64 * 1024
    ack_max_bytes: int = 16 * 1024
    image_max_bytes: int = 2_000_000 + 64 * 1024   # JPEG + multipart overhead
    default_max_bytes: int = 1_000_000


class CaptureCfg(_Cfg):
    ttl_s: int = 60


class TasteFeedbackCfg(_Cfg):
    apply_first_schedule_step: bool = False
    motor_duration_s: float = 3.0
    motor_pattern: str = "wiggle"


class HardwareConfig(_Cfg):
    schema_version: str = "1.0"
    chambers: dict[str, ChamberCfg] = Field(
        default_factory=lambda: {
            "A": ChamberCfg(label="Chamber A", initial_setpoint_c=22.0),
            "B": ChamberCfg(label="Chamber B", initial_setpoint_c=27.0),
        }
    )
    temperature: TemperatureCfg = Field(default_factory=TemperatureCfg)
    commands: CommandsCfg = Field(default_factory=CommandsCfg)
    motor: MotorCfg = Field(default_factory=MotorCfg)
    telemetry: TelemetryCfg = Field(default_factory=TelemetryCfg)
    polling: PollingCfg = Field(default_factory=PollingCfg)
    images: ImagesCfg = Field(default_factory=ImagesCfg)
    taste_feedback: TasteFeedbackCfg = Field(default_factory=TasteFeedbackCfg)
    request_limits: RequestLimitsCfg = Field(default_factory=RequestLimitsCfg)
    thinking_pulses: ThinkingPulsesCfg = Field(default_factory=ThinkingPulsesCfg)
    capture_image: CaptureCfg = Field(default_factory=CaptureCfg)
    firmware_local_limits: dict[str, float] = Field(default_factory=dict)

    def device_bindings(self) -> dict[str, str]:
        """device_id -> chamber_id for every device listed in hardware.yaml."""
        out: dict[str, str] = {}
        for cid, c in self.chambers.items():
            for d in c.all_device_ids():
                if d in out and out[d] != cid:
                    raise ValueError(f"hardware.yaml: device {d!r} bound to two chambers")
                out[d] = cid
        return out

    def validate_sane(self) -> None:
        t = self.temperature
        if not (t.min_setpoint_c < t.max_setpoint_c):
            raise ValueError("hardware.yaml: min_setpoint_c must be < max_setpoint_c")
        if t.max_step_c_per_update <= 0:
            raise ValueError("hardware.yaml: max_step_c_per_update must be > 0")
        self.device_bindings()
        if self.motor.max_duration_s <= 0 or self.motor.default_duration_s > self.motor.max_duration_s:
            raise ValueError("hardware.yaml: motor durations invalid")


def load_hardware_config(path: str | os.PathLike | None = None) -> HardwareConfig:
    path = _resolve(path or os.environ.get("FERMENTAI_HARDWARE_CONFIG", "configs/hardware.yaml"))
    if path.is_file():
        data = yaml.safe_load(path.read_text()) or {}
        cfg = HardwareConfig.model_validate(data)
    else:
        log.warning("hardware config %s not found; using built-in defaults", path)
        cfg = HardwareConfig()
    cfg.validate_sane()
    return cfg


# --------------------------------------------------------------------------- settings


@dataclass
class Settings:
    api_token: str
    db_path: Path
    data_dir: Path
    hardware: HardwareConfig
    dashboard_static_dir: Path
    findings_path: Path | None = None
    llm_mode: str = "offline"
    token_is_default: bool = False
    # chamber_id -> per-device token (env FERMENTAI_DEVICE_TOKEN_<CHAMBER>); optional.
    device_tokens: dict[str, str] = field(default_factory=dict)
    # Where fermentai.agents.chat keeps sessions (None = the module's default / env).
    chat_sessions_path: Path | None = None
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, **overrides) -> Settings:
        load_dotenv()
        token = os.environ.get("FERMENTAI_API_TOKEN", "").strip()
        token_is_default = not token or token == DEFAULT_TOKEN
        if not token:
            token = DEFAULT_TOKEN
        data_dir = _resolve(os.environ.get("FERMENTAI_DATA_DIR", "data"))
        hardware = load_hardware_config()
        device_tokens = {
            cid: tok.strip() for cid in hardware.chambers
            if (tok := os.environ.get(f"FERMENTAI_DEVICE_TOKEN_{cid.upper()}", "").strip())
        }
        findings = os.environ.get("FERMENTAI_FINDINGS_PATH")
        kwargs = dict(
            api_token=token,
            db_path=_resolve(os.environ.get("FERMENTAI_DB_PATH", "data/fermentai.sqlite3")),
            data_dir=data_dir,
            hardware=hardware,
            device_tokens=device_tokens,
            dashboard_static_dir=REPO_ROOT / "src" / "fermentai" / "dashboard" / "static",
            findings_path=_resolve(findings) if findings else None,
            llm_mode=os.environ.get("FERMENTAI_LLM_MODE", "offline"),
            token_is_default=token_is_default,
        )
        kwargs.update(overrides)
        return cls(**kwargs)
