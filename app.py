#!/usr/bin/env python3
"""
Multi-species cluster dashboard.

Reads all input files from the `data/` folder (relative to this script):
- species_statistics_<species>.json  (required; one tab per file)
- heaps_curve_data_<species>.csv     (optional; matched to the JSON suffix)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, dash_table, dcc, html

DATA_FOLDER = Path(__file__).parent / "data"

CLASS_COLOR_MAP = {
    "core": "#d62728",
    "shell": "#ff7f0e",
    "cloud": "#1f77b4",
    "unique": "#2ca02c",
    "accessory": "#9467bd",
    "singleton": "#8c564b",
    "na": "#7f7f7f",
    "missing": "#7f7f7f",
    "other": "#17becf",
}

BAR_COLOR_SEQUENCE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    "#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2",
]

REQUIRED_JSON_KEYS = {
    "total_proteins", "total_clusters", "gc_mean", "total_genomes",
    "mean_per_cluster", "median_per_cluster", "proteins_per_cluster",
    "clusters_class_count", "top15_clusters", "microbiome_distribution",
    "geoloc_distribution",
}


@dataclass(frozen=True)
class SpeciesDataset:
    species: str
    data_dir: Path
    statistics_path: Path
    heaps_path: Path | None


def _pretty_species_name(species: str) -> str:
    return species.replace("_", " ")


def _scan_species_datasets(root_dir: Path) -> list[SpeciesDataset]:
    if not root_dir.exists():
        raise FileNotFoundError(f"Data folder not found: {root_dir}")
    if not root_dir.is_dir():
        raise NotADirectoryError(f"Provided path is not a folder: {root_dir}")

    datasets: list[SpeciesDataset] = []
    prefix = "species_statistics_"
    for statistics_path in sorted(root_dir.glob(f"{prefix}*.json"), key=lambda p: p.name.lower()):
        species = statistics_path.stem[len(prefix):].strip()
        if not species:
            continue
        candidate = root_dir / f"heaps_curve_data_{species}.csv"
        datasets.append(SpeciesDataset(
            species=species,
            data_dir=root_dir,
            statistics_path=statistics_path,
            heaps_path=candidate if candidate.is_file() else None,
        ))
    return datasets


def _load_species_statistics(statistics_path: Path) -> dict[str, Any]:
    if not statistics_path.is_file():
        raise FileNotFoundError(f"Species statistics file not found: {statistics_path}")
    try:
        with statistics_path.open("r", encoding="utf-8") as f:
            statistics = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {statistics_path}: {exc}") from exc

    if not isinstance(statistics, dict):
        raise ValueError("species statistics JSON must contain a JSON object at the top level.")

    missing_keys = sorted(REQUIRED_JSON_KEYS - set(statistics))
    if missing_keys:
        raise ValueError("species statistics JSON is missing required keys: " + ", ".join(missing_keys))

    if not isinstance(statistics["proteins_per_cluster"], list):
        raise ValueError("'proteins_per_cluster' must be a numeric list.")
    if not isinstance(statistics["clusters_class_count"], dict):
        raise ValueError("'clusters_class_count' must be a JSON object.")
    if not isinstance(statistics["top15_clusters"], list):
        raise ValueError("'top15_clusters' must be a list of objects.")
    if not isinstance(statistics["microbiome_distribution"], dict):
        raise ValueError("'microbiome_distribution' must be a JSON object.")
    if not isinstance(statistics["geoloc_distribution"], dict):
        raise ValueError("'geoloc_distribution' must be a JSON object.")

    return statistics


def _to_numeric(values: Any) -> pd.Series:
    return pd.to_numeric(pd.Series(values), errors="coerce").dropna()


def _to_float_or_none(value: Any) -> float | None:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(numeric) else float(numeric)


def _to_int_or_none(value: Any) -> int | None:
    numeric = _to_float_or_none(value)
    return None if numeric is None else int(numeric)


def _fmt_number(value: int | float | None, decimals: int = 0, suffix: str = "") -> str:
    if value is None:
        return ""
    if decimals == 0:
        return f"{int(round(float(value))):,}{suffix}"
    return f"{float(value):,.{decimals}f}{suffix}"


def _canonical_class_name(value: Any) -> str:
    normalized = str(value).strip().lower()
    if not normalized or normalized in {"nan", "none", "null", "na", "n/a"}:
        return "NA"
    mapping = {"core": "Core", "shell": "Shell", "cloud": "Cloud",
               "unique": "Unique", "accessory": "Accessory", "singleton": "Singleton"}
    return mapping.get(normalized, str(value).strip())


def _class_color_for_name(class_name: str) -> str:
    return CLASS_COLOR_MAP.get(str(class_name).strip().lower(), CLASS_COLOR_MAP["other"])


def _tab_style() -> tuple[dict[str, Any], dict[str, Any]]:
    base = {
        "padding": "10px 14px", "whiteSpace": "normal", "height": "auto",
        "lineHeight": "1.2", "border": "1px solid #d7dde4", "borderRadius": "8px",
        "margin": "0 6px 6px 0", "backgroundColor": "#f2f6fa",
    }
    return base, {**base, "backgroundColor": "#ffffff", "border": "1px solid #9ab7d3", "fontWeight": "bold"}


def _metric_card(title: str, value: str, subtitle: str | None = None) -> html.Div:
    children = [
        html.H4(title, style={"margin": "0 0 6px 0", "fontSize": "13px", "color": "#1f3b5b"}),
        html.H2(value, style={"margin": 0, "fontSize": "24px"}),
    ]
    if subtitle:
        children.append(html.Div(subtitle, style={"marginTop": "6px", "fontSize": "12px", "color": "#555"}))
    return html.Div(children, style={
        "padding": "14px", "border": "1px solid #d7dde4", "borderRadius": "8px",
        "background": "#fff", "minWidth": "180px", "boxShadow": "0 1px 2px rgba(0,0,0,0.04)",
    })


def _warning_box(message: str, details: str | None = None) -> html.Div:
    children: list[Any] = [html.Div(message)]
    if details:
        children.append(html.Pre(details, style={"whiteSpace": "pre-wrap", "margin": "8px 0 0"}))
    return html.Div(children, style={
        "padding": "12px", "border": "1px solid #e7c783", "borderRadius": "8px",
        "background": "#fff8e8", "color": "#6f4600", "fontSize": "13px",
    })


def _distribution_dataframe(mapping: dict[str, Any], label: str, value_label: str) -> pd.DataFrame:
    records = [{label: str(k), value_label: v} for k, v in mapping.items()]
    df = pd.DataFrame(records)
    if df.empty:
        return df
    df[value_label] = pd.to_numeric(df[value_label], errors="coerce")
    return df.dropna(subset=[value_label]).sort_values(value_label, ascending=False)


def _load_heaps_curve_data(heaps_path: Path | None) -> tuple[pd.DataFrame, float | None, float | None, Path]:
    if heaps_path is None or not heaps_path.is_file():
        raise FileNotFoundError("Matching heaps_curve_data_<species>.csv was not found.")
    heaps = pd.read_csv(heaps_path)
    for col in ["x", "f(x)", "f(x) SEM", "f(x) fit", "gama", "gamma", "k"]:
        if col in heaps.columns:
            heaps[col] = pd.to_numeric(heaps[col], errors="coerce")

    gamma_col = "gama" if "gama" in heaps.columns else ("gamma" if "gamma" in heaps.columns else None)
    gamma_value = None
    if gamma_col:
        vals = heaps[gamma_col].dropna().unique()
        if len(vals):
            gamma_value = float(vals[0])

    k_value = None
    if "k" in heaps.columns:
        vals = heaps["k"].dropna().unique()
        if len(vals):
            k_value = float(vals[0])

    return heaps, k_value, gamma_value, heaps_path


def _build_heaps_figure(heaps_path: Path | None) -> tuple[go.Figure | None, list[html.Div], str | None, Path | None]:
    try:
        heaps, k_value, gamma_value, heaps_path = _load_heaps_curve_data(heaps_path)
    except Exception as exc:
        return None, [], f"Heaps curve unavailable: {exc}", None

    missing_cols = {"x", "f(x)", "f(x) fit"} - set(heaps.columns)
    if missing_cols:
        return None, [], "heaps_curve_data.csv is missing columns: " + ", ".join(sorted(missing_cols)), heaps_path

    valid = heaps.dropna(subset=["x", "f(x)", "f(x) fit"]).sort_values("x")
    if valid.empty:
        return None, [], "Heaps curve has no valid numeric rows.", heaps_path

    observed_kwargs: dict[str, Any] = {
        "x": valid["x"], "y": valid["f(x)"], "mode": "markers+lines",
        "name": "Observed curve", "marker": {"size": 7},
    }
    if "f(x) SEM" in valid.columns:
        observed_kwargs["error_y"] = {"type": "data", "array": valid["f(x) SEM"].fillna(0), "visible": True}

    fig = go.Figure()
    fig.add_trace(go.Scatter(**observed_kwargs))
    fig.add_trace(go.Scatter(x=valid["x"], y=valid["f(x) fit"], mode="lines",
                             name="Heaps fit", line={"dash": "dash", "width": 2}))

    title_parts = []
    if k_value is not None:
        title_parts.append(f"k={k_value:.4f}")
    if gamma_value is not None:
        title_parts.append(f"gamma={gamma_value:.4f}")
    title = "Heaps curve" + (" (" + ", ".join(title_parts) + ")" if title_parts else "")

    fig.update_layout(title=title, xaxis_title="Number of genomes (x)",
                      yaxis_title="Number of clusters P(x)", template="plotly_white",
                      height=420, margin={"l": 50, "r": 20, "t": 60, "b": 45})

    cards = []
    if k_value is not None:
        cards.append(_metric_card("Heaps k", f"{k_value:.6f}"))
    if gamma_value is not None:
        cards.append(_metric_card("Heaps gamma", f"{gamma_value:.6f}"))

    return fig, cards, None, heaps_path


def _build_species_dashboard(dataset: SpeciesDataset) -> dict[str, Any]:
    statistics = _load_species_statistics(dataset.statistics_path)
    proteins_per_cluster = _to_numeric(statistics["proteins_per_cluster"])
    if proteins_per_cluster.empty:
        raise ValueError("'proteins_per_cluster' contains no numeric values.")

    total_clusters = _to_int_or_none(statistics.get("total_clusters"))
    total_proteins = _to_int_or_none(statistics.get("total_proteins"))
    total_genomes = _to_int_or_none(statistics.get("total_genomes"))
    mean_per_cluster = _to_float_or_none(statistics.get("mean_per_cluster"))
    median_per_cluster = _to_float_or_none(statistics.get("median_per_cluster"))
    gc_mean = _to_float_or_none(statistics.get("gc_mean"))

    singleton_count = int((proteins_per_cluster == 1).sum())
    singleton_pct = (100 * singleton_count / len(proteins_per_cluster)) if len(proteins_per_cluster) else None
    largest_cluster = int(proteins_per_cluster.max())

    cards = [
        _metric_card("Clustered proteins", _fmt_number(total_proteins)),
        _metric_card("Clusters", _fmt_number(total_clusters)),
        _metric_card("Represented genomes", _fmt_number(total_genomes)),
        _metric_card("Mean per cluster", _fmt_number(mean_per_cluster, decimals=2), "Proteins per cluster"),
        _metric_card("Median per cluster", _fmt_number(median_per_cluster), "Proteins per cluster"),
        _metric_card("Largest cluster", _fmt_number(largest_cluster), "Proteins"),
        _metric_card("Singletons", _fmt_number(singleton_count),
                     f"{_fmt_number(singleton_pct, decimals=1, suffix='%')} of clusters" if singleton_pct is not None else ""),
        _metric_card("Mean GC", _fmt_number(gc_mean, decimals=2, suffix="%"), "Genome average"),
    ]

    fig_sizes = px.histogram(
        pd.DataFrame({"cluster_size": proteins_per_cluster}), x="cluster_size",
        nbins=min(50, max(10, int(proteins_per_cluster.nunique()))),
        title="Cluster size distribution",
        labels={"cluster_size": "Proteins per cluster", "count": "Number of clusters"},
        template="plotly_white",
    )
    fig_sizes.update_layout(height=420, margin={"l": 50, "r": 20, "t": 60, "b": 45})

    top_clusters = pd.DataFrame(statistics["top15_clusters"])
    expected = {"representative", "cluster_size"}
    if not expected.issubset(top_clusters.columns):
        raise ValueError("'top15_clusters' is missing expected columns: " + ", ".join(sorted(expected - set(top_clusters.columns))))
    top_clusters = (top_clusters.copy()
                    .assign(cluster_size=lambda df: pd.to_numeric(df["cluster_size"], errors="coerce"))
                    .dropna(subset=["cluster_size"])
                    .sort_values("cluster_size", ascending=False)
                    .head(15).reset_index(drop=True))

    fig_largest = px.bar(top_clusters, x="representative", y="cluster_size", color="representative",
                         title="Top 15 largest clusters",
                         labels={"representative": "Representative", "cluster_size": "Number of proteins"},
                         color_discrete_sequence=BAR_COLOR_SEQUENCE, template="plotly_white")
    fig_largest.update_layout(height=420, margin={"l": 50, "r": 20, "t": 60, "b": 115}, showlegend=False)
    fig_largest.update_xaxes(tickangle=-35)

    class_counts = _distribution_dataframe(statistics["clusters_class_count"], "class", "clusters")
    if not class_counts.empty:
        class_counts["class"] = class_counts["class"].apply(_canonical_class_name)
    class_cards = [_metric_card(f"{r['class']} clusters", f"{int(r['clusters']):,}") for r in class_counts.to_dict("records")]

    fig_class, class_message = None, None
    if class_counts.empty:
        class_message = "No valid data in 'clusters_class_count'."
    else:
        color_map = {n: _class_color_for_name(n) for n in class_counts["class"].unique()}
        fig_class = px.pie(class_counts, names="class", values="clusters",
                           title="Cluster class proportions", hole=0.25,
                           color="class", color_discrete_map=color_map, template="plotly_white")
        fig_class.update_traces(textposition="inside", textinfo="percent+label")
        fig_class.update_layout(height=420, margin={"l": 35, "r": 20, "t": 60, "b": 40})

    microbiome = _distribution_dataframe(statistics["microbiome_distribution"], "microbiome", "proteins")
    fig_microbiome, microbiome_message = None, None
    if microbiome.empty:
        microbiome_message = "No valid data in 'microbiome_distribution'."
    else:
        fig_microbiome = px.bar(microbiome, x="microbiome", y="proteins", color="microbiome",
                                title="Protein distribution by microbiome",
                                labels={"microbiome": "Microbiome", "proteins": "Proteins"},
                                color_discrete_sequence=BAR_COLOR_SEQUENCE, template="plotly_white")
        fig_microbiome.update_layout(height=420, margin={"l": 50, "r": 20, "t": 60, "b": 110}, showlegend=False)
        fig_microbiome.update_xaxes(tickangle=-35)

    geography = _distribution_dataframe(statistics["geoloc_distribution"], "country", "proteins")
    fig_geography, geography_message = None, None
    if geography.empty:
        geography_message = "No valid data in 'geoloc_distribution'."
    else:
        fig_geography = px.choropleth(geography, locations="country", locationmode="country names",
                                      color="proteins", hover_name="country",
                                      hover_data={"proteins": True, "country": False},
                                      title="Geographic protein distribution",
                                      labels={"proteins": "Proteins"}, color_continuous_scale="Viridis")
        fig_geography.update_geos(showframe=False, showcoastlines=True, projection_type="natural earth")
        fig_geography.update_layout(height=460, margin={"l": 20, "r": 20, "t": 60, "b": 20})

    fig_heaps, heaps_cards, heaps_message, resolved_heaps = _build_heaps_figure(dataset.heaps_path)

    used_files = [f"Statistics JSON: {dataset.statistics_path.name}"]
    used_files.append(f"Heaps CSV: {resolved_heaps.name}" if resolved_heaps else f"Heaps CSV: not found (expected heaps_curve_data_{dataset.species}.csv)")

    return {
        "cards": [*cards, *class_cards, *heaps_cards],
        "figure_sizes": fig_sizes, "figure_largest": fig_largest, "top_clusters": top_clusters,
        "figure_class": fig_class, "class_message": class_message,
        "figure_microbiome": fig_microbiome, "microbiome_message": microbiome_message, "microbiome_table": microbiome,
        "figure_geography": fig_geography, "geography_message": geography_message, "geography_table": geography,
        "figure_heaps": fig_heaps, "heaps_message": heaps_message, "used_files": used_files,
    }


def _dataset_summary_table(datasets: list[SpeciesDataset]) -> pd.DataFrame:
    rows = []
    for ds in datasets:
        row: dict[str, Any] = {
            "species": ds.species,
            "statistics_file": ds.statistics_path.name,
            "has_matching_heaps_csv": ds.heaps_path is not None,
            "heaps_file": ds.heaps_path.name if ds.heaps_path else "Not found",
            "total_proteins": "NA", "total_clusters": "NA", "total_genomes": "NA", "gc_mean_percent": "NA",
        }
        try:
            s = _load_species_statistics(ds.statistics_path)
            row.update({
                "total_proteins": int(s["total_proteins"]),
                "total_clusters": int(s["total_clusters"]),
                "total_genomes": int(s["total_genomes"]),
                "gc_mean_percent": round(float(s["gc_mean"]), 2),
            })
        except Exception as exc:
            row["json_error"] = str(exc)
        rows.append(row)
    return pd.DataFrame(rows).sort_values("species") if rows else pd.DataFrame()


def _graph_or_warning(figure: go.Figure | None, message: str | None, flex: str = "1 1 48%") -> html.Div:
    if figure is not None:
        return html.Div([dcc.Graph(figure=figure)], style={"flex": flex})
    return html.Div([_warning_box(message or "Data unavailable for this chart.")],
                    style={"flex": flex, "marginTop": "12px"})


def _build_species_tab(dataset: SpeciesDataset) -> dcc.Tab:
    label = _pretty_species_name(dataset.species)
    tab_style, selected_tab_style = _tab_style()
    try:
        d = _build_species_dashboard(dataset)
        heaps_status = (f"Matching Heaps file: {dataset.heaps_path.name}" if dataset.heaps_path
                        else f"Matching Heaps file was not found: heaps_curve_data_{dataset.species}.csv")
        children: list[Any] = [
            html.H2(label, style={"marginBottom": "4px"}),
            html.P(f"Statistics file: {dataset.statistics_path.name}. {heaps_status}",
                   style={"color": "#666", "fontSize": "13px", "marginTop": "0px"}),
            html.Div(d["cards"], style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "marginBottom": "14px"}),
            html.Div([_graph_or_warning(d["figure_sizes"], None), _graph_or_warning(d["figure_largest"], None)],
                     style={"display": "flex", "gap": "12px", "flexWrap": "wrap"}),
            html.Div([_graph_or_warning(d["figure_class"], d["class_message"]), _graph_or_warning(d["figure_heaps"], d["heaps_message"])],
                     style={"display": "flex", "gap": "12px", "flexWrap": "wrap"}),
            html.Div([_graph_or_warning(d["figure_microbiome"], d["microbiome_message"]), _graph_or_warning(d["figure_geography"], d["geography_message"])],
                     style={"display": "flex", "gap": "12px", "flexWrap": "wrap"}),
            html.H4("Top 15 largest clusters"),
            dash_table.DataTable(
                columns=[{"name": c, "id": c} for c in d["top_clusters"].columns],
                data=d["top_clusters"].to_dict("records"), page_size=15,
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "left", "padding": "6px", "fontSize": "12px", "maxWidth": "520px"},
                style_header={"backgroundColor": "#eef3f8", "fontWeight": "bold"},
            ),
            html.H4("Microbiome distribution"),
            dash_table.DataTable(
                columns=[{"name": c, "id": c} for c in d["microbiome_table"].columns],
                data=d["microbiome_table"].to_dict("records"), page_size=10,
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "left", "padding": "6px", "fontSize": "12px"},
                style_header={"backgroundColor": "#eef3f8", "fontWeight": "bold"},
            ),
            html.H4("Geographic distribution"),
            dash_table.DataTable(
                columns=[{"name": c, "id": c} for c in d["geography_table"].columns],
                data=d["geography_table"].to_dict("records"), page_size=10,
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "left", "padding": "6px", "fontSize": "12px"},
                style_header={"backgroundColor": "#eef3f8", "fontWeight": "bold"},
            ),
        ]
    except Exception as exc:
        children = [
            html.H2(label, style={"marginBottom": "4px"}),
            html.P(f"Statistics file: {dataset.statistics_path.name}", style={"color": "#666", "fontSize": "13px"}),
            _warning_box("Could not build this species dashboard.", str(exc)),
        ]
    return dcc.Tab(label=label, value=f"tab-{dataset.species}", children=children,
                   style=tab_style, selected_style=selected_tab_style)


def _build_overview_tab(root_dir: Path, datasets: list[SpeciesDataset]) -> dcc.Tab:
    summary = _dataset_summary_table(datasets)
    json_count = len(datasets)
    heaps_count = int(summary["has_matching_heaps_csv"].sum()) if not summary.empty else 0
    tab_style, selected_tab_style = _tab_style()

    children: list[Any] = [
        html.H2("Folder summary"),
        html.Div([
            _metric_card("Species statistics JSON files", f"{json_count:,}"),
            _metric_card("Matching Heaps CSV files", f"{heaps_count:,}"),
            _metric_card("Missing Heaps CSV files", f"{max(json_count - heaps_count, 0):,}"),
        ], style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "marginBottom": "14px"}),
    ]

    if summary.empty:
        children.append(_warning_box("No species statistics JSON file was found.",
                                     "Expected filenames such as species_statistics_Bacillus_subtilis.json."))
    else:
        children.append(dash_table.DataTable(
            columns=[{"name": c, "id": c} for c in summary.columns],
            data=summary.to_dict("records"), page_size=25,
            sort_action="native", filter_action="native",
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "left", "padding": "6px", "fontSize": "12px", "maxWidth": "480px"},
            style_header={"backgroundColor": "#eef3f8", "fontWeight": "bold"},
        ))

    return dcc.Tab(label="Overview", value="tab-overview", children=children,
                   style=tab_style, selected_style=selected_tab_style)


def build_app() -> Dash:
    root_dir = DATA_FOLDER.resolve()
    app = Dash(__name__)
    app.title = "Clusters by species"

    try:
        datasets = _scan_species_datasets(root_dir)
        tabs = [_build_overview_tab(root_dir, datasets)]
        tabs.extend(_build_species_tab(ds) for ds in datasets)
        default_tab = tabs[1].value if len(tabs) > 1 else tabs[0].value
        content: list[Any] = [
            html.H1("Clusters by species"),
            html.P("One tab per species_statistics_<species>.json found in the data/ folder. "
                   "The paired heaps_curve_data_<species>.csv is optional.",
                   style={"color": "#444", "marginTop": "0px"}),
            dcc.Tabs(id="species-tabs", value=default_tab, children=tabs,
                     parent_style={"display": "flex", "flexWrap": "wrap", "alignItems": "center"},
                     style={"display": "flex", "flexWrap": "wrap", "alignItems": "center", "borderBottom": "none"},
                     content_style={"paddingTop": "10px", "width": "100%"}),
        ]
    except Exception as exc:
        content = [
            html.H1("Clusters by species"),
            _warning_box("Could not build the dashboard.", str(exc)),
        ]

    app.layout = html.Div(content, style={
        "padding": "16px", "fontFamily": "Segoe UI, Tahoma, sans-serif", "background": "#f7f9fc",
    })
    return app


app = build_app()
server = app.server  # required for Hugging Face Spaces (gunicorn)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)
