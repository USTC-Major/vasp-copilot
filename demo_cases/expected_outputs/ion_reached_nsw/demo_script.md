# Demo script: ion_reached_nsw

- upload `ion_reached_nsw/input.zip` to `POST /api/v1/diagnosis/upload`
- run `POST /api/v1/diagnosis/run`
- `GET /api/v1/diagnosis/{id}` then `GET .../report`
- expected rule_ids: IONIC_REACHED_NSW
- expected issue_count_by_severity: {"critical": 0, "high": 1, "medium": 0, "low": 0, "info": 0}
- fix availability: no safe auto-fix
