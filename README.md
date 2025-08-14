## Medical Note Analysis — How to Run

This repo contains three main notebooks for data pipeline, note quality analysis, and system design. Follow the steps below to set up your environment and run the analysis end-to-end.

### 1) Prerequisites
- Python 3.10+
- An AWS account/credentials with access to DynamoDB (read) if you plan to pull from AWS
- Network access to your PostgreSQL database (RDS) if you plan to pull from Postgres

### 2) Install dependencies
Use a virtual environment and install requirements:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If you don’t use a venv:
```bash
pip install -r requirements.txt
```

### 3) Configure environment
Copy the example env file and fill in values:
```bash
cp env.example .env
```
Edit `.env`:
- `AWS_REGION`, `AWS_PROFILE` (or `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` if using static keys)
- `DYNAMO_TABLE_NAME` (e.g., clinical_notes)
- `POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- Optional: `OPENAI_API_KEY` if you use LLM analysis in notebooks

Load the env in your shell when running locally:
```bash
export $(grep -v '^#' .env | xargs)
```

Or add this to the first cell of notebooks if you prefer automatic loading:
```python
from dotenv import load_dotenv
load_dotenv()
```

### 4) Data sources
- DynamoDB: table name provided by `DYNAMO_TABLE_NAME`. You must have AWS credentials configured (via AWS CLI profile or env vars).
- PostgreSQL: connection values from `.env`. The Section 1 notebook currently shows connection examples; adjust to your host/DB/user/password as needed.

Optional DynamoDB bootstrap (creates table and optionally imports JSON):
```bash
python setup_dynamo.py --region $AWS_REGION --table $DYNAMO_TABLE_NAME --dry-run
# or import data
python setup_dynamo.py --region $AWS_REGION --table $DYNAMO_TABLE_NAME --import-file path/to/file.json
```

### 5) Run the notebooks
Start Jupyter and open the notebooks:
```bash
jupyter lab
# or
jupyter notebook
```

- `section1_pipeline_analysis.ipynb` (Pipeline & Analysis)
  - Reads from DynamoDB and Postgres
  - Normalizes and merges into `merged`, then sets `df = merged`
  - Includes utilities:
    - `process_time_columns` (and `TimeAnalyzer` class) to compute `processing_time_min` and analyze per-doctor time stats
    - ICD-10 code extraction that understands the AI JSON structure (prefers `selected_codes`, falls back to `default`, includes `historical`)
  - You can optionally write CSVs for later runs (commented examples in the notebook)

- `section2_note_quality.ipynb` (Note Quality)
  - Compares AI vs human-reviewed notes
  - Generates per-doctor reports and visualizations (section presence, similarity scores, worst section, etc.)
  - Use the provided plotting cells to switch between counts (missing/empty/extra) and scores

- `section3_system_design.ipynb` (System Design)
  - Conceptual/system design notes and diagrams

### 6) Common tasks
- Generate per-doctor quality report (in Section 2):
```python
# doctor_reports is a dict: {doctor_name: report_dict}
# Access quality score:
quality_scores = {doc: rep["metrics"]["quality_score"] for doc, rep in doctor_reports.items()}
```

- Update charts to use new analysis objects:
```python
# Example: plot empty sections
analysis = { ... }  # your computed dict
items = sorted(analysis["empty_sections"].items(), key=lambda x: x[1], reverse=True)
labels, vals = zip(*items)
sns.barplot(x=list(vals), y=list(labels))
```

### 7) Troubleshooting
- AWS credentials: ensure `aws sts get-caller-identity` works (correct profile/region).
- Postgres connection: verify host/port/security groups; ensure `psycopg2-binary` installed.
- No processing time data available: ensure both `job_start_time` and `job_end_time` columns exist and are parseable; re-run `TimeAnalyzer.process_time_columns()` so derived columns are created before computing stats.
- Missing libraries: install with `pip install -r requirements.txt` (includes `boto3`, `pandas`, `numpy`, `matplotlib`, `seaborn`, `psycopg2-binary`, `python-dotenv`, `jupyterlab`).

### 8) Project structure
- `section1_pipeline_analysis.ipynb`: data ingestion and analytics
- `section2_note_quality.ipynb`: note quality scoring and plots
- `section3_system_design.ipynb`: design notes
- `env.example`: template for environment variables

That’s it! Open the notebooks, step through the cells, and adapt connection settings to your environment. If you need a scripted entrypoint later, we can factor notebook logic into Python modules.
