"""Stream-clean, validate, and log Brewery_sales.

Raw input is never overwritten. Every input row is retained; only formatting
and parseable representations are corrected. Run from the project directory.
"""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path
import pandas as pd

DEFAULT_SOURCE = "https://huggingface.co/datasets/ankurnapa/Brewery_sales/resolve/main/brewery_data_complete_extended.csv"
OUTPUT = "brewery_sales_cleaned.csv"
NUMERIC = ["Batch_ID", "Fermentation_Time", "Temperature", "pH_Level", "Gravity", "Alcohol_Content", "Bitterness", "Color", "Volume_Produced", "Total_Sales", "Quality_Score", "Brewhouse_Efficiency", "Loss_During_Brewing", "Loss_During_Fermentation", "Loss_During_Bottling_Kegging"]
LABELS = ["Beer_Style", "SKU", "Location"]

def clean_chunk(df):
    actions = {"blank_to_missing": 0, "trimmed_text": 0, "date_converted": 0, "numeric_converted": 0}
    for c in df.columns:
        if df[c].dtype == "object":
            before = df[c].copy()
            stripped = before.str.strip()
            actions["trimmed_text"] += int((before != stripped).sum())
            df[c] = stripped.mask(stripped.eq(""))
            actions["blank_to_missing"] += int(stripped.eq("").sum())
    parsed = pd.to_datetime(df["Brew_Date"], errors="coerce")
    actions["date_converted"] = int(parsed.notna().sum())
    df["Brew_Date"] = parsed.dt.strftime("%Y-%m-%d %H:%M:%S")
    for c in NUMERIC:
        old = df[c]
        new = pd.to_numeric(old, errors="coerce")
        actions["numeric_converted"] += int((old.notna() & new.notna()).sum())
        df[c] = new
    return df, actions

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=DEFAULT_SOURCE)
    ap.add_argument("--output", default=OUTPUT)
    ap.add_argument("--chunksize", type=int, default=100_000)
    args = ap.parse_args()
    out = Path(args.output)
    if out.exists(): raise FileExistsError(f"Refusing to overwrite {out}; choose another output path")
    rows_in = rows_out = 0
    totals_in = totals_out = 0.0
    action_totals = {"blank_to_missing": 0, "trimmed_text": 0, "date_converted": 0, "numeric_converted": 0}
    first = True
    for chunk in pd.read_csv(args.source, chunksize=args.chunksize, dtype=str, keep_default_na=False):
        rows_in += len(chunk)
        totals_in += pd.to_numeric(chunk["Total_Sales"], errors="coerce").sum()
        cleaned, actions = clean_chunk(chunk)
        rows_out += len(cleaned)
        totals_out += cleaned["Total_Sales"].sum()
        for k, v in actions.items(): action_totals[k] += v
        cleaned.to_csv(out, mode="w" if first else "a", header=first, index=False, na_rep="")
        first = False
    # Validation pass over the saved result.
    missing, dup_rows, seen = {}, 0, set()
    types = None; label_values = {c: set() for c in LABELS}; invalid_dates = 0; numeric_failures = {c: 0 for c in NUMERIC}
    for chunk in pd.read_csv(out, chunksize=args.chunksize, keep_default_na=False):
        if types is None: types = {c: str(t) for c, t in chunk.dtypes.items()}
        for c in chunk.columns: missing[c] = missing.get(c, 0) + int(chunk[c].isna().sum() + chunk[c].eq("").sum())
        for c in LABELS: label_values[c].update(chunk[c].dropna().unique().tolist())
        invalid_dates += int(pd.to_datetime(chunk["Brew_Date"], errors="coerce").isna().sum())
        for c in NUMERIC: numeric_failures[c] += int(pd.to_numeric(chunk[c], errors="coerce").isna().sum() - chunk[c].isna().sum())
        hashes = pd.util.hash_pandas_object(chunk, index=False)
        for h in hashes:
            h = int(h); dup_rows += int(h in seen); seen.add(h)
    log = [
      {"issue_found":"Blank/whitespace-only cells", "action_taken":"Converted to missing values; rows retained", "affected_records":action_totals["blank_to_missing"], "justification":"Blank text carries no observed value; no row deletion."},
      {"issue_found":"Text formatting whitespace", "action_taken":"Trimmed leading/trailing whitespace", "affected_records":action_totals["trimmed_text"], "justification":"Formatting-only normalization; category meaning preserved."},
      {"issue_found":"Date and numeric representations", "action_taken":"Parsed Brew_Date and numeric measurements", "affected_records":action_totals["date_converted"] + action_totals["numeric_converted"], "justification":"Required types; unparseable values become missing and are not invented."},
      {"issue_found":"Potential sentinel codes and unusual values", "action_taken":"Not recoded or removed automatically", "affected_records":0, "justification":"Documentation does not establish that values such as 999, zero, or unusual measurements are missing/invalid."},
      {"issue_found":"Duplicates", "action_taken":"No rows removed", "affected_records":0, "justification":"Repeated Batch_IDs or identical rows require domain evidence before deletion; unit of analysis is preserved."},
      {"issue_found":"Missing Total_Sales", "action_taken":"Rows retained; exclude only from analyses requiring observed sales", "affected_records":missing.get("Total_Sales", 0), "justification":"Sales outcomes must not be imputed."},
    ]
    report = {"source":args.source, "output":str(out), "rows_before":rows_in, "rows_after":rows_out, "columns_after":len(types or {}), "total_sales_before":totals_in, "total_sales_after":totals_out, "total_sales_difference":totals_out-totals_in, "validation":{"missing_counts":missing,"dtypes":types,"duplicate_rows_after_first":dup_rows,"date_conversion_failures":invalid_dates,"numeric_conversion_failures":numeric_failures,"labels":{c:sorted(v) for c,v in label_values.items()}},"cleaning_log":log,"unresolved_issues":["Meaning of possible sentinel values is undocumented.","Repeated Batch_IDs were not established as accidental duplicates.","The dataset documentation does not fully define whether Total_Sales is realized sales or an estimate."],"unit_of_analysis":"Preserved one input row per output row; no records dropped."}
    Path("brewery_sales_cleaning_log.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
if __name__ == "__main__": main()
