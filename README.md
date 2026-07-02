---
title: Clusters by Species Dashboard
emoji: 🧬
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# Clusters by Species Dashboard

Interactive multi-species pangenome cluster dashboard built with [Dash](https://dash.plotly.com/).

## What it shows

One tab is created automatically for each `species_statistics_<species>.json` file found in the `data/` folder. The paired `heaps_curve_data_<species>.csv` is optional and enables the Heaps curve panel.

### Panels per species tab
- Summary metric cards (proteins, clusters, genomes, GC mean, singletons)
- Cluster size distribution histogram
- Top 15 largest clusters bar chart
- Cluster class proportions pie chart (core / shell / cloud / unique …)
- Heaps curve (observed + fit) with k and gamma parameters
- Protein distribution by microbiome
- Geographic protein distribution (choropleth map)
- Sortable/filterable data tables

## Input file format

| File | Required |
|------|----------|
| `data/species_statistics_<species>.json` | ✅ |
| `data/heaps_curve_data_<species>.csv` | optional |

### JSON required keys
`total_proteins`, `total_clusters`, `gc_mean`, `total_genomes`, `mean_per_cluster`, `median_per_cluster`, `proteins_per_cluster`, `clusters_class_count`, `top15_clusters`, `microbiome_distribution`, `geoloc_distribution`

### CSV required columns
`x`, `f(x)`, `f(x) fit` — optional: `f(x) SEM`, `gama`/`gamma`, `k`
