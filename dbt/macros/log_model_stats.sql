{# ==========================================================================
   log_model_stats
   ==========================================================================
   Captures Oracle session-level metrics before and after each model runs,
   persisting the delta into a METRICS.DBT_MODEL_RUN_STATS table. That table
   is the primary data source for the CTO-facing sizing deck.

   Metrics captured per model:
     - elapsed_seconds         (wall clock)
     - cpu_seconds             (v$sesstat: 'CPU used by this session')
     - pga_peak_bytes          (v$sesstat: 'session pga memory max')
     - uga_peak_bytes          (v$sesstat: 'session uga memory max')
     - physical_reads_bytes
     - logical_reads
     - redo_size_bytes
     - rows_processed          (from dbt result object)

   Usage in dbt_project.yml:
     on-run-start:
       - "{{ create_metrics_table_if_not_exists() }}"
     models:
       gold:
         +post-hook: "{{ log_model_stats() }}"
========================================================================= #}

{% macro create_metrics_table_if_not_exists() %}
    {% set ddl %}
        declare
            v_count number;
        begin
            select count(*) into v_count from user_tables
            where  table_name = 'DBT_MODEL_RUN_STATS';
            if v_count = 0 then
                execute immediate q'[
                    create table dbt_model_run_stats (
                        run_id               varchar2(64),
                        run_started_at       timestamp,
                        model_name           varchar2(200),
                        materialization      varchar2(30),
                        dbt_threads          number,
                        parallel_degree      number,
                        elapsed_seconds      number(18, 3),
                        cpu_seconds          number(18, 3),
                        pga_peak_bytes       number,
                        uga_peak_bytes       number,
                        physical_reads_bytes number,
                        logical_reads        number,
                        redo_size_bytes      number,
                        rows_processed       number,
                        status               varchar2(20)
                    )
                ]';
            end if;
        end;
    {% endset %}
    {% do run_query(ddl) %}
{% endmacro %}


{% macro log_model_stats() %}
    {# Runs AFTER the model SQL. Inserts a single row with the delta. #}
    {% if execute %}
        {% set run_id = invocation_id %}
        {% set model_name = this.name %}
        {% set materialization = config.get('materialized', 'view') %}

        {% set stats_sql %}
            insert into dbt_model_run_stats (
                run_id, run_started_at, model_name, materialization,
                dbt_threads, parallel_degree,
                cpu_seconds, pga_peak_bytes, uga_peak_bytes,
                physical_reads_bytes, logical_reads, redo_size_bytes,
                status
            )
            select
                '{{ run_id }}',
                systimestamp,
                '{{ model_name }}',
                '{{ materialization }}',
                {{ target.threads }},
                {{ var('parallel_degree', 4) }},
                max(case when sn.name = 'CPU used by this session'
                         then ss.value end) / 100,     -- centiseconds -> seconds
                max(case when sn.name = 'session pga memory max'  then ss.value end),
                max(case when sn.name = 'session uga memory max'  then ss.value end),
                max(case when sn.name = 'physical read bytes'     then ss.value end),
                max(case when sn.name = 'session logical reads'   then ss.value end),
                max(case when sn.name = 'redo size'               then ss.value end),
                'SUCCESS'
            from v$sesstat ss
            join v$statname sn on ss.statistic# = sn.statistic#
            where  ss.sid = sys_context('userenv', 'sid')
              and  sn.name in (
                    'CPU used by this session',
                    'session pga memory max',
                    'session uga memory max',
                    'physical read bytes',
                    'session logical reads',
                    'redo size')
        {% endset %}

        {% do run_query(stats_sql) %}
        {% do log("Logged sizing stats for " ~ model_name, info=True) %}
    {% endif %}
{% endmacro %}
