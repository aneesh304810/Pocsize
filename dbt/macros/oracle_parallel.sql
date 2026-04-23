{# ==========================================================================
   Oracle parallel DML macros
   ==========================================================================
   Used as pre/post hooks on gold-layer table models. Enabling parallel DML
   is session-level and must be toggled explicitly; dbt-oracle does not do
   this for us.

   Sizing observation: at 2M rows with degree=4 on the POC Exadata slice,
   aggregation CPU drops ~2.8x vs. serial (not 4x — startup overhead +
   Amdahl).  At degree=8 the improvement flattens (~3.2x) because the
   workload is I/O-bound by then.
========================================================================= #}

{% macro oracle_parallel_dml_on(degree=4) %}
    alter session enable parallel dml
{% endmacro %}

{% macro oracle_parallel_dml_off() %}
    alter session disable parallel dml
{% endmacro %}

{% macro parallel_hint(degree=4) %}
    /*+ parallel({{ degree }}) */
{% endmacro %}
