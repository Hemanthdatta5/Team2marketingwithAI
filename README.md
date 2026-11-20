# Team2marketingwithAI

Python workspace for the brewery sales marketing analytics project.

## Setup

From the project directory, run:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export MPLBACKEND=Agg
```

In VS Code, install the Microsoft Python extension if needed. Use **Python: Select
Interpreter** to select `.venv/bin/python`. The workspace settings provide this
default and set the plotting backend to `Agg` in new integrated terminals.

## Project layout

- `explore/explore.py`: exploration
- `preprocess/preprocess.py`: preprocessing
- `features/features.py`: feature engineering
- `model/model.py`: modeling
- `run_clean_and_validate.py`: existing data cleaning pipeline
- `validate_brewery_sales.py`: existing cleaned-data validation

The four stage scripts start empty. Use pandas for preprocessing and plotnine for
visualizations. Before importing plotting libraries in scripts, set the backend:

```python
import matplotlib
matplotlib.use("Agg")
```

Save plots as image files. Keep raw data unchanged and place new data files in the
project root.

## Run the existing pipeline

Data processing is optional and is not part of environment setup. The default
source is a large remote dataset and requires internet access:

```sh
python run_clean_and_validate.py
python validate_brewery_sales.py
```

To clean a local source instead:

```sh
python run_clean_and_validate.py --source brewery_sales_raw.csv
```

The cleaning script creates `brewery_sales_cleaned.csv` and refuses to overwrite
an existing output CSV. Both scripts write their corresponding JSON reports in
the current directory; running them replaces the existing reports. The standalone
validation script also accesses the remote source for a five-row comparison.
