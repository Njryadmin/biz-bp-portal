# Business Lines

This directory holds **all** business-line-specific code. The rest of the monorepo
(`apps/`, `infra/`, `packages/`) is generic and must never import a specific
business line by name.

## How to add a new business line (5 steps)

1. **Copy the template**:
   ```bash
   cp -r business_lines/_template business_lines/<line_id>
   ```
   where `<line_id>` is a URL-safe slug (e.g. `consumer_loan`, `wealth_mgmt`).

2. **Edit the manifest** at `business_lines/<line_id>/manifest.yaml`:
   - `id`, `name`, `description`
   - `nav` entries (each becomes a left-nav link)
   - `api_prefix` (e.g. `/api/lines/<line_id>`)
   - `warehouse.schema` names

3. **Edit indicators** at `business_lines/<line_id>/indicators.yaml`:
   - list of `indicators` and `charts` rendered by `packages/ui`

4. **Add DBT models** under `business_lines/<line_id>/dbt/models/` and
   `dbt/dbt_project.yml` (use `_template/dbt/dbt_project.yml.example`).

5. **Register the line** in `business_lines/registry.yaml`:
   ```yaml
   lines:
     - id: <line_id>
       manifest: business_lines/<line_id>/manifest.yaml
   ```

That's it. Restart the API and the Web. The new line will:
- appear in the left navigation (auto-discovered by `apps/web/app/(dashboard)/layout.tsx`),
- expose its API at `<api_prefix>` (auto-loaded by `apps/api/app/routers/registry.py`).

## What you must NOT do

- Do **not** import `business_lines/<line>/*` from `apps/*` or `packages/*`.
- Do **not** add the line's name to any list in `apps/*`.
- Do **not** edit a single file outside `business_lines/<line>/` to add the line.
