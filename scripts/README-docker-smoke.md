# Docker integration smoke

Run from a clean checkout on Linux with Docker and Docker Compose **2.24.4+**:

```sh
python3 scripts/docker_smoke.py --output /tmp/vasp-doctor-smoke-unique-run
```

Use a fresh output directory for each run. Building downloads base images and
Python/npm dependencies; this is not an offline test. The script builds the
existing Dockerfiles without layer cache and runs the existing service commands.
It does not install Docker, publish images or require application secrets.

The test verifies:

- Default Compose starts healthy backend/frontend while the AI container is absent;
  nginx serves the compiled SPA and Toolbox API, and reports unavailable AI.
- Both Python services use `/opt/venv`, import their installed dependencies and
  run as UID 10001. The existing nginx image is tested with its own normal user setup.
- A synthetic project/task created through nginx's Toolbox API is returned by
  the optional AI service with exactly the same ID and saved execution state.
- Recreating backend/AI containers retains the same temporary named volumes and
  task. Stopping AI leaves Toolbox and the frontend available.
- Cleanup removes this run's containers, network and volumes only.

A generated Compose override removes `env_file`, uses unique local image tags and
one random loopback frontend port. An explicit empty `--env-file` prevents Compose
from loading the repository `.env`. Model/SSH environment variables are not inherited;
the merged configuration is checked before any resources are created. Local `.env*`
files other than `.env.example` in the two build-context roots cause a refusal.
There are no source/data bind mounts, chat calls, model requests, SSH calls, job
submissions, cancellations or scientific calculations.

The script has a 20-minute test budget and bounded command timeouts. Its `finally`
block preserves diagnostic service logs and runs project-scoped `down --volumes`.
If interrupted before cleanup completes, the same checkout can use its owner record:

```sh
python3 scripts/docker_smoke.py --output /tmp/vasp-doctor-smoke-unique-run --cleanup-only
```

No global prune is used; unique build images remain local until the disposable CI
runner is destroyed (on a local machine, keep or remove those exact recorded tags
yourself). Do not point cleanup at another run's output directory.

The PR workflow runs on a standard `ubuntu-24.04` runner with `contents: read` and
a 25-minute job limit. It tests the exact PR head SHA, including before merge.
Checkout and artifact actions are pinned to verified upstream release commit SHAs.
Evidence includes code SHA, image IDs, dependency versions, container IDs/volumes,
assertions and bounded logs. Only this small evidence directory is uploaded, for
**one day**; no images, build layers or dependency caches are uploaded.

Inspect `evidence/summary.json` and the actual workflow result before claiming
Docker passed. Python/YAML parsing or reviewing the Dockerfile cannot substitute
for a completed container build/startup run. This smoke does not validate real
HPC, models, SSH, scientific results, browser interaction or production operations.
