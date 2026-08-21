# Demo script: magmom_collapse_collinear

- upload `magmom_collapse_collinear/input.zip` to `POST /api/v1/diagnosis/upload`
- run `POST /api/v1/diagnosis/run`
- `GET /api/v1/diagnosis/{id}` then `GET .../report`
- expected rule_ids: LOCAL_MOMENT_COLLAPSE
- expected issue_count_by_severity: {"critical": 0, "high": 0, "medium": 1, "low": 0, "info": 0}
- fix availability: no safe auto-fix
