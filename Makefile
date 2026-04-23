# Convenience targets for local dev + CI.
#
# The Jenkins shared-pod pipeline calls `make ci` — keep that green.

PYTHON      ?= python
DBT_PROJECT ?= dbt
DATA_DIR    ?= /mnt/user-data/outputs/sample_data

.PHONY: help sample-data small medium large sweep dbt-run dbt-test \
        load-oracle report clean ci

help:
	@echo "SWP Sizing POC — make targets"
	@echo "  sample-data   generate 2M-row sample dataset"
	@echo "  load-oracle   load CSV.gz into Oracle (requires ORACLE_* env vars)"
	@echo "  dbt-run       run dbt bronze -> silver -> gold"
	@echo "  dbt-test      run dbt tests"
	@echo "  report        rebuild sizing_matrix.xlsx from collected CSVs"
	@echo "  sweep         trigger the swp_sizing_sweep DAG in Airflow"
	@echo "  clean         remove target/, __pycache__, generated data"

sample-data:
	$(PYTHON) scripts/generate_sample_data.py \
	    --positions 2000000 --securities 50000 --accounts 10000 \
	    --out $(DATA_DIR)

load-oracle:
	$(PYTHON) scripts/load_to_oracle.py

dbt-run:
	cd $(DBT_PROJECT) && dbt deps && dbt build --profiles-dir .

dbt-test:
	cd $(DBT_PROJECT) && dbt test --profiles-dir .

report:
	$(PYTHON) scripts/build_sizing_report.py \
	    --reports-dir /mnt/sizing_reports \
	    --out /mnt/sizing_reports/sizing_matrix.xlsx

# Sweep is typically triggered from Airflow UI; this target is a CLI shortcut
sweep:
	airflow dags trigger swp_sizing_sweep

clean:
	rm -rf $(DBT_PROJECT)/target $(DBT_PROJECT)/dbt_packages
	find . -name __pycache__ -type d -exec rm -rf {} +
	rm -rf $(DATA_DIR)

ci:
	$(PYTHON) -m py_compile scripts/*.py dags/*.py
	cd $(DBT_PROJECT) && dbt parse --profiles-dir .
