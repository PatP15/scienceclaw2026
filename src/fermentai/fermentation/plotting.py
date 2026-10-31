"""Charts for fermentation predictions (matplotlib PNG + self-contained Plotly HTML).

All charts carry a visible "SIMULATED" provenance label. Observations, when passed, are drawn
as markers labelled "observed" so the same functions produce predicted-vs-observed chart sets
once real pH / rise data exist.

API: ``chamber_pngs(...)``, ``chamber_html(...)``, ``comparison_pngs(...)``,
``dashboard_png(...)``, ``dashboard_html(...)``. Inputs are the DataFrames from
``simulate`` (nominal) and the ``envelope`` DataFrame from ``simulate_with_uncertainty``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Validated categorical slots 1-2 (dataviz reference palette; CVD dE 24.7 light).
CHAMBER_COLORS = {"A": "#2a78d6", "B": "#eb6834"}
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e1e0d9"
WINDOW_FILL = "#ecebe6"
WARN = "#d03b3b"  # status "critical" step, always paired with a text label

PROV_TEXT = "SIMULATED — model prediction, not a measurement"

PANELS = [
    # key, title, y label
    ("temperature_c", "Dough temperature", "°C"),
    ("ph", "pH", "pH"),
    ("acids", "Lactic & acetic acid", "g / kg dough"),
    ("co2_cumulative", "Modelled cumulative CO₂ production", "g CO₂ / kg dough"),
    ("expansion_ratio", "Dough expansion", "V/V₀ (≈ relative height)"),
]
CO2_NOTE = "Model CO₂ only. The BME680 measures gas resistance (VOCs), not CO₂."


def _style_ax(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9, length=0, pad=4)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _window(ax, window):
    if window:
        ax.axvspan(window[0], window[1], color=WINDOW_FILL, zorder=0, lw=0)


def _prov(fig, text=PROV_TEXT):
    fig.text(
        0.995,
        0.005,
        text,
        ha="right",
        va="bottom",
        fontsize=8,
        color="white",
        bbox=dict(boxstyle="round,pad=0.35", fc=INK_2, ec="none"),
    )


def _band(ax, env, var, color, label=None):
    lo, hi = f"{var}_p05", f"{var}_p95"
    if env is not None and lo in env:
        ax.fill_between(env.time_h, env[lo], env[hi], color=color, alpha=0.13, lw=0, label=label)


def _peak(df):
    i = int(np.argmax(df.expansion_ratio.to_numpy()))
    return float(df.time_h.iloc[i]), float(df.expansion_ratio.iloc[i])


def _onset(df, thr=0.5):
    idx = np.nonzero(df.overproof_risk.to_numpy() >= thr)[0]
    return float(df.time_h.iloc[idx[0]]) if len(idx) else None


def _draw_panel(ax, key, runs, window, observations=None, annotate=True):
    """runs: list of (chamber_id, nominal_df, envelope_df)."""
    _window(ax, window)
    multi = len(runs) > 1
    # label placement order for expansion annotations: earlier events go left, later go right
    peak_rank = {cid: r for r, (cid, _, _) in enumerate(sorted(runs, key=lambda x: _peak(x[1])[0]))}
    onset_rank = {
        cid: r for r, (cid, _, _) in enumerate(sorted(runs, key=lambda x: _onset(x[1]) or 1e9))
    }
    leader = dict(arrowstyle="-", color=INK_2, lw=0.7, shrinkA=0, shrinkB=4)
    for cid, df, env in runs:
        c = CHAMBER_COLORS.get(cid, INK)
        if key == "acids":
            _band(ax, env, "lactic_acid", c)
            _band(ax, env, "acetic_acid", c)
            ax.plot(
                df.time_h,
                df.lactic_acid,
                color=c,
                lw=2,
                solid_capstyle="round",
                label=f"{cid} lactic",
            )
            ax.plot(df.time_h, df.acetic_acid, color=c, lw=2, ls=(0, (4, 2)), label=f"{cid} acetic")
            if annotate:
                ax.annotate(
                    f"{cid} lactic",
                    (df.time_h.iloc[-1], df.lactic_acid.iloc[-1]),
                    xytext=(4, 0),
                    textcoords="offset points",
                    fontsize=8,
                    color=INK_2,
                    va="center",
                )
                if not multi:
                    ax.annotate(
                        f"{cid} acetic",
                        (df.time_h.iloc[-1], df.acetic_acid.iloc[-1]),
                        xytext=(4, 0),
                        textcoords="offset points",
                        fontsize=8,
                        color=INK_2,
                        va="center",
                    )
        else:
            _band(ax, env, key, c)
            ax.plot(df.time_h, df[key], color=c, lw=2, label=f"Chamber {cid}")
        if key == "expansion_ratio" and annotate:
            tp, ep = _peak(df)
            ax.plot([tp], [ep], "o", ms=8, color=c, mec=SURFACE, mew=2, zorder=5)
            if multi:
                left = peak_rank[cid] == 0
                xy_off, ha = ((-28, 26), "right") if left else ((28, 26), "left")
            else:
                xy_off, ha = (0, 12), "center"
            ax.annotate(
                f"{cid} peak {ep:.2f}× @ {tp:.1f} h",
                (tp, ep),
                xytext=xy_off,
                textcoords="offset points",
                ha=ha,
                fontsize=8,
                color=INK,
                arrowprops=leader if multi else None,
            )
            on = _onset(df)
            if on is not None and on <= df.time_h.iloc[-1]:
                y = float(np.interp(on, df.time_h, df.expansion_ratio))
                ax.plot([on], [y], marker="v", ms=9, color=WARN, mec=SURFACE, mew=1.5, zorder=6)
                if multi:
                    left = onset_rank[cid] == 0
                    xy_off, ha = ((-22, -30), "right") if left else ((22, -30), "left")
                else:
                    xy_off, ha = (0, -16), "center"
                ax.annotate(
                    f"{cid} overproof risk ≥ {on:.1f} h",
                    (on, y),
                    xytext=xy_off,
                    textcoords="offset points",
                    ha=ha,
                    fontsize=8,
                    color=WARN,
                    arrowprops=leader if multi else None,
                )
        if key == "ph" and annotate:
            ax.annotate(
                f"{df.ph.iloc[-1]:.2f}",
                (df.time_h.iloc[-1], df.ph.iloc[-1]),
                xytext=(4, 0),
                textcoords="offset points",
                fontsize=8,
                color=INK_2,
                va="center",
            )
    if key == "acids" and multi and annotate:
        top = max(runs, key=lambda r: r[1].acetic_acid.iloc[-1])[1]
        ax.annotate(
            "acetic (dashed)",
            (top.time_h.iloc[-1], top.acetic_acid.iloc[-1]),
            xytext=(4, 4),
            textcoords="offset points",
            fontsize=8,
            color=INK_2,
            va="bottom",
        )
    if observations is not None:
        for cid, obs in observations.items():
            col = "lactic_acid" if key == "acids" else key
            if obs is not None and col in obs and obs[col].notna().any():
                o = obs.dropna(subset=[col])
                ax.plot(
                    o.time_h,
                    o[col],
                    "o",
                    ms=6,
                    mfc="white",
                    mec=CHAMBER_COLORS.get(cid, INK),
                    mew=2,
                    label=f"{cid} observed",
                    zorder=7,
                )


def _head(fig, title, sub):
    """Figure-level title + subtitle; returns the top of the free area for tight_layout."""
    fig.text(0.012, 0.975, title, fontsize=12.5, fontweight="bold", color=INK, va="top", ha="left")
    fig.text(0.012, 0.905, sub, fontsize=8, color=INK_2, va="top", ha="left", linespacing=1.4)
    return 0.80 if "\n" in sub else 0.85


def _finish(ax, title, ylab, xlab=True):
    ax.set_title(title, loc="left", fontsize=11, color=INK, fontweight="bold")
    ax.set_ylabel(ylab, color=INK_2, fontsize=9)
    if xlab:
        ax.set_xlabel("Time since mixing (h)", color=INK_2, fontsize=9)


def chamber_pngs(
    cid: str,
    nominal: pd.DataFrame,
    envelope: pd.DataFrame | None,
    out_dir: Path,
    window=(12.0, 16.0),
    observations: pd.DataFrame | None = None,
    n_samples: int | None = None,
    dpi: int = 160,
) -> list[Path]:
    """One PNG per variable for a chamber; returns file paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    band_note = f"band: 5–95 % of {n_samples} parameter samples" if n_samples else "band: 5–95 %"
    for key, title, ylab in PANELS:
        fig, ax = plt.subplots(figsize=(7.2, 4.0), facecolor=SURFACE)
        _style_ax(ax)
        _draw_panel(
            ax,
            key,
            [(cid, nominal, envelope)],
            window,
            observations={cid: observations} if observations is not None else None,
        )
        _finish(ax, "", ylab)
        head = f"Chamber {cid} · {title}"
        sub = f"shaded column = 12–16 h observation window · {band_note}"
        if key == "co2_cumulative":
            sub += "\n" + CO2_NOTE
        if key == "acids":
            sub += " · solid = lactic, dashed = acetic"
        top = _head(fig, head, sub)
        if observations is not None:
            ax.legend(frameon=False, fontsize=8)
        _prov(fig)
        fig.tight_layout(rect=(0, 0.03, 1, top))
        p = out_dir / f"chamber_{cid}_{key}.png"
        fig.savefig(p, dpi=dpi, facecolor=SURFACE)
        plt.close(fig)
        paths.append(p)
    return paths


def comparison_pngs(
    runs: list[tuple[str, pd.DataFrame, pd.DataFrame | None]],
    out_dir: Path,
    window=(12.0, 16.0),
    n_samples: int | None = None,
    dpi: int = 160,
) -> list[Path]:
    """A-vs-B overlay PNG per variable."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for key, title, ylab in PANELS:
        fig, ax = plt.subplots(figsize=(7.6, 4.2), facecolor=SURFACE)
        _style_ax(ax)
        _draw_panel(ax, key, runs, window)
        _finish(ax, "", ylab)
        head = f"Chamber A vs B · {title}"
        sub = "A: 22 °C mild hypothesis · B: 27 °C more-acidic hypothesis · grey = 12–16 h window"
        if n_samples:
            sub += f" · bands 5–95 % ({n_samples} samples)"
        if key == "co2_cumulative":
            sub += "\n" + CO2_NOTE
        top = _head(fig, head, sub)
        ax.legend(frameon=False, fontsize=8, ncol=2 if key == "acids" else 1, loc="best")
        _prov(fig)
        fig.tight_layout(rect=(0, 0.03, 1, top))
        p = out_dir / f"compare_{key}.png"
        fig.savefig(p, dpi=dpi, facecolor=SURFACE)
        plt.close(fig)
        paths.append(p)
    return paths


def _summary_rows(summaries: Mapping[str, dict]) -> list[tuple[str, ...]]:
    def f(v, fmt):
        return "—" if v is None else format(v, fmt)

    rows = [
        ("pH at 12 h", "ph_at_12h", ".2f"),
        ("pH at 16 h", "ph_at_16h", ".2f"),
        ("Lactic at 16 h (g/kg)", "lactic_at_16h", ".1f"),
        ("Acetic at 16 h (g/kg)", "acetic_at_16h", ".2f"),
        ("Peak rise (V/V₀)", "peak_expansion_ratio", ".2f"),
        ("Peak rise time (h)", "peak_expansion_time_h", ".1f"),
        ("Time to 2× (h)", "time_to_double_h", ".1f"),
        ("Overproof risk from (h)", "overproof_onset_h", ".1f"),
    ]
    ids = list(summaries)
    return [(lab, *[f(summaries[c].get(k), fmt) for c in ids]) for lab, k, fmt in rows]


def dashboard_png(
    runs,
    summaries: Mapping[str, dict],
    path: Path,
    window=(12.0, 16.0),
    n_samples: int | None = None,
    dpi: int = 170,
) -> Path:
    """Dashboard-style 2x3 multi-panel A-vs-B figure with a key-numbers table."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 3, figsize=(16, 9), facecolor=SURFACE)
    order = ["expansion_ratio", "ph", "acids", "temperature_c", "co2_cumulative"]
    meta = {k: (t, y) for k, t, y in PANELS}
    for ax, key in zip(axes.flat, order):
        _style_ax(ax)
        _draw_panel(ax, key, runs, window)
        _finish(ax, meta[key][0], meta[key][1])
        if key == "co2_cumulative":
            ax.text(0, -0.25, CO2_NOTE, transform=ax.transAxes, fontsize=7.5, color=INK_2)
        if key == "expansion_ratio":
            ax.legend(frameon=False, fontsize=9, loc="lower right")
    # key-numbers table
    ax = axes.flat[5]
    ax.axis("off")
    ids = list(summaries)
    rows = _summary_rows(summaries)
    ax.text(
        0,
        1.0,
        "Key numbers (nominal parameters)",
        fontsize=11,
        fontweight="bold",
        color=INK,
        transform=ax.transAxes,
        va="top",
    )
    y0, dy = 0.88, 0.085
    for j, cid in enumerate(ids):
        x = 0.48 + 0.26 * j
        ax.add_patch(
            plt.Rectangle(
                (x, y0 - 0.018),
                0.03,
                0.036,
                transform=ax.transAxes,
                color=CHAMBER_COLORS.get(cid, INK),
            )
        )
        ax.text(
            x + 0.045,
            y0,
            f"Chamber {cid}",
            transform=ax.transAxes,
            fontsize=9,
            color=INK,
            va="center",
            fontweight="bold",
        )
    for i, row in enumerate(rows):
        y = y0 - (i + 1) * dy
        ax.plot([0, 1], [y - dy / 2, y - dy / 2], color=GRID, lw=0.8, transform=ax.transAxes)
        ax.text(0, y, row[0], transform=ax.transAxes, fontsize=9, color=INK_2, va="center")
        for j, v in enumerate(row[1:]):
            ax.text(
                0.48 + 0.26 * j + 0.12,
                y,
                v,
                transform=ax.transAxes,
                fontsize=10,
                color=INK,
                va="center",
                ha="center",
            )
    fig.suptitle(
        "FermentAI · Chamber A (22 °C, mild hypothesis) vs Chamber B (27 °C, more-acidic hypothesis)",
        x=0.01,
        ha="left",
        fontsize=15,
        fontweight="bold",
        color=INK,
        y=0.985,
    )
    sub = (
        "Hypotheses, not guaranteed flavour outcomes · lines = nominal model, bands = 5–95 % "
        f"parameter uncertainty{f' ({n_samples} samples)' if n_samples else ''} · grey = 12–16 h window · "
        "pH ≠ total acidity ≠ perceived sourness"
    )
    fig.text(0.01, 0.948, sub, fontsize=9.5, color=INK_2, ha="left")
    _prov(fig, PROV_TEXT + " · ODE model, configs/kinetics.yaml")
    fig.tight_layout(rect=(0, 0.03, 1, 0.94))
    fig.savefig(path, dpi=dpi, facecolor=SURFACE)
    plt.close(fig)
    return path


# ------------------------------------------------------------------------------------- Plotly


def _rgba(hex_color: str, a: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{a})"


def _plotly_band(fig, env, var, color, row, col, name, legendgroup, show=False):
    lo, hi = f"{var}_p05", f"{var}_p95"
    if env is None or lo not in env:
        return
    fig.add_trace(
        go.Scatter(
            x=env.time_h,
            y=env[hi],
            mode="lines",
            line=dict(width=0),
            hoverinfo="skip",
            showlegend=False,
            legendgroup=legendgroup,
        ),
        row=row,
        col=col,
    )
    fig.add_trace(
        go.Scatter(
            x=env.time_h,
            y=env[lo],
            mode="lines",
            line=dict(width=0),
            fill="tonexty",
            fillcolor=_rgba(color, 0.14),
            name=name,
            legendgroup=legendgroup,
            showlegend=show,
            hovertemplate="5–95 %: %{y:.3g}<extra>" + name + "</extra>",
        ),
        row=row,
        col=col,
    )


def _plotly_panel(fig, key, runs, row, col, first, window, observations=None):
    for cid, df, env in runs:
        c = CHAMBER_COLORS.get(cid, INK)
        grp = f"ch{cid}"
        if key == "acids":
            _plotly_band(fig, env, "lactic_acid", c, row, col, f"{cid} lactic 5–95 %", grp)
            _plotly_band(fig, env, "acetic_acid", c, row, col, f"{cid} acetic 5–95 %", grp)
            fig.add_trace(
                go.Scatter(
                    x=df.time_h,
                    y=df.lactic_acid,
                    line=dict(color=c, width=2),
                    name=f"{cid} lactic",
                    legendgroup=grp,
                    showlegend=False,
                    hovertemplate="%{y:.2f} g/kg<extra>" + f"{cid} lactic</extra>",
                ),
                row=row,
                col=col,
            )
            fig.add_trace(
                go.Scatter(
                    x=df.time_h,
                    y=df.acetic_acid,
                    line=dict(color=c, width=2, dash="dash"),
                    name=f"{cid} acetic",
                    legendgroup=grp,
                    showlegend=False,
                    hovertemplate="%{y:.2f} g/kg<extra>" + f"{cid} acetic</extra>",
                ),
                row=row,
                col=col,
            )
        else:
            _plotly_band(fig, env, key, c, row, col, f"Chamber {cid} 5–95 %", grp, show=first)
            fig.add_trace(
                go.Scatter(
                    x=df.time_h,
                    y=df[key],
                    line=dict(color=c, width=2),
                    name=f"Chamber {cid}",
                    legendgroup=grp,
                    showlegend=first,
                    hovertemplate="t=%{x:.1f} h · %{y:.3g}<extra>" + f"Chamber {cid}</extra>",
                ),
                row=row,
                col=col,
            )
        if key == "expansion_ratio":
            tp, ep = _peak(df)
            fig.add_trace(
                go.Scatter(
                    x=[tp],
                    y=[ep],
                    mode="markers+text",
                    text=[f"{cid} peak {ep:.2f}× @ {tp:.1f} h"],
                    textposition="top center",
                    textfont=dict(color=INK, size=11),
                    marker=dict(color=c, size=10, line=dict(color=SURFACE, width=2)),
                    showlegend=False,
                    legendgroup=grp,
                    hoverinfo="skip",
                ),
                row=row,
                col=col,
            )
            on = _onset(df)
            if on is not None:
                y = float(np.interp(on, df.time_h, df.expansion_ratio))
                fig.add_trace(
                    go.Scatter(
                        x=[on],
                        y=[y],
                        mode="markers+text",
                        text=[f"{cid} overproof risk ≥ {on:.1f} h"],
                        textposition="bottom center",
                        textfont=dict(color=WARN, size=11),
                        marker=dict(
                            color=WARN,
                            size=11,
                            symbol="triangle-down",
                            line=dict(color=SURFACE, width=1.5),
                        ),
                        showlegend=False,
                        legendgroup=grp,
                        hoverinfo="skip",
                    ),
                    row=row,
                    col=col,
                )
    if observations:
        for cid, obs in observations.items():
            ycol = "lactic_acid" if key == "acids" else key
            if obs is not None and ycol in obs and obs[ycol].notna().any():
                o = obs.dropna(subset=[ycol])
                fig.add_trace(
                    go.Scatter(
                        x=o.time_h,
                        y=o[ycol],
                        mode="markers",
                        name=f"{cid} observed",
                        marker=dict(
                            color="white",
                            size=9,
                            line=dict(color=CHAMBER_COLORS.get(cid, INK), width=2),
                        ),
                    ),
                    row=row,
                    col=col,
                )
    if window:
        fig.add_vrect(
            x0=window[0],
            x1=window[1],
            fillcolor=WINDOW_FILL,
            opacity=0.6,
            layer="below",
            line_width=0,
            row=row,
            col=col,
        )


def _plotly_layout(fig, title, subtitle, height):
    fig.update_layout(
        title=dict(
            text=f"<b>{title}</b><br><span style='font-size:12px;color:{INK_2}'>{subtitle}</span>",
            x=0.01,
        ),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family="Helvetica, Arial, sans-serif", color=INK),
        height=height,
        hovermode="x unified",
        margin=dict(t=110, l=60, r=30, b=60),
        legend=dict(
            orientation="h", y=1.0, yanchor="bottom", x=1, xanchor="right", font=dict(size=11)
        ),
    )
    fig.update_xaxes(
        showgrid=True,
        gridcolor=GRID,
        zeroline=False,
        linecolor=GRID,
        title_text="Time since mixing (h)",
        title_font=dict(size=11, color=INK_2),
        tickfont=dict(color=INK_2),
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor=GRID,
        zeroline=False,
        linecolor=GRID,
        title_font=dict(size=11, color=INK_2),
        tickfont=dict(color=INK_2),
    )
    fig.add_annotation(
        text=PROV_TEXT,
        xref="paper",
        yref="paper",
        x=1,
        y=1.11,
        xanchor="right",
        showarrow=False,
        font=dict(color="white", size=11),
        bgcolor=INK_2,
        borderpad=4,
    )


def chamber_html(
    cid, nominal, envelope, path, window=(12.0, 16.0), observations=None, n_samples=None
) -> Path:
    """Self-contained Plotly HTML (plotly.js inlined) with all five panels for one chamber."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    titles = [f"{t} ({y})" for _, t, y in PANELS]
    fig = make_subplots(
        rows=len(PANELS), cols=1, shared_xaxes=True, vertical_spacing=0.05, subplot_titles=titles
    )
    for i, (key, _, _) in enumerate(PANELS, start=1):
        _plotly_panel(
            fig,
            key,
            [(cid, nominal, envelope)],
            i,
            1,
            i == 1,
            window,
            {cid: observations} if observations is not None else None,
        )
    sub = (
        f"lines = nominal model · bands = 5–95 % of {n_samples or '?'} parameter samples · grey = 12–16 h "
        f"window · acids: solid lactic, dashed acetic · {CO2_NOTE}"
    )
    _plotly_layout(fig, f"Chamber {cid} · simulated fermentation", sub, 1500)
    fig.write_html(path, include_plotlyjs=True, full_html=True)
    return path


def dashboard_html(
    runs, summaries: Mapping[str, dict], path, window=(12.0, 16.0), n_samples=None
) -> Path:
    """Self-contained Plotly dashboard: A vs B, 2x3 panels incl. a key-numbers table."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    order = ["expansion_ratio", "ph", "acids", "temperature_c", "co2_cumulative"]
    meta = {k: (t, y) for k, t, y in PANELS}
    specs = [[{}, {}, {}], [{}, {}, {"type": "table"}]]
    titles = [meta[k][0] for k in order] + ["Key numbers (nominal parameters)"]
    fig = make_subplots(
        rows=2,
        cols=3,
        specs=specs,
        subplot_titles=titles,
        vertical_spacing=0.14,
        horizontal_spacing=0.07,
    )
    for i, key in enumerate(order):
        r, c = divmod(i, 3)
        _plotly_panel(fig, key, runs, r + 1, c + 1, i == 0, window)
        fig.update_yaxes(title_text=meta[key][1], row=r + 1, col=c + 1)
    ids = list(summaries)
    rows = _summary_rows(summaries)
    fig.add_trace(
        go.Table(
            header=dict(
                values=["", *[f"<b>Chamber {c}</b>" for c in ids]],
                fill_color=[SURFACE, *[CHAMBER_COLORS.get(c, INK) for c in ids]],
                font=dict(color=[INK, *["white"] * len(ids)], size=12),
                align="left",
                line_color=GRID,
            ),
            cells=dict(
                values=[[r[0] for r in rows], *[[r[j + 1] for r in rows] for j in range(len(ids))]],
                fill_color=SURFACE,
                font=dict(color=INK, size=12),
                align="left",
                line_color=GRID,
                height=28,
            ),
        ),
        row=2,
        col=3,
    )
    sub = (
        "A: 22 °C mild hypothesis · B: 27 °C more-acidic hypothesis (hypotheses, not guaranteed flavour "
        f"outcomes) · bands = 5–95 % ({n_samples or '?'} samples) · grey = 12–16 h window · {CO2_NOTE}"
    )
    _plotly_layout(fig, "FermentAI · Chamber A vs Chamber B (simulated)", sub, 900)
    fig.write_html(path, include_plotlyjs=True, full_html=True)
    return path
