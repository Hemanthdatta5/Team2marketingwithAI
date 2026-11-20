"""Read-only, full-dataset exploration. Run: .venv/bin/python explore/explore.py.

Writes exploration summaries and a plot to the project root. Does not clean data,
create model features, or fit models. CSV types are inferred on reading.
"""
from pathlib import Path
import json
import os
import tempfile
import argparse

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "brewery-matplotlib"))
import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd
from plotnine import (ggplot, aes, geom_col, labs, theme_minimal, scale_x_continuous,
                      geom_tile, geom_line, geom_point, geom_segment, geom_vline,
                      facet_wrap, scale_fill_gradient, scale_y_continuous)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "brewery_sales_cleaned.csv"
CONTINUOUS = ["Alcohol_Content", "Quality_Score", "Fermentation_Time"]
NUMERIC = ["Total_Sales", *CONTINUOUS]
CATEGORIES = ["Beer_Style", "SKU", "Location"]


def markdown_table(frame):
    """Keep report generation independent of optional table packages."""
    frame = frame.reset_index().astype(str)
    lines = ["| " + " | ".join(frame.columns) + " |",
             "| " + " | ".join(["---"] * len(frame.columns)) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in frame.itertuples(index=False, name=None))
    return "\n".join(lines)


def main():
    source_before = SOURCE.stat()
    cleaning = json.loads((ROOT / "brewery_sales_cleaning_log.json").read_text())
    validation = json.loads((ROOT / "brewery_sales_final_validation.json").read_text())
    missing = None
    types = {}
    counts = {column: pd.Series(dtype="int64") for column in CATEGORIES}
    numeric_chunks = []
    lowest, highest = pd.DataFrame(), pd.DataFrame()
    rows = complete = 0
    for chunk in pd.read_csv(SOURCE, chunksize=250_000):
        rows += len(chunk)
        missing = chunk.isna().sum() if missing is None else missing + chunk.isna().sum()
        for column in chunk:
            types.setdefault(column, set()).add(str(chunk[column].dtype))
        for column in CATEGORIES:
            counts[column] = counts[column].add(chunk[column].value_counts(dropna=False), fill_value=0)
        numeric_chunks.append(chunk[NUMERIC])
        complete += int(chunk[NUMERIC + CATEGORIES].notna().all(axis=1).sum())
        lowest = pd.concat([lowest, chunk.nsmallest(5, "Total_Sales")]).nsmallest(5, "Total_Sales")
        highest = pd.concat([highest, chunk.nlargest(5, "Total_Sales")]).nlargest(5, "Total_Sales")
        if rows % 1_000_000 == 0:
            print(f"Read {rows:,} rows", flush=True)

    # Retain only four numeric columns for exact full-data quantiles and correlations.
    numeric = pd.concat(numeric_chunks, ignore_index=True)
    del numeric_chunks
    schema = pd.DataFrame({"dtype": {c: ", ".join(sorted(t)) for c, t in types.items()},
                           "missing": missing})
    schema.index.name = "variable"
    descriptive = numeric.describe(percentiles=[.01, .25, .5, .75, .99]).T
    descriptive.index.name = "variable"
    frequencies = {}
    for column, count in counts.items():
        frequencies[column] = pd.DataFrame({"count": count.astype("int64"), "percent": count / rows * 100}).sort_index()
        frequencies[column].index.name = column
        assert int(count.sum()) == rows

    sales = numeric["Total_Sales"]
    q1, q3 = sales.quantile([.25, .75])
    iqr = q3 - q1
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    distribution = {
        "skewness": float(sales.skew()),
        "excess_kurtosis": float(sales.kurt()),
        "lower_1.5_IQR_fence": float(lower), "upper_1.5_IQR_fence": float(upper),
        "below_lower_fence": int((sales < lower).sum()),
        "above_upper_fence": int((sales > upper).sum()),
        "nonpositive_sales": int((sales <= 0).sum()),
        "sales_sum": float(sales.sum()),
    }
    correlations = numeric[CONTINUOUS].corr(method="pearson")
    correlations.index.name = "predictor"
    hist_counts, edges = np.histogram(sales.dropna(), bins=40)
    histogram = pd.DataFrame({"midpoint": (edges[:-1] + edges[1:]) / 2,
                              "count": hist_counts})
    assert int(hist_counts.sum()) == int(sales.count())
    plot = (ggplot(histogram, aes("midpoint", "count"))
            + geom_col(width=float(edges[1] - edges[0]), fill="#246A82")
            + theme_minimal()
            + scale_x_continuous(labels=lambda values: [f"{v:,.0f}" for v in values])
            + labs(title="Distribution of Total Sales", subtitle=f"All {rows:,} rows · 40 equal-width bins",
                   x="Total Sales (source units; currency unspecified)", y="Number of observations"))
    plot.save(ROOT / "brewery_sales_exploration_distribution.png", width=9, height=5, dpi=160, verbose=False)

    source_after = SOURCE.stat()
    assert (source_before.st_size, source_before.st_mtime_ns) == (source_after.st_size, source_after.st_mtime_ns)
    required = {c: c in schema.index for c in NUMERIC + CATEGORIES}
    report = {
        "source": SOURCE.name, "rows": rows, "columns": len(schema),
        "method": "Full CSV scan; exact quantiles; sample SD (ddof=1); pandas adjusted skewness and excess kurtosis; Pearson correlations.",
        "schema": schema.to_dict(orient="index"),
        "descriptive_statistics": descriptive.to_dict(orient="index"),
        "frequencies": {c: f.to_dict(orient="index") for c, f in frequencies.items()},
        "sales_distribution": distribution,
        "lowest_sales_records": lowest.to_dict(orient="records"),
        "highest_sales_records": highest.to_dict(orient="records"),
        "continuous_predictor_correlations": correlations.to_dict(orient="index"),
        "required_variables_available": required, "complete_model_rows": complete,
        "previous_report_comparison": {
            "row_count_matches": rows == cleaning["rows_after"] == validation["rows"],
            "missing_counts_match": missing.to_dict() == validation["missing_counts"],
            "sales_total_matches_within_0.01": bool(abs(sales.sum() - validation["total_sales"]) < .01),
        },
        "source_size_and_mtime_unchanged": True,
    }
    (ROOT / "brewery_sales_exploration.json").write_text(json.dumps(report, indent=2))
    sections = ["# Brewery sales exploration", f"Source: `{SOURCE.name}`. All {rows:,} rows and {len(schema)} columns inspected.",
                report["method"],
                "## Prior preprocessing\n\nThe supplied reports retain all 10 million rows, parse dates/numeric values, and report no missing values or exact duplicate rows. No imputation, capping, row deletion, or category collapsing was performed. The 160 million conversion count refers to field values, not distinct rows. Sales definition and sentinel meanings remain unresolved. The validation JSON claims unique Batch_IDs, but the existing validation code hardcodes that claim and the cleaning code checks whole-row duplicates, so ID uniqueness is not independently established here.",
                "## Variables and missingness\n\n" + markdown_table(schema),
                "Brew_Date is read as text from CSV; prior date parsing is documented in the cleaning report. CSV does not preserve native date types.",
                "## Descriptive statistics\n\n" + markdown_table(descriptive.round(6))]
    for column, frame in frequencies.items():
        sections.append(f"## {column} frequencies\n\n" + markdown_table(frame.round(4)))
    sections += ["## Total Sales distribution\n\n" + markdown_table(pd.DataFrame.from_dict(distribution, orient="index", columns=["value"]).round(6)),
                 "![Total Sales histogram](brewery_sales_exploration_distribution.png)",
                 "### Five lowest sales observations\n\n" + markdown_table(lowest[["Batch_ID", "Total_Sales"]].reset_index(drop=True)),
                 "### Five highest sales observations\n\n" + markdown_table(highest[["Batch_ID", "Total_Sales"]].reset_index(drop=True)),
                 "These are observed endpoints, not automatically errors or influential observations. The 1.5-IQR rule describes marginal sales extremes; regression influence cannot be assessed before fitting a model.",
                 "## Continuous predictor correlations\n\n" + markdown_table(correlations.round(6)),
                 "Only the three proposed continuous predictors are included. Pairwise correlations do not establish absence of multicollinearity involving categorical encodings or future interaction terms. No auxiliary regressions or VIF models were fitted.",
                 f"## Proposed model availability\n\nAll required variables available: {all(required.values())}. Complete rows across outcome and six predictors: {complete:,}. Outcome: Total_Sales. Categorical predictors: Beer_Style, SKU, Location. Numeric predictors: Alcohol_Content, Quality_Score, Fermentation_Time.",
                 "## Scope\n\nNo dataset writes, preprocessing, new model features, or regression estimation. Summary tables and histogram bins are descriptive outputs only. Existing instructions and preprocessing reports were not changed."]
    sections.append("## Findings and decisions before modeling\n\nTotal Sales is approximately uniform across its observed range, with skewness 0.000685 and excess kurtosis -1.200620. There are no nonpositive sales or observations beyond the 1.5-IQR fences. The endpoint observations alone provide no reason to remove or cap records. The three proposed numeric predictors have negligible pairwise Pearson correlations (maximum absolute correlation 0.000124), so this check gives no indication of pairwise linear multicollinearity.\n\n1. Confirm data provenance and measurement definitions. The flat sales distribution and nearly equal category frequencies are compatible with synthetic generation, but do not prove it. Confirm whether sales are observed or simulated, their currency and reporting period, alcohol and fermentation units, and how quality is scored.\n2. Confirm what one row represents and whether observations are independent across batches, locations, and time. The existing code does not substantiate the validation report's separate Batch_ID uniqueness claim. Decide whether temporal or grouped dependence needs to be addressed later.\n3. Choose meaningful reference levels for Beer_Style, SKU, and Location. Treat these as nominal categories, using k-1 indicator columns with an intercept at the modeling stage. Fermentation_Time has integer values from 10 to 19; decide whether a linear per-unit effect is substantively appropriate.\n4. Start by considering sales in its original units. The marginal distribution gives no skewness-based reason for a log transformation. Regression assumptions concern conditional errors; assess residual shape, linearity, variance, and influence after fitting.\n5. Decide whether production volume or time should be controls based on the research question. Volume_Produced and Brew_Date are available but are not part of the six proposed predictors. Do not add controls automatically or treat associations as causal effects.\n6. Prespecify a small set of motivated interactions, such as Beer_Style by SKU or Beer_Style by Quality_Score. Retain their main effects and consider centering numeric predictors when implementing interactions. Check category combinations and the full design matrix for collinearity at that stage; marginal frequencies do not establish joint-cell support.\n7. With 10 million observations, prioritize effect sizes, uncertainty, and practical business relevance over p-values alone. Agree on what constitutes a meaningful sales difference before modeling.\n")
    (ROOT / "brewery_sales_exploration.md").write_text("\n\n".join(sections) + "\n")
    print(json.dumps({"rows": rows, "distribution": distribution, "correlations": report["continuous_predictor_correlations"], "verification": report["previous_report_comparison"]}, indent=2))


def relationships():
    """One bounded-memory pass; no row-level dataset or model features are saved."""
    before = SOURCE.stat()
    previous = json.loads((ROOT / "brewery_sales_exploration.json").read_text())
    # The earlier observed range sizes the exact 10 MB ID presence array.
    # Every ID is range/type checked below; uniqueness is NOT assumed from reports.
    validation = json.loads((ROOT / "brewery_sales_final_validation.json").read_text())
    id_min = int(validation["numeric_minimums"]["Batch_ID"])
    id_max = int(validation["numeric_maximums"]["Batch_ID"])
    if id_max - id_min > 50_000_000:
        raise ValueError("ID range too wide for this bounded-memory presence check")
    seen = np.zeros(id_max - id_min + 1, dtype=bool)
    duplicates = 0
    observed_min, observed_max = id_max, id_min
    n = 0
    mean = np.zeros(4)
    cross = np.zeros((4, 4))
    groups = {c: pd.DataFrame(columns=["count", "sum", "sumsq"], dtype=float) for c in CATEGORIES}
    x_edges = {"Alcohol_Content": np.linspace(4.5, 6, 41),
               "Quality_Score": np.linspace(6, 10, 41),
               "Fermentation_Time": np.arange(9.5, 20.5, 1)}
    y_edges = np.linspace(1000, 20000, 41)
    cells = {c: np.zeros((len(x_edges[c]) - 1, 40)) for c in CONTINUOUS}
    bin_sums = {c: np.zeros(len(x_edges[c]) - 1) for c in CONTINUOUS}
    for chunk in pd.read_csv(SOURCE, usecols=["Batch_ID", *NUMERIC, *CATEGORIES], chunksize=250_000):
        if chunk.isna().any().any():
            raise ValueError("Unexpected missing values; review before proceeding")
        ids = chunk["Batch_ID"]
        if not pd.api.types.is_integer_dtype(ids.dtype) or ids.min() < id_min or ids.max() > id_max:
            raise ValueError("Batch_ID type/range changed; revise the presence-array bounds")
        observed_min = min(observed_min, int(ids.min()))
        observed_max = max(observed_max, int(ids.max()))
        unique_ids = np.unique(ids.to_numpy()) - id_min
        duplicates += len(ids) - len(unique_ids) + int(seen[unique_ids].sum())
        seen[unique_ids] = True

        # Merge centered cross-products for stable full-data Pearson correlations.
        values = chunk[NUMERIC].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError("Nonfinite numeric observations require review")
        chunk_n = len(values)
        chunk_mean = values.mean(axis=0)
        centered = values - chunk_mean
        delta = chunk_mean - mean
        cross += centered.T @ centered + np.outer(delta, delta) * (n * chunk_n / (n + chunk_n))
        mean += delta * (chunk_n / (n + chunk_n))
        n += chunk_n
        sales = chunk["Total_Sales"]
        for column in CATEGORIES:
            grouped = sales.groupby(chunk[column], observed=True)
            summary = grouped.agg(["count", "sum"])
            summary["sumsq"] = sales.pow(2).groupby(chunk[column], observed=True).sum()
            groups[column] = groups[column].add(summary, fill_value=0)
        for column in CONTINUOUS:
            cells[column] += np.histogram2d(chunk[column], sales, bins=[x_edges[column], y_edges])[0]
            bin_sums[column] += np.histogram(chunk[column], bins=x_edges[column], weights=sales)[0]
        if n % 1_000_000 == 0:
            print(f"Relationship scan: {n:,} rows", flush=True)

    sd = float(np.sqrt(cross[0, 0] / (n - 1)))
    correlations = pd.DataFrame({"Pearson_r": cross[0, 1:] / np.sqrt(cross[0, 0] * np.diag(cross)[1:]),
                                 "count": n}, index=CONTINUOUS)
    correlations.index.name = "predictor"
    comparisons = []
    plot_groups = []
    for column, summary in groups.items():
        summary["mean"] = summary["sum"] / summary["count"]
        summary["std"] = np.sqrt((summary["sumsq"] - summary["sum"] ** 2 / summary["count"]) / (summary["count"] - 1))
        summary["count"] = summary["count"].astype("int64")
        summary["difference_from_overall"] = summary["mean"] - mean[0]
        summary["difference_in_overall_SD"] = summary["difference_from_overall"] / sd
        groups[column] = summary[["count", "mean", "std", "difference_from_overall", "difference_in_overall_SD"]]
        groups[column].index.name = "category"
        spread = float(summary["mean"].max() - summary["mean"].min())
        comparisons.append({"predictor": column, "lowest_mean_category": summary["mean"].idxmin(),
                            "highest_mean_category": summary["mean"].idxmax(), "max_mean_gap_USD": spread,
                            "gap_divided_by_overall_SD": spread / sd,
                            "gap_as_percent_of_overall_SD": 100 * spread / sd})
        plot_groups.append(groups[column].reset_index().assign(predictor=column))
        assert summary["count"].sum() == n
        assert np.isclose(summary["sum"].sum() / n, mean[0], rtol=1e-12)
    comparisons = pd.DataFrame(comparisons).set_index("predictor")
    category_plot = pd.concat(plot_groups, ignore_index=True)
    category_plot["low"] = category_plot["mean"] - category_plot["std"]
    category_plot["high"] = category_plot["mean"] + category_plot["std"]
    p = (ggplot(category_plot, aes(y="category", x="mean"))
         + geom_segment(aes(x="low", xend="high", yend="category"), color="#8AB7C7", size=1.2)
         + geom_point(color="#174D65", size=2)
         + geom_vline(xintercept=float(mean[0]), linetype="dashed", color="#B84A35")
         + facet_wrap("~predictor", scales="free_y", ncol=1)
         + scale_x_continuous(limits=(0, 20000), labels=lambda v: [f"{x:,.0f}" for x in v])
         + theme_minimal()
         + labs(title="Category means compared with sales variability",
                subtitle="All rows; bars show mean ± 1 SD, not confidence intervals; dashed line = overall mean",
                x="Total Sales (USD)", y=""))
    p.save(ROOT / "brewery_sales_category_spread.png", width=10, height=10, dpi=150, verbose=False)
    p = (ggplot(category_plot, aes(y="category", x="difference_in_overall_SD"))
         + geom_vline(xintercept=0, linetype="dashed", color="#B84A35")
         + geom_point(color="#174D65", size=2.5)
         + facet_wrap("~predictor", scales="free_y", ncol=1)
         + scale_x_continuous(limits=(-.01, .01)) + theme_minimal()
         + labs(title="Category mean deviations on a magnified scale",
                subtitle="All rows; full horizontal scale spans only ±0.01 overall SD",
                x="(Category mean − overall mean) / overall sales SD", y=""))
    p.save(ROOT / "brewery_sales_category_differences.png", width=10, height=10, dpi=150, verbose=False)

    heatmap, trends = [], []
    for column in CONTINUOUS:
        assert int(cells[column].sum()) == n, "Plot ranges must include every observation"
        mids = (x_edges[column][:-1] + x_edges[column][1:]) / 2
        ymids = (y_edges[:-1] + y_edges[1:]) / 2
        # Within each x-bin, express sales-bin counts as percentages for comparable panels.
        percentages = cells[column] / cells[column].sum(axis=1)[:, None] * 100
        xx, yy = np.meshgrid(mids, ymids, indexing="ij")
        heatmap.append(pd.DataFrame({"predictor": column, "x": xx.ravel(), "sales": yy.ravel(),
                                     "percent": percentages.ravel(),
                                     "bin_width": float(x_edges[column][1] - x_edges[column][0]),
                                     "bin_height": float(y_edges[1] - y_edges[0])}))
        trends.append(pd.DataFrame({"predictor": column, "x": mids,
                                    "mean_sales": bin_sums[column] / cells[column].sum(axis=1)}))
    heatmap, trends = pd.concat(heatmap), pd.concat(trends)
    p = (ggplot(heatmap, aes("x", "sales", fill="percent"))
         + geom_tile(aes(width="bin_width", height="bin_height"))
         + geom_line(aes(x="x", y="mean_sales"), data=trends, inherit_aes=False, color="#D44427", size=.8)
         + facet_wrap("~predictor", scales="free_x", nrow=1)
         + scale_fill_gradient(low="#FFFFFF", high="#155F79", limits=(0, float(heatmap["percent"].max())))
         + scale_y_continuous(labels=lambda v: [f"{x:,.0f}" for x in v]) + theme_minimal()
         + labs(title="Total Sales versus numeric predictors",
                subtitle="All rows; 40 sales bins; red lines show observed means within predictor bins",
                x="Predictor value (40 bins for alcohol/quality; each integer for fermentation)",
                y="Total Sales (USD)", fill="% within\npredictor bin"))
    p.save(ROOT / "brewery_sales_numeric_relationships.png", width=13, height=5, dpi=150, verbose=False)

    unique_count = int(seen.sum())
    assert n - unique_count == duplicates
    assert n == previous["rows"]
    assert np.isclose(sd, previous["descriptive_statistics"]["Total_Sales"]["std"], rtol=1e-10)
    after = SOURCE.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    id_check = {"rows": n, "distinct_Batch_ID": unique_count, "duplicate_occurrences_after_first": duplicates,
                "missing_Batch_ID": 0, "minimum": observed_min, "maximum": observed_max,
                "unique": duplicates == 0,
                "method": "Exact presence array; within-chunk unique IDs and cross-chunk repeats counted; every ID type/range checked."}
    results = {"rows": n, "overall_sales_mean": float(mean[0]), "overall_sales_SD": sd,
               "sales_correlations": correlations.to_dict(orient="index"),
               "category_sales": {c: f.to_dict(orient="index") for c, f in groups.items()},
               "category_comparisons": comparisons.to_dict(orient="index"), "Batch_ID_check": id_check,
               "verification": {"group_counts_and_means_reconcile": True, "all_rows_in_each_plot": True,
                                "overall_SD_matches_previous_exploration": True, "source_size_and_mtime_unchanged": True}}
    (ROOT / "brewery_sales_relationships.json").write_text(json.dumps(results, indent=2))
    sections = ["# Sales relationships: full-data exploration",
                f"All {n:,} rows analyzed. Overall mean sales: ${mean[0]:,.6f}; sample SD: ${sd:,.6f}.",
                "## Method\n\nA single CSV pass reads eight columns in chunks of 250,000 rows. Centered cross-products provide Pearson correlations; grouped counts, sums, and squared sums provide means and sample SDs (ddof=1). Only the current chunk, small summary tables/histograms, and a roughly 10 MB exact ID presence array are retained. No samples, regression models, preprocessing, or row-level features are used. Plot bins and standardized group differences are descriptive summaries, not model features.",
                "## Correlations with Total Sales\n\n" + markdown_table(correlations.round(9)),
                "## Category gaps relative to overall sales variation\n\n" + markdown_table(comparisons.round(6))]
    for column, summary in groups.items():
        sections.append(f"## Sales by {column}\n\n" + markdown_table(summary.round(6)))
    sections += ["## Batch_ID uniqueness\n\n" + markdown_table(pd.DataFrame.from_dict(id_check, orient="index", columns=["value"])),
                 "This independently resolves the earlier unverified uniqueness claim; it does not by itself establish statistical independence of rows.",
                 "## Plots\n\n![Numeric relationships](brewery_sales_numeric_relationships.png)\n\n![Category spread](brewery_sales_category_spread.png)\n\n![Magnified category differences](brewery_sales_category_differences.png)",
                 "The numeric plots aggregate every observation into two-dimensional bins. Color represents the conditional sales distribution within each predictor bin. The line is an observed bin mean, not a fitted regression. SD bars show individual sales variation, not uncertainty around category means. The separate magnified plot makes tiny differences visible without presenting them as large effects.",
                 "## Documentation review\n\n" + (ROOT / "brewery_sales_documentation_review.md").read_text(),
                 "## Interpretation and research decisions\n\nThe marginal correlations and group mean differences are negligible relative to individual sales variability. These checks provide no evidence of practically substantial marginal associations for the six proposed predictors. A business threshold has not been specified, and marginal checks do not rule out conditional, nonlinear, or interaction effects. No such models have been fitted.\n\nSynthetic provenance is confirmed, but an independent uniform sales generator is an inference consistent with the results, not a documented algorithm. Do not claim that any beer style, packaging format, location, or production trait drives real brewery sales from these data. Agree with the group/instructor whether to use this dataset as a methods exercise demonstrating weak signal, or select observed data suitable for the original substantive question. If retaining it, state its synthetic nature, prespecify effects and interactions, and prioritize effect magnitude over significance with 10 million rows. Do not search many interactions simply to obtain significant results.",
                 "## Verification\n\nAll category counts and weighted means reconcile to the full-data totals. Every numeric plot includes all rows. Overall SD agrees with the initial exploration. Exact duplicate occurrences reconcile with rows minus distinct IDs. Input file size and modification time are unchanged. Existing instructions, cleaned data, and preprocessing reports were not overwritten."]
    (ROOT / "brewery_sales_relationships.md").write_text("\n\n".join(sections) + "\n")
    print(json.dumps({k: results[k] for k in ["rows", "sales_correlations", "category_comparisons", "Batch_ID_check", "verification"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--relationships-only", action="store_true", help="Run the bounded-memory sales relationship extension only")
    args = parser.parse_args()
    if not args.relationships_only:
        main()
    relationships()
