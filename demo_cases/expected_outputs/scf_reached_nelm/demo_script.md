# Demo script: scf_reached_nelm

- upload `scf_reached_nelm/input.zip` to `POST /api/v1/diagnosis/upload`
- run `POST /api/v1/diagnosis/run`
- `GET /api/v1/diagnosis/{id}` then `GET .../report`
- expected rule_ids: OUTCAR_TRUNCATED, SCF_REACHED_NELM
- expected issue_count_by_severity: {"critical": 0, "high": 1, "medium": 1, "low": 0, "info": 0}
- fix availability: no safe auto-fix
