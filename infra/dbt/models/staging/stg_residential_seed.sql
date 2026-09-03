-- infra/dbt/models/staging/stg_residential_seed.sql
-- Reference-data view for residential projects, loaded from a dbt seed
-- (infra/dbt/seeds/sample_residential.csv).

{{ config(materialized='view') }}

select
    project_id,
    project_name,
    city,
    manager,
    region
from {{ ref('sample_residential') }}
