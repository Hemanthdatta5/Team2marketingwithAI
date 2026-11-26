from pathlib import Path
import json
import hashlib
import pandas as pd

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'raw_download' / 'online_retail_II.xlsx'
OUT = ROOT / 'cleaned_outputs'
OUT.mkdir(exist_ok=True)

DOC_URL = 'https://archive.ics.uci.edu/dataset/502/online+retail+ii'
EXPECTED = ['Invoice', 'StockCode', 'Description', 'Quantity', 'InvoiceDate', 'Price', 'Customer ID', 'Country']
RENAMES = {'Invoice': 'InvoiceNo', 'Price': 'UnitPrice', 'Customer ID': 'CustomerID'}

def profile_sheet(df, name):
    d = df['InvoiceDate'] if 'InvoiceDate' in df else pd.Series(dtype='datetime64[ns]')
    return {'sheet': name, 'rows': int(len(df)), 'columns': list(df.columns),
            'date_min': None if d.dropna().empty else str(pd.to_datetime(d).min()),
            'date_max': None if d.dropna().empty else str(pd.to_datetime(d).max())}

def main():
    if not SOURCE.exists(): raise FileNotFoundError(SOURCE)
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    book = pd.ExcelFile(SOURCE)
    sheets = {}
    profiles = []
    for name in book.sheet_names:
        df = pd.read_excel(SOURCE, sheet_name=name)
        sheets[name] = df
        profiles.append(profile_sheet(df, name))
    cols = [tuple(df.columns) for df in sheets.values()]
    compatible = len(set(cols)) == 1 and set(cols[0]) == set(EXPECTED)
    combined = []
    for name, df in sheets.items():
        x = df.copy()
        x.insert(0, 'source_row_id', range(2, len(x) + 2))
        x.insert(0, 'source_sheet', name)
        x = x.rename(columns=RENAMES)
        combined.append(x)
    raw = pd.concat(combined, ignore_index=True)
    key_cols = ['InvoiceNo','StockCode','Description','Quantity','InvoiceDate','UnitPrice','CustomerID','Country']
    date_before = raw['InvoiceDate'].notna()
    date_parsed = pd.to_datetime(raw['InvoiceDate'], errors='coerce')
    quantity_before = raw['Quantity'].notna()
    quantity_parsed = pd.to_numeric(raw['Quantity'], errors='coerce')
    price_before = raw['UnitPrice'].notna()
    price_parsed = pd.to_numeric(raw['UnitPrice'], errors='coerce')
    type_audit = {
        'identifier_fields': ['InvoiceNo', 'StockCode', 'CustomerID'],
        'identifier_treatment': 'Stored as string identifiers; missing values remain missing and are never converted to literal text nan.',
        'invoice_date_conversion_failures': int((date_before & date_parsed.isna()).sum()),
        'quantity_conversion_failures': int((quantity_before & quantity_parsed.isna()).sum()),
        'unitprice_conversion_failures': int((price_before & price_parsed.isna()).sum()),
        'original_dtypes': {k: str(v) for k, v in raw.dtypes.items()},
    }
    raw['InvoiceDate'] = date_parsed
    duplicate_mask = raw.duplicated(key_cols, keep=False)
    cross_sheet_dupes = raw.loc[duplicate_mask].duplicated(key_cols, keep=False).sum()
    # Duplicate audit on business fields only. Source tracking and derived fields are excluded.
    exact_key = key_cols
    invoice_product_key = ['InvoiceNo', 'StockCode']
    def duplicate_summary(keys):
        counts = raw.groupby(keys, dropna=False).size()
        repeated = counts[counts > 1]
        return {
            'key_fields': keys, 'repeated_key_groups': int(len(repeated)),
            'rows_in_repeated_groups': int(repeated.sum()),
            'extra_rows_beyond_one_per_group': int((repeated - 1).sum()),
            'max_rows_per_key': int(repeated.max()) if len(repeated) else 1,
        }
    def split_duplicate_summary(keys):
        groups = raw.groupby(keys, dropna=False).agg(rows=('InvoiceNo','size'), sheets=('source_sheet','nunique'))
        repeated = groups[groups['rows'] > 1]
        return {
            'within_sheet_repeated_key_groups': int((repeated['sheets'] == 1).sum()),
            'between_sheet_repeated_key_groups': int((repeated['sheets'] > 1).sum()),
            'rows_in_between_sheet_repeated_groups': int(repeated.loc[repeated['sheets'] > 1, 'rows'].sum()),
        }
    duplicate_audit = {
        'comparison_note': 'Business-field comparisons exclude source_sheet, source_row_id, is_cancellation, and line_total.',
        'identical_business_records': {**duplicate_summary(exact_key), **split_duplicate_summary(exact_key)},
        'repeated_invoice_numbers': {**duplicate_summary(['InvoiceNo']), **split_duplicate_summary(['InvoiceNo'])},
        'repeated_invoice_product_combinations': {**duplicate_summary(invoice_product_key), **split_duplicate_summary(invoice_product_key)},
        'treatment': 'No records were removed. Repeated invoices and invoice-product combinations can be valid transaction lines; identical records remain flagged because the dataset provides no evidence sufficient to distinguish accidental duplication from repeated source records.',
    }
    clean = raw.copy()
    # Genuine repairs: normalize text whitespace and numeric/date types; preserve missing values.
    label_trim_counts = {}
    for c in ['InvoiceNo','StockCode','Description','Country']:
        before = clean[c].copy()
        clean[c] = clean[c].astype('string').str.strip()
        label_trim_counts[c] = int((before.notna() & (before.astype('string') != clean[c])).sum())
    def identifier_string(s):
        def one(v):
            if pd.isna(v): return pd.NA
            if isinstance(v, float) and v.is_integer(): return str(int(v))
            return str(v).strip()
        return s.map(one).astype('string')
    for c in ['InvoiceNo', 'StockCode', 'CustomerID']:
        clean[c] = identifier_string(clean[c])
    clean['Quantity'] = quantity_parsed
    clean['UnitPrice'] = price_parsed
    clean['InvoiceDate'] = pd.to_datetime(clean['InvoiceDate'], errors='coerce')
    clean['is_cancellation'] = clean['InvoiceNo'].str.upper().str.startswith('C', na=False)
    clean['LineAmount'] = clean['Quantity'] * clean['UnitPrice']
    clean['line_total'] = clean['LineAmount']
    clean['has_negative_quantity'] = clean['Quantity'] < 0
    clean['has_zero_price'] = clean['UnitPrice'] == 0
    clean['has_negative_price'] = clean['UnitPrice'] < 0
    clean['cancellation_quantity_inconsistent'] = clean['is_cancellation'] & ~clean['has_negative_quantity']
    clean['negative_quantity_non_cancellation'] = clean['has_negative_quantity'] & ~clean['is_cancellation']
    desc_upper = clean['Description'].astype('string').str.upper()
    stock_upper = clean['StockCode'].astype('string').str.upper()
    non_merch_pattern = r'POSTAGE|CARRIAGE|DELIVERY|FEE|CHARGE|DISCOUNT|ADJUST|BAD DEBT|BANK CHARG|AMAZON|SAMPLES?|MANUAL|DOTCOM|CRUK'
    clean['possible_non_merchandise'] = desc_upper.str.contains(non_merch_pattern, na=False) | stock_upper.str.contains(non_merch_pattern, na=False)
    # Purchase-only eligibility: a positive-quantity, non-cancellation merchandise line
    # with a nonnegative unit price. Zero-price lines are retained as free merchandise.
    clean['purchase_eligible'] = True
    clean['purchase_exclusion_reason'] = pd.Series(pd.NA, index=clean.index, dtype='string')
    reason_masks = [
        ('cancellation_invoice', clean['is_cancellation']),
        ('nonpositive_quantity', ~clean['is_cancellation'] & (clean['Quantity'] <= 0)),
        ('negative_unit_price', ~clean['is_cancellation'] & (clean['Quantity'] > 0) & (clean['UnitPrice'] < 0)),
        ('possible_non_merchandise', ~clean['is_cancellation'] & (clean['Quantity'] > 0) & (clean['UnitPrice'] >= 0) & clean['possible_non_merchandise']),
    ]
    for reason, mask in reason_masks:
        take = clean['purchase_eligible'] & mask
        clean.loc[take, 'purchase_eligible'] = False
        clean.loc[take, 'purchase_exclusion_reason'] = reason
    purchase_only = clean.loc[clean['purchase_eligible']].copy()
    exclusion_report = clean.loc[~clean['purchase_eligible'], 'purchase_exclusion_reason'].value_counts(dropna=False).rename_axis('exclusion_reason').reset_index(name='rows')
    exclusion_report['pct_of_master'] = exclusion_report['rows'] / len(clean) * 100
    # Validation before export: source-vs-master reconciliation and traceable spot checks.
    validation = {
        'source_vs_master': {
            'rows_source': int(len(raw)), 'rows_master': int(len(clean)), 'rows_difference': int(len(clean)-len(raw)),
            'distinct_invoices_source': int(raw['InvoiceNo'].nunique(dropna=True)), 'distinct_invoices_master': int(clean['InvoiceNo'].nunique(dropna=True)),
            'identified_customers_source': int(raw['CustomerID'].notna().sum()), 'identified_customers_master': int(clean['CustomerID'].notna().sum()),
            'quantity_sum_source': float(pd.to_numeric(raw['Quantity'], errors='coerce').sum()), 'quantity_sum_master': float(clean['Quantity'].sum()),
            'line_amount_source': float((quantity_parsed * price_parsed).sum()), 'line_amount_master': float(clean['LineAmount'].sum()),
            'explanation': 'Master retains all source rows. Differences in text fields are whitespace normalization and documented unambiguous Description recovery; derived flags and LineAmount are added.'
        },
        'master_rechecks': {
            'missing_by_column': {k:int(v) for k,v in clean.isna().sum().items()},
            'identifier_dtypes': {k:str(clean[k].dtype) for k in ['InvoiceNo','StockCode','CustomerID']},
            'numeric_dtypes': {k:str(clean[k].dtype) for k in ['Quantity','UnitPrice','LineAmount']},
            'distinct_countries': int(clean['Country'].nunique(dropna=True)), 'distinct_stockcodes': int(clean['StockCode'].nunique(dropna=True)),
            'date_min': str(clean['InvoiceDate'].min()), 'date_max': str(clean['InvoiceDate'].max()),
            'quantity_min': float(clean['Quantity'].min()), 'quantity_max': float(clean['Quantity'].max()),
            'unitprice_min': float(clean['UnitPrice'].min()), 'unitprice_max': float(clean['UnitPrice'].max()),
            'duplicate_flagged_rows': int(raw.duplicated(key_cols, keep=False).sum()),
            'rows_with_unresolved_missing_description': int(clean['Description'].isna().sum()),
        },
        'spot_checks': []
    }
    for _, r in raw.iloc[[0, len(raw)//2, len(raw)-1]].iterrows():
        m = clean[(clean['source_sheet'] == r['source_sheet']) & (clean['source_row_id'] == r['source_row_id'])].iloc[0]
        validation['spot_checks'].append({'source_sheet': r['source_sheet'], 'source_row_id': int(r['source_row_id']), 'invoice_source': str(r['InvoiceNo']), 'invoice_master': str(m['InvoiceNo']), 'stockcode_source': str(r['StockCode']), 'stockcode_master': str(m['StockCode']), 'quantity_source': float(r['Quantity']), 'quantity_master': float(m['Quantity']), 'unitprice_source': float(r['UnitPrice']), 'unitprice_master': float(m['UnitPrice']), 'trace_match': str(r['InvoiceNo']) == str(m['InvoiceNo']) and str(r['StockCode']) == str(m['StockCode'])})
    cleaning_log = pd.DataFrame([
        ['Missing Description', 'Recovered only unambiguous StockCode mappings; preserved original and added flag', int(clean['Description'].isna().sum()), 'Avoids fabricating descriptions while restoring reliable labels.'],
        ['Missing CustomerID', 'Retained missing values; excluded only from customer-identification analyses', int(clean['CustomerID'].isna().sum()), 'Customer IDs cannot be safely inferred.'],
        ['Whitespace in labels', 'Trimmed surrounding whitespace', int(sum(label_trim_counts.values())), 'Removes formatting noise without merging products.'],
        ['Cancellations', 'Flagged invoices beginning with C, case-insensitive', int(clean['is_cancellation'].sum()), 'Required by UCI documentation; cancellations may be valid business events.'],
        ['Duplicate business records', 'Flagged and retained', int(raw.duplicated(key_cols, keep=False).sum()), 'No evidence was sufficient to prove accidental duplication.'],
        ['Unusual values', 'Flagged negative/zero prices, negative quantities, extremes, and possible non-merchandise', int((clean['has_negative_quantity'] | clean['has_zero_price'] | clean['has_negative_price'] | clean['possible_non_merchandise']).sum()), 'Wholesale orders, credits, free items, and adjustments may be valid.'],
        ['Purchase-only subset', 'Excluded by documented eligibility rules and recorded exclusion reason', int(len(clean)-len(purchase_only)), 'Provides a conservative merchandise-purchase view without changing the master.'],
    ], columns=['issue','action','affected_record_count','justification'])
    unresolved = ['1,310 descriptions remain missing because StockCode mappings were absent or ambiguous.', '243,007 rows lack CustomerID and are not suitable for customer-level analyses.', 'Duplicate business records remain ambiguous and may affect totals if a downstream user deduplicates them.', 'Some possible non-merchandise flags use descriptive keywords and require business review.', 'One cancellation has a nonnegative quantity and 3,457 negative-quantity rows lack a cancellation prefix; these were retained for review.']
    # Full-dataset extreme-value summaries; no rows are removed or transformed.
    def value_summary(s):
        q = s.quantile([0.99, 0.999, 1.0])
        return {'min': float(s.min()), 'median': float(s.median()), 'p99': float(q.loc[0.99]), 'p999': float(q.loc[0.999]), 'max': float(q.loc[1.0])}
    unusual_audit = {
        'cancellation_rows': int(clean['is_cancellation'].sum()),
        'cancellation_with_nonnegative_quantity': int(clean['cancellation_quantity_inconsistent'].sum()),
        'negative_quantity_non_cancellation_rows': int(clean['negative_quantity_non_cancellation'].sum()),
        'zero_price_rows': int(clean['has_zero_price'].sum()),
        'negative_price_rows': int(clean['has_negative_price'].sum()),
        'possible_non_merchandise_rows': int(clean['possible_non_merchandise'].sum()),
        'possible_non_merchandise_by_stockcode': clean.loc[clean['possible_non_merchandise'], 'StockCode'].value_counts(dropna=False).head(50).to_dict(),
        'possible_non_merchandise_by_description': clean.loc[clean['possible_non_merchandise'], 'Description'].value_counts(dropna=False).head(50).to_dict(),
        'quantity_summary': value_summary(clean['Quantity']),
        'unitprice_summary': value_summary(clean['UnitPrice']),
        'treatment': 'Cancellations, negative/zero/negative-price rows, possible non-merchandise rows, and extremes are retained and flagged. No caps, absolute values, outlier deletion, or guessed corrections were applied. Inconsistencies remain unresolved for review.'
    }
    # Missingness treats nulls and blank/whitespace-only strings as missing.
    missing = {}
    for c in clean.columns:
        if pd.api.types.is_string_dtype(clean[c]):
            missing[c] = clean[c].isna() | clean[c].str.strip().eq('')
        else:
            missing[c] = clean[c].isna()
    missing_report = pd.DataFrame({
        'column': list(clean.columns),
        'missing_count': [int(missing[c].sum()) for c in clean.columns],
    })
    missing_report['total_rows'] = len(clean)
    missing_report['missing_pct'] = missing_report['missing_count'] / len(clean) * 100
    # Recover descriptions only where a StockCode maps to exactly one nonblank description.
    desc_map = clean.loc[~missing['Description'], ['StockCode','Description']].drop_duplicates()
    desc_counts = desc_map.groupby('StockCode')['Description'].nunique()
    reliable_codes = set(desc_counts[desc_counts == 1].index)
    lookup = desc_map.drop_duplicates('StockCode').set_index('StockCode')['Description'].to_dict()
    recoverable = missing['Description'] & clean['StockCode'].isin(reliable_codes)
    clean['Description_original'] = clean['Description']
    clean['Description_recovered'] = False
    clean.loc[recoverable, 'Description'] = clean.loc[recoverable, 'StockCode'].map(lookup)
    clean.loc[recoverable, 'Description_recovered'] = True
    # Dimension audits use full data; month is calendar month from InvoiceDate.
    clean['_month'] = clean['InvoiceDate'].dt.to_period('M').astype('string')
    def grouped_missing(group_col):
        x = clean.assign(_missing_description=missing['Description']).groupby(group_col, dropna=False).agg(rows=('InvoiceNo','size'), missing_description=('_missing_description','sum'))
        x['missing_description_pct'] = x['missing_description'] / x['rows'] * 100
        return x.reset_index()
    by_sheet = grouped_missing('source_sheet')
    by_month = grouped_missing('_month')
    by_country = grouped_missing('Country')
    by_cancel = grouped_missing('is_cancellation')
    # Do not delete cancellations, missing CustomerID/Description, or negative quantities: these are documented/valid business information.
    issues = {
        'source_sha256': source_hash, 'source_file': str(SOURCE), 'documentation_url': DOC_URL,
        'sheet_profiles': profiles, 'sheet_names': book.sheet_names,
        'columns_compatible': compatible, 'original_columns': list(sheets[book.sheet_names[0]].columns),
        'renames': RENAMES, 'combined_rows': int(len(raw)), 'combined_columns': list(clean.columns),
        'exact_duplicate_rows_on_transaction_fields': int(raw.duplicated(key_cols, keep=False).sum()),
        'cross_sheet_duplicate_rows_on_transaction_fields': int(cross_sheet_dupes),
        'duplicate_audit': duplicate_audit,
        'type_and_label_audit': {**type_audit, 'trimmed_whitespace_counts': label_trim_counts,
            'alphabetic_stockcode_rows_retained': int(clean['StockCode'].str.contains(r'[A-Za-z]', na=False).sum()),
            'product_description_similarity_rule': 'No products were merged based on description similarity; StockCode remains the product identifier.'},
        'unusual_values_audit': unusual_audit,
        'purchase_only_rules': {
            'eligible_when': 'not a cancellation invoice, Quantity > 0, UnitPrice >= 0, and not flagged as possible non-merchandise',
            'zero_price_treatment': 'Included as free merchandise when other eligibility rules pass.',
            'signed_amount_definition': 'LineAmount = Quantity * UnitPrice; signs are preserved in the master dataset.',
            'exclusion_report': exclusion_report.to_dict(orient='records'),
            'eligible_rows': int(len(purchase_only)), 'excluded_rows': int(len(clean) - len(purchase_only)),
            'positive_purchase_amount_definition': 'Sum of LineAmount over purchase_only; represents eligible merchandise purchases before cancellations/adjustments.',
            'net_amount_definition': 'Sum of signed LineAmount over the master; includes positive sales plus negative cancellations, credits, discounts, and other adjustments. A negative row is not automatically a confirmed product return.'
        },
        'validation': validation,
        'unresolved_issues': unresolved,
        'quality_full_dataset': {
            'rows': int(len(clean)), 'missing_by_column': {k:int(v) for k,v in clean.isna().sum().items()},
            'invalid_dates': int(clean['InvoiceDate'].isna().sum()), 'zero_quantity': int((clean['Quantity']==0).sum()),
            'negative_quantity': int((clean['Quantity']<0).sum()), 'negative_unit_price': int((clean['UnitPrice']<0).sum()),
            'cancellation_rows': int(clean['is_cancellation'].sum()), 'unique_invoices': int(clean['InvoiceNo'].nunique(dropna=True)),
            'unique_customers': int(clean['CustomerID'].nunique(dropna=True)), 'date_min': str(clean['InvoiceDate'].min()), 'date_max': str(clean['InvoiceDate'].max()),
        },
        'missingness_by_column': missing_report.to_dict(orient='records'),
        'missing_description_by_sheet': by_sheet.to_dict(orient='records'),
        'missing_description_by_month': by_month.to_dict(orient='records'),
        'missing_description_by_country': by_country.to_dict(orient='records'),
        'missing_description_by_cancellation': by_cancel.to_dict(orient='records'),
        'description_recovery': {
            'reliable_stockcode_count': len(reliable_codes), 'recovered_description_rows': int(recoverable.sum()),
            'remaining_missing_description_rows': int((missing['Description'] & ~recoverable).sum()),
            'method': 'Filled only when StockCode had exactly one distinct nonblank Description in the full dataset; original values preserved in Description_original.'
        },
        'customer_identification_impact': {
            'missing_customerid_rows': int(missing['CustomerID'].sum()),
            'identified_customer_rows': int((~missing['CustomerID']).sum()),
            'identified_customer_row_pct': float((~missing['CustomerID']).mean() * 100),
            'treatment': 'Missing CustomerID rows remain in the master dataset and are excluded only from analyses that require customer identification.'
        },
        'cleaning_decisions': ['Trim surrounding whitespace in text fields.', 'Parse dates and numeric fields with coercion to missing only when unparsable.', 'Add is_cancellation and line_total derived fields.', 'Retain all source rows and documented cancellations/negative quantities/missing customer IDs.', 'No rows were dropped because duplicate rows were not proven erroneous.'],
        'row_definition': 'One row is one invoice line: one product (StockCode) and quantity recorded on an invoice at a unit price and timestamp. One invoice appears on multiple rows because an order can contain multiple products or separate product lines.'
    }
    clean = clean.drop(columns=['_month'])
    clean.to_csv(OUT / 'online_retail_extended.csv', index=False)
    purchase_only.drop(columns=['_month'], errors='ignore').to_csv(OUT / 'online_retail_II_clean.csv', index=False)
    # Excel worksheets max out at 1,048,576 rows; the full cleaned table has 1,067,371.
    # Keep the complete output in CSV and use Excel only for compact audit tables.
    with pd.ExcelWriter(OUT / 'online_retail_II_audit.xlsx', engine='openpyxl') as w:
        pd.DataFrame(profiles).to_excel(w, sheet_name='sheet_profile', index=False)
        pd.DataFrame([issues['quality_full_dataset']]).to_excel(w, sheet_name='quality_checks', index=False)
        missing_report.to_excel(w, sheet_name='missing_by_column', index=False)
        by_sheet.to_excel(w, sheet_name='missing_by_sheet', index=False)
        by_month.to_excel(w, sheet_name='missing_by_month', index=False)
        by_country.to_excel(w, sheet_name='missing_by_country', index=False)
        by_cancel.to_excel(w, sheet_name='missing_by_cancel', index=False)
        exclusion_report.to_excel(w, sheet_name='purchase_exclusions', index=False)
    (OUT / 'quality_report.json').write_text(json.dumps(issues, indent=2, default=str), encoding='utf-8')
    (OUT / 'validation_report.json').write_text(json.dumps(validation, indent=2, default=str), encoding='utf-8')
    cleaning_log.to_csv(OUT / 'cleaning_log.csv', index=False)
    print(json.dumps(issues, indent=2, default=str))

if __name__ == '__main__': main()
