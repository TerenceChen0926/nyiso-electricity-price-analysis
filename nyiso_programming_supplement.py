"""NYISO day-ahead prices: data validation, summary statistics, and plots.

Run:
    python nyiso_programming_supplement.py
    Place nyiso_day_ahead.csv beside this script.
Optional:
    python nyiso_programming_supplement.py "path/to/another.csv"
    --out-dir "path/to/results"
Dependencies: pandas, numpy, matplotlib, reportlab, pypdf, tzdata.
No prices are trimmed or imputed.
"""
import argparse
import json
import textwrap

import matplotlib
matplotlib.use("Agg")

# BEGIN_PAGE_1
# GitHub: https://github.com/TerenceChen0926/nyiso-electricity-price-analysis
# Hourly NYISO prices offer a real-world case for DST-aware time-series analysis.
# This workflow combines data cleaning, custom functions, and price visualization.
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
csv_path = Path(__file__).with_name("nyiso_day_ahead.csv")
FIELDS = {"Eastern Date Hour": "wall", "Zone Name": "zone",
          "Eastern Date Hour Time Zone": "tz", "Zone PTID": "ptid",
          "DAM Zonal LBMP": "price"}

def clean_prices(raw):
    df = raw.rename(columns=FIELDS)[list(FIELDS.values())].copy()
    wall = pd.to_datetime(df["wall"], format="%Y/%m/%d %H:%M",
                          errors="coerce")
    shift = df["tz"].map({"EDT": 4, "EST": 5})
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["ptid"] = pd.to_numeric(df["ptid"], errors="coerce")
    if wall.isna().any() or shift.isna().any() or df.isna().any().any():
        raise ValueError("Missing field, invalid time/number, or unknown zone.")
    if not np.isfinite(df[["price", "ptid"]].to_numpy()).all():
        raise ValueError("Non-finite numeric value.")
    # EDT/EST disambiguates the repeated fall-back local hour.
    df["utc"] = (wall + pd.to_timedelta(shift, unit="h")).dt.tz_localize("UTC")
    df["local"] = df["utc"].dt.tz_convert("America/New_York")
    if not df["local"].dt.tz_localize(None).equals(wall):
        raise ValueError("Time-zone label disagrees with New York time.")
    df = df.sort_values("utc").reset_index(drop=True)
    if df["utc"].duplicated().any():
        raise ValueError("Repeated UTC instant; inspect the source revisions.")
    if not df["utc"].diff().dropna().eq(pd.Timedelta(hours=1)).all():
        raise ValueError("Hourly gap; inspect the source before analysis.")
    return df  # Keep all valid observations, including price spikes.

def summarize_prices(df):
    price = df["price"]
    stats = price.agg(["mean", "median", "max"]).to_dict()
    stats["p95"] = price.quantile(0.95)  # pandas linear interpolation
    # Simple hourly averages within each New York local calendar day.
    daily = price.set_axis(df["local"]).resample("D").mean().to_frame("mean")
    # Use a full seven-day window; leave the first six values undefined.
    daily["ma7"] = daily["mean"].rolling(7, min_periods=7).mean()
    # Year-month keys keep the two partial June months separate.
    grouped = df.assign(month=df["local"].dt.strftime("%Y-%m"),
                        hour=df["local"].dt.hour)
    heat = grouped.pivot_table(index="month", columns="hour",
                              values="price", aggfunc="mean")
    return stats, daily, heat
# END_PAGE_1


def audit_source(raw, df):
    """Record full source diagnostics; fail rather than silently discard data."""
    numeric_fields = ["Zone PTID", "DAM Zonal LBMP", "DAM Zonal Losses",
                      "DAM Zonal Congestion", "DAM Zonal Price Version"]
    failures = {col: int(pd.to_numeric(raw[col], errors="coerce").isna().sum())
                for col in numeric_fields}
    if any(failures.values()):
        raise ValueError(f"Invalid numeric source fields: {failures}")
    zones = df[["zone", "ptid"]].drop_duplicates()
    if len(zones) != 1:
        raise ValueError("Multiple regions; select a region before analysis.")
    counts = df.groupby(df["local"].dt.strftime("%Y-%m")).size()
    months = []
    for label, count in counts.items():
        start = pd.Timestamp(label, tz="America/New_York")
        stop = start + pd.offsets.MonthBegin(1)
        expected = int((stop.tz_convert("UTC") - start.tz_convert("UTC"))
                       / pd.Timedelta(hours=1))
        days = df.loc[df["local"].dt.strftime("%Y-%m").eq(label), "local"]
        months.append({"month": label, "observed_hours": int(count),
                       "observed_days": int(days.dt.normalize().nunique()),
                       "expected_hours": expected, "partial": count < expected})
    repeated = raw[raw["Eastern Date Hour"].duplicated(keep=False)]
    return {
        "source_rows": len(raw), "retained_rows": len(df), "removed_rows": 0,
        "columns": list(raw.columns), "source_dtypes": raw.dtypes.astype(str).to_dict(),
        "missing_by_column": raw.isna().sum().astype(int).to_dict(),
        "numeric_conversion_failures": failures,
        "exact_duplicate_rows": int(raw.duplicated().sum()),
        "utc_duplicate_instants": int(df["utc"].duplicated().sum()),
        "non_hourly_utc_steps": int(df["utc"].diff().dropna()
                                     .ne(pd.Timedelta(hours=1)).sum()),
        "zone": str(zones.iloc[0]["zone"]),
        "ptid": int(zones.iloc[0]["ptid"]),
        "local_start": df["local"].min().isoformat(),
        "local_end": df["local"].max().isoformat(),
        "utc_start": df["utc"].min().isoformat(),
        "utc_end": df["utc"].max().isoformat(),
        "timezone_counts": raw["Eastern Date Hour Time Zone"].value_counts().to_dict(),
        "repeated_wall_hours": repeated.to_dict(orient="records"),
        "negative_prices": int(df["price"].lt(0).sum()),
        "zero_prices": int(df["price"].eq(0).sum()),
        "months": months,
    }


def run_analysis(csv_path, out_dir):
    # BEGIN_PAGE_2
    raw = pd.read_csv(csv_path)
    df = clean_prices(raw)
    stats, daily, heat = summarize_prices(df)
    high = df["price"] > stats["p95"]  # Strict, full-sample descriptive cutoff.
    print("Mean/median/p95/max (USD/MWh): " +
          "/".join(f"{stats[k]:.3f}" for k in ["mean", "median", "p95", "max"]))
    print(f"High-price hours: {high.sum():,}/{len(df):,} ({high.mean():.1%})")
    winter = df["local"].dt.strftime("%Y-%m").isin(["2026-01", "2026-02"])
    print(f"Jan-Feb 2026: {(high & winter).sum()}/{high.sum()} high-price hours")
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 3.75), layout="constrained",
                             gridspec_kw={"height_ratios": [1, 1.45]})
    axes[0].plot(daily.index, daily["mean"], lw=0.8, label="Daily mean")
    axes[0].plot(daily.index, daily["ma7"], lw=1.5, label="7-day mean")
    axes[0].set(title="A  Daily prices and trailing 7-day mean",
                ylabel="USD/MWh")
    axes[0].legend(fontsize=7, loc="upper left", frameon=False)
    image = axes[1].imshow(heat, aspect="auto", cmap="YlOrRd")
    axes[1].set(title="B  Mean price by year-month and local hour",
                xlabel="New York local hour (0-23)",
                xticks=np.arange(0, 24, 3), yticks=np.arange(len(heat)),
                yticklabels=heat.index)
    fig.colorbar(image, ax=axes[1], label="USD/MWh", pad=0.02)
    # END_PAGE_2
    
    audit = audit_source(raw, df)
    partial = {m["month"] for m in audit["months"] if m["partial"]}
    labels = [m + ("*" if m in partial else "") for m in heat.index]
    axes[1].set_yticklabels(labels)
    from matplotlib import dates as mdates
    axes[0].xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    axes[0].xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    axes[0].grid(axis="y", color="#E2E8F0", linewidth=0.5)
    axes[0].set_ylim(bottom=min(0, float(daily["mean"].min())))
    for ax in axes:
        ax.tick_params(labelsize=7, length=2)
        ax.title.set_fontsize(8.5)
        ax.title.set_weight("bold")
        ax.xaxis.label.set_fontsize(7.5)
        ax.yaxis.label.set_fontsize(7.5)
        for spine in ax.spines.values():
            spine.set_color("#CBD5E1")
    figure_path = out_dir / "nyiso_price_patterns.png"
    fig.savefig(figure_path, dpi=300)
    plt.close(fig)

    month = df["local"].dt.strftime("%Y-%m")
    winter_high = int((high & month.isin(["2026-01", "2026-02"])).sum())
    evening_high = int((high & df["local"].dt.hour.between(17, 19)).sum())
    results = {"stats": stats, "high_price_hours": int(high.sum()),
               "high_price_fraction": float(high.mean()),
               "jan_feb_high_hours": winter_high,
               "jan_feb_share_of_high_hours": winter_high / int(high.sum()),
               "hours_17_19_high_hours": evening_high,
               "hours_17_19_share_of_high_hours": evening_high / int(high.sum()),
               "audit": audit}
    with (out_dir / "nyiso_audit_results.json").open("w", encoding="utf-8") as stream:
        json.dump(results, stream, indent=2)
    daily.to_csv(out_dir / "nyiso_daily_prices.csv", index_label="local_date")
    heat.to_csv(out_dir / "nyiso_month_hour_means.csv")
    print(f"Quality: missing={sum(audit['missing_by_column'].values())}; "
          f"UTC duplicates={audit['utc_duplicate_instants']}; "
          f"UTC gaps={audit['non_hourly_utc_steps']}; removed=0")
    build_supplement(out_dir, results, figure_path)
    return results


def extract_excerpt(marker):
    source = Path(__file__).read_text(encoding="utf-8")
    # Exact executed source excerpts; export/audit/presentation helpers omitted.
    lines = source.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"# BEGIN_{marker}")
    stop = next(i for i, line in enumerate(lines) if line.strip() == f"# END_{marker}")
    return textwrap.dedent("\n".join(lines[start + 1:stop])).strip("\n")


def build_supplement(out_dir, results, figure_path):
    """Lay out exactly two Letter pages with 10-point selectable code."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import HexColor
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.utils import ImageReader
    from pypdf import PdfReader

    font_path = Path("C:/Windows/Fonts/consola.ttf")
    if font_path.exists():
        pdfmetrics.registerFont(TTFont("Code", str(font_path)))
        code_font = "Code"
    else:
        code_font = "Courier"
    pdf_path = out_dir / "NYISO_UChicago_Programming_Supplement.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=(612, 792))
    c.setTitle("NYISO Day-Ahead Electricity Prices | Python Programming Supplement")
    c.setAuthor("")
    ink, muted, blue = map(HexColor, ["#162435", "#526173", "#205A79"])
    left, width = 39, 534

    def line(text, y, size=9.5, font="Helvetica", color=ink):
        c.setFont(font, size)
        c.setFillColor(color)
        c.drawString(left, y, text)

    def code_block(source, top):
        y = top
        for row in source.splitlines():
            if pdfmetrics.stringWidth(row, code_font, 10) > width:
                raise ValueError(f"Code line exceeds page width: {row}")
            comment = row.lstrip().startswith("#")
            line(row, y, 10, code_font, muted if comment else ink)
            if row.startswith("# GitHub: "):
                c.linkURL(row.removeprefix("# GitHub: "),
                          (left, y - 2, left + pdfmetrics.stringWidth(row, code_font, 10),
                           y + 10), relative=0, thickness=0)
            y -= 12.0
        return y

    def footer(number):
        line("PYTHON PROGRAMMING SUPPLEMENT", 26, 7.5, color=muted)
        c.setFont("Helvetica", 8)
        c.drawRightString(573, 26, f"{number} / 2")

    audit, stats = results["audit"], results["stats"]
    line("NYISO Day-Ahead Electricity Prices", 754, 18, "Helvetica-Bold")
    line("How do prices vary over time, and when are unusually high prices concentrated?",
         735, 10.3, color=blue)
    line(f"Source: NYISO OASIS day-ahead zonal LBMP CSV | {audit['zone']} | PTID "
         f"{audit['ptid']}", 716, 9.2)
    line(f"{audit['source_rows']:,} hourly records | "
         f"{audit['local_start'][:10]} to {audit['local_end'][:10]} | USD/MWh", 702, 9.2)
    line("1  Parse, validate, and summarize", 677, 10.5, "Helvetica-Bold")
    y = code_block(extract_excerpt("PAGE_1"), 659)
    if y < 70:
        raise ValueError(f"Page 1 code overlaps footer: {y}")
    line("All valid prices retained. UTC validates hourly continuity; local time defines daily/hourly groups.",
         61, 8.4, color=muted)
    footer(1)
    c.showPage()

    line("Run the functions and inspect the output", 754, 15, "Helvetica-Bold")
    y = code_block(extract_excerpt("PAGE_2"), 735)
    line("ACTUAL RUN OUTPUT", y - 5, 8.7, "Helvetica-Bold", blue)
    output = [
        "Mean/median/p95/max (USD/MWh): " + "/".join(
            f"{stats[k]:.3f}" for k in ["mean", "median", "p95", "max"]),
        f"High-price hours: {results['high_price_hours']:,}/{audit['retained_rows']:,} "
        f"({results['high_price_fraction']:.1%})",
        f"Jan-Feb 2026: {results['jan_feb_high_hours']}/{results['high_price_hours']} "
        "high-price hours",
        "Quality: missing=0; UTC duplicates=0; UTC gaps=0; removed=0",
    ]
    # Audit assertions ensure that the printed quality summary is data-derived.
    assert sum(audit["missing_by_column"].values()) == 0
    assert audit["utc_duplicate_instants"] == audit["non_hourly_utc_steps"] == 0
    code_block("\n".join(output), y - 21)
    figure_top = y - 76
    figure_height = width * 3.75 / 7.2
    c.drawImage(ImageReader(str(figure_path)), left, figure_top - figure_height,
                width=width, height=figure_height)
    note_y = figure_top - figure_height - 11
    boundary = [m for m in audit["months"] if m["partial"]]
    partial_text = "; ".join(f"{m['month']} ({m['observed_days']} days)"
                             for m in boundary)
    line("* Partial months: " + partial_text + ". Prices use simple hourly means, without load weights.",
         note_y, 8.1, color=muted)
    findings = [
        f"Of the {results['high_price_hours']} hours above the full-sample 95th percentile, "
        f"{results['jan_feb_share_of_high_hours']:.1%} occurred in January-February 2026, "
        "making this period a priority for further investigation of high-price exposure.",
        "The heatmap also shows higher mean prices in late-afternoon/evening hours in several months.",
        "These are descriptive patterns in this rolling year, not causal explanations or a forecasting test.",
    ]
    finding_y = note_y - 17
    for text in findings:
        row = ""
        for word in text.split():
            candidate = f"{row} {word}".strip()
            if pdfmetrics.stringWidth(candidate, "Helvetica", 8.5) > width:
                line(row, finding_y, 8.5)
                finding_y -= 12
                row = word
            else:
                row = candidate
        line(row, finding_y, 8.5)
        finding_y -= 12
    if finding_y + 12 < 42:
        raise ValueError("Page 2 content overlaps footer.")
    footer(2)
    c.save()
    if len(PdfReader(str(pdf_path)).pages) != 2:
        raise AssertionError("The supplement must contain exactly two pages.")
    print(f"PDF: {pdf_path} (2 pages)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "csv", type=Path, nargs="?",
        default=csv_path,
        help="Input CSV; defaults to nyiso_day_ahead.csv beside this script.",
    )
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    if not args.csv.is_file():
        parser.error(f"CSV not found: {args.csv}. Pass its current path as an argument.")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    run_analysis(args.csv, args.out_dir)
