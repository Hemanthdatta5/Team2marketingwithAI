# AGENTS.md

This project is used for applied data science work.

## General Working Rules

- When prompted to create script use simple, readable Python.
- Build on existing working code rather than replacing it unnecessarily.
- Avoid unnecessary functions, classes, abstractions, or production-style programming structures.
- Do not perform analyses, diagnostics, transformations, or checks that were not requested.

## Specific Instructions

- Analysis proceeds in stages - explore, preprocess, feature engineering and modeling

- Create folders representing the above stages if not present already. Add empty python script if not present. For example

- `explore/explore.py`
- `preprocess/preprocess.py`
- `features/features.py`
- `model/model.py`

- As work progresses add codes to the relevant script belonging to the relevant analytical stage.

- To handle any data preprocessing, use Pandas. Install it if not installed. Rely on methods chaining rather than creating too many intermediate datasets

- To handle data viz, use plotnine. Install it if not installed. Use a non-interactive Matplotlib backend (`Agg`) for all visualizations. When creating a visualization, save it as an image file.

## Data instructions
- Never modify the raw data.
- Place any new data in the project folder but not in any subfolders