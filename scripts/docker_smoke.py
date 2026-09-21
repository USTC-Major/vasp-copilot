#!/usr/bin/env python3
"""Real Docker smoke: standard library only; no models, SSH or job submission.

Requires Linux, Docker and Compose >= 2.24.4. The generated override clears
env_file and isolates tags, ports and volumes; the product Dockerfiles and
service commands are used unchanged. No host source/data is mounted.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

REPO = Path(__file__).resolve().parents[1]
LOG_LIMIT = 64 * 1024
TOOLBOX = '/api/v1/toolbox'


class Smoke:
    def __init__(self, output: Path, *, cleanup_only=False):
        self.output = output.resolve()
        self.evidence = self.output / 'evidence'
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.state_path = self.output / 'owner.json'
        self.deadline = time.monotonic() + 20 * 60
        self.checks = []
        self.summary = {'result': 'running', 'checks': self.checks,
                        'scope': 'real Docker containers; synthetic metadata only; no model/SSH/HPC calls'}
        # Do not pass caller API keys, COMPOSE_* variables or workflow credentials.
        allow = {'PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG',
                 'XDG_RUNTIME_DIR', 'TMPDIR', 'LANG'}
        self.env = {k: v for k, v in os.environ.items() if k in allow}
        self.env.update(COMPOSE_ANSI='never', DOCKER_BUILDKIT='1')
        if cleanup_only:
            state = json.loads(self.state_path.read_text())
            self.project = state['project']
        else:
            if self.state_path.exists():
                raise RuntimeError('Output already has an owner record; choose a fresh output directory')
            self.project = 'd0e9-' + uuid.uuid4().hex[:16]
            state = {'project': self.project, 'armed': False, 'cleaned': False}
            self.state_path.write_text(json.dumps(state), encoding='utf-8')
        self.state = state
        if not re.fullmatch(r'd0e9-[0-9a-f]{16}', self.project):
            raise ValueError('Refusing cleanup of an unrecognized project name')
        self.summary['compose_project'] = self.project
        self.empty_env = self.output / 'empty.env'
        self.override = self.output / 'compose.smoke.yml'
        self.empty_env.write_text('', encoding='utf-8')
        self.override.write_text(f'''services:
  backend:
    image: {self.project}-backend:smoke
    env_file: !reset []
  ai_mode:
    image: {self.project}-backend:smoke
    env_file: !reset []
  frontend:
    image: {self.project}-frontend:smoke
    ports: !override
      - target: 80
        published: "0"
        host_ip: 127.0.0.1
        protocol: tcp
''', encoding='utf-8')
        self.compose = ['docker', 'compose', '--project-name', self.project,
                        '--env-file', str(self.empty_env), '-f', str(REPO / 'docker-compose.yml'),
                        '-f', str(self.override)]

    def log(self, name, text):
        # Only controlled commands/config are recorded; redact common secret forms defensively.
        text = re.sub(r'(?i)((?:api[_-]?key|password|token|authorization)\s*[=:]\s*)[^\s,]+', r'\1<redacted>', text)
        raw = text.encode('utf-8', errors='replace')
        if len(raw) > LOG_LIMIT:
            raw = b'[tail only; log exceeded 64 KiB]\n' + raw[-LOG_LIMIT:]
        (self.evidence / name).write_bytes(raw)

    def run(self, args, *, name, timeout=120, cleanup=False, required=True):
        budget = timeout if cleanup else min(timeout, self.deadline - time.monotonic())
        if budget <= 0:
            raise TimeoutError('Smoke 20-minute budget exhausted; proceeding to cleanup')
        print(f'RUN {name}', flush=True)
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(args, cwd=REPO, env=self.env, stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                process.wait(timeout=budget)
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
            finally:
                output.seek(0)
                text = output.read().decode('utf-8', errors='replace')
                self.log(name, text)
        if required and process.returncode:
            raise RuntimeError(f'{name}: exit {process.returncode}; see diagnostic log')
        return text

    def dc(self, *args, name, **kwargs):
        return self.run([*self.compose, *args], name=name, **kwargs)

    def check(self, condition, label):
        if not condition:
            raise AssertionError(label)
        self.checks.append(label)
        print('PASS ' + label, flush=True)

    def request(self, path, *, body=None):
        # Only requests to this run's loopback nginx port; never an external URL.
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base_url + path, data=data,
                                        headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=10) as response:
            content = response.read(1024 * 1024)
            return json.loads(content) if 'application/json' in response.headers.get('Content-Type', '') else content.decode()

    def wait_http(self, path):
        # Retry GET readiness only; POST creation is always single-attempt.
        end = min(self.deadline, time.monotonic() + 90)
        while time.monotonic() < end:
            try:
                return self.request(path)
            except (OSError, ValueError):
                time.sleep(2)
        raise TimeoutError('HTTP readiness failed: ' + path)

    def container(self, service):
        value = self.dc('ps', '-q', service, name=f'id-{service}.log').strip()
        if not re.fullmatch(r'[0-9a-f]{12,64}', value):
            raise AssertionError('Expected exactly one container for ' + service)
        return value

    def healthy(self, service):
        cid = self.container(service)
        info = json.loads(self.run(['docker', 'inspect', cid], name=f'container-{service}.json'))[0]
        self.check(info['State']['Health']['Status'] == 'healthy', service + ': container healthy')
        mounts = {m['Destination']: m['Name'] for m in info['Mounts'] if m['Type'] == 'volume'}
        self.summary.setdefault('containers', {})[service] = {'id': cid, 'image_id': info['Image'], 'mounts': mounts}
        return cid, mounts

    def verify_runtime(self, service):
        code = """import importlib.metadata as m,json,os,sys,shutil
print(json.dumps({'uid':os.getuid(),'python':sys.executable,'uvicorn_path':shutil.which('uvicorn'),
                  'versions':{p:m.version(p) for p in ('uvicorn','fastapi','paramiko','httpx','pydantic','pymatgen')}}))
assert os.getuid()==10001
assert sys.executable.startswith('/opt/venv/')
assert shutil.which('uvicorn').startswith('/opt/venv/')
import uvicorn,fastapi,paramiko,httpx,pydantic
import pymatgen.core
"""
        data = self.dc('exec', '-T', service, 'python', '-c', code, name=f'runtime-{service}.log')
        self.summary.setdefault('runtime', {})[service] = json.loads(data)
        self.check(True, service + ': UID 10001 and dependencies in /opt/venv')

    def verify_config(self):
        raw = self.dc('--profile', 'ai', 'config', '--format', 'json', name='compose-config.json')
        config = json.loads(raw)
        self.check(set(config['services']) == {'backend', 'frontend', 'ai_mode'}, 'only three expected services')
        volumes = config.get('volumes', {})
        self.check(set(volumes) == {'app_data', 'temp_data'}, 'only expected temporary volume declarations')
        for key, value in volumes.items():
            self.check(value.get('name') == f'{self.project}_{key}' and not value.get('external'), 'isolated named volume: ' + key)
        for name, svc in config['services'].items():
            self.check(not svc.get('env_file'), name + ': no env_file')
            self.check(not svc.get('secrets') and not svc.get('configs'), name + ': no secret/config mounts')
            for mount in svc.get('volumes', []):
                self.check(mount['type'] == 'volume' and mount['source'] in volumes, name + ': no bind mount')
            env = svc.get('environment', {})
            allowed = {'DATA_DIR', 'TTL_SECONDS', 'VASP_AI_HOME'}
            if name == 'ai_mode':
                allowed |= {'ENABLE_AI_MODE', 'TOOLBOX_URL'}
                self.check(env.get('TOOLBOX_URL') == 'http://backend:8000', 'AI uses container-network Toolbox URL')
            self.check(set(env) <= allowed, name + ': no model or SSH environment injected')
            if name != 'frontend':
                self.check(not svc.get('ports'), name + ': not published to host')
        ports = config['services']['frontend']['ports']
        self.check(len(ports) == 1 and ports[0]['host_ip'] == '127.0.0.1' and str(ports[0]['published']) == '0', 'nginx uses one random loopback port')

    def run_smoke(self):
        self.summary['code_sha'] = self.run(['git', 'rev-parse', 'HEAD'], name='code-sha.log').strip()
        expected = os.environ.get('SMOKE_EXPECTED_SHA')
        self.check(not expected or expected == self.summary['code_sha'], 'checkout matches requested PR head')
        # COPY . in the real Dockerfiles must never pick up a caller's local env.
        for context in ('backend', 'frontend'):
            local_envs = [p.name for p in (REPO / context).glob('.env*')
                          if p.is_file() and p.name != '.env.example']
            self.check(not local_envs, context + ': no local .env files in build context')
        self.run(['docker', 'version'], name='docker-version.log')
        self.run(['docker', 'compose', 'version'], name='compose-version.log')
        self.verify_config()
        # Refuse existing resources even in the extraordinarily unlikely UUID collision.
        for kind in ('container', 'volume', 'network'):
            ids = self.run(['docker', kind, 'ls', '-aq' if kind == 'container' else '-q', '--filter', f'label=com.docker.compose.project={self.project}'], name=f'preexisting-{kind}.log')
            self.check(not ids.strip(), 'new project has no existing ' + kind)
        # Only now may finally/CI fallback remove this project's resources.
        self.state['armed'] = True
        self.state_path.write_text(json.dumps(self.state), encoding='utf-8')
        self.dc('build', '--no-cache', 'backend', 'frontend', name='build.log', timeout=900)
        for service in ('backend', 'frontend'):
            tag = f'{self.project}-{service}:smoke'
            info = json.loads(self.run(['docker', 'image', 'inspect', tag], name=f'image-{service}.json'))[0]
            self.summary.setdefault('images', {})[service] = {'id': info['Id'], 'created': info['Created'], 'tag': tag}
        self.dc('up', '-d', '--no-build', '--wait', '--wait-timeout', '150', name='up-default.log', timeout=180)
        services = self.dc('ps', '--services', '--status', 'running', name='default-services.log').split()
        self.check(set(services) == {'backend', 'frontend'}, 'default profile runs Toolbox and frontend only')
        ai_ids = self.run(['docker', 'container', 'ls', '-aq', '--filter', f'label=com.docker.compose.project={self.project}', '--filter', 'label=com.docker.compose.service=ai_mode'], name='default-ai-absent.log')
        self.check(not ai_ids.strip(), 'AI container does not exist in default phase')
        self.healthy('backend')
        self.healthy('frontend')
        address = self.dc('port', 'frontend', '80', name='frontend-port.log').strip()
        self.check(bool(re.fullmatch(r'127\.0\.0\.1:[1-9][0-9]*', address)), 'nginx bound to assigned loopback port')
        self.base_url = 'http://' + address
        self.check('<html' in self.wait_http('/').lower(), 'nginx serves built frontend without AI')
        self.check('<html' in self.request('/toolbox/projects').lower(), 'SPA task entry resolves through nginx')
        try:
            self.request('/ai/v1/ping')
        except urllib.error.HTTPError as exc:
            self.check(exc.code >= 400, 'nginx reports AI unavailable when profile is absent')
        else:
            raise AssertionError('Absent AI unexpectedly returned HTTP success')
        self.check(self.wait_http(TOOLBOX + '/projects')['projects'] == [], 'nginx proxies empty Toolbox store')
        self.verify_runtime('backend')
        project = self.request(TOOLBOX + '/projects', body={'name': 'Docker smoke synthetic project'})['project']
        task = self.request(TOOLBOX + f"/projects/{project['id']}/tasks", body={'title': 'Metadata persistence only', 'goal': 'No computation'})['task']
        self.summary['synthetic_ids'] = {'project': project['id'], 'task': task['id']}
        task_path = f"/projects/{project['id']}/tasks/{task['id']}/detail"
        before = self.request(TOOLBOX + task_path)
        self.check(before['task_id'] == task['id'] and before['backend_mode'] == 'None' and not before['flow']['jobs'], 'synthetic task has no backend or jobs')
        self.dc('--profile', 'ai', 'up', '-d', '--no-build', '--wait', '--wait-timeout', '120', 'ai_mode', name='up-ai.log', timeout=150)
        self.check(self.wait_http('/ai/v1/ping')['enabled'] is True, 'optional AI enabled and nginx proxy healthy')
        self.healthy('ai_mode')
        self.verify_runtime('ai_mode')
        ai_detail = self.request('/ai/v1' + task_path)
        self.check(ai_detail['task'] == before['task'] and ai_detail['flow'] == before['flow'], 'AI reads the same authoritative task and execution state')
        containers = {service: self.healthy(service) for service in ('backend', 'ai_mode')}
        self.dc('--profile', 'ai', 'up', '-d', '--no-build', '--force-recreate', '--wait', '--wait-timeout', '120', 'backend', 'ai_mode', name='recreate.log', timeout=150)
        for service, (old_id, old_mounts) in containers.items():
            new_id, new_mounts = self.healthy(service)
            self.summary.setdefault('recreation', {})[service] = {
                'before_id': old_id, 'after_id': new_id, 'before_mounts': old_mounts, 'after_mounts': new_mounts}
            self.check(new_id != old_id and old_mounts == new_mounts, service + ': recreated container with same persistent volumes')
        after = self.wait_http(TOOLBOX + task_path)
        self.check(after['task'] == before['task'] and after['flow'] == before['flow'], 'task persists across backend and AI recreation')
        self.check(self.wait_http('/ai/v1' + task_path)['task'] == before['task'], 'AI reads persisted task after recreation')
        self.dc('--profile', 'ai', 'stop', 'ai_mode', name='stop-ai.log', timeout=60)
        self.check('ai_mode' not in self.dc('--profile', 'ai', 'ps', '--services', '--status', 'running', name='after-stop-services.log').split(), 'AI is stopped')
        ai_state = self.run(['docker', 'inspect', '--format', '{{.State.Status}}', self.summary['containers']['ai_mode']['id']], name='ai-stopped-state.log').strip()
        self.check(ai_state == 'exited', 'stopped AI is expected exited, not a health failure')
        self.check(self.wait_http(TOOLBOX + task_path)['task'] == before['task'], 'Toolbox remains available after AI stops')
        self.check('<html' in self.request('/').lower(), 'frontend remains available after AI stops')
        self.healthy('backend')
        self.healthy('frontend')

    def cleanup(self):
        # Explicit project scope only. Never prune or remove unrelated resources.
        if not self.state.get('armed') or self.state.get('cleaned'):
            print('No owned resources to clean, or cleanup already completed', flush=True)
            return
        try:
            self.dc('--profile', 'ai', 'logs', '--no-color', '--tail', '100', name='services.log', timeout=30, cleanup=True, required=False)
        except Exception as exc:
            # Diagnostic collection must never prevent the scoped teardown.
            self.log('log-collection-warning.log', f'{type(exc).__name__}: {exc}')
        self.dc('--profile', 'ai', 'down', '--volumes', '--remove-orphans', '--timeout', '15', name='cleanup.log', timeout=90, cleanup=True)
        for kind in ('container', 'volume', 'network'):
            ids = self.run(['docker', kind, 'ls', '-aq' if kind == 'container' else '-q', '--filter', f'label=com.docker.compose.project={self.project}'], name=f'cleanup-{kind}.log', timeout=15, cleanup=True)
            self.check(not ids.strip(), 'cleanup removed only this project ' + kind)
        self.summary['cleanup'] = 'passed'
        self.state['cleaned'] = True
        self.state_path.write_text(json.dumps(self.state), encoding='utf-8')

    def save_summary(self):
        (self.evidence / 'summary.json').write_text(json.dumps(self.summary, indent=2), encoding='utf-8')
        lines = ['## Docker integration smoke', '', f"Result: **{self.summary['result']}**",
                 f"Code SHA: `{self.summary.get('code_sha', 'unavailable')}`",
                 f"Project: `{self.project}`", f"Assertions passed: {len(self.checks)}",
                 f"Cleanup: {self.summary.get('cleanup', 'not confirmed')}", '',
                 'Actual Docker containers; only synthetic project/task metadata. No model, SSH or HPC requests.',
                 'No images or dependency caches uploaded. Diagnostic logs are limited to 64 KiB tails.']
        if self.summary.get('error'):
            lines.extend(['', 'Failure: ' + self.summary['error']])
        self.log('summary.md', '\n\n'.join(lines) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Fresh, disposable host evidence directory')
    parser.add_argument('--cleanup-only', action='store_true')
    args = parser.parse_args()
    if args.cleanup_only and not (args.output / 'owner.json').exists():
        print('No smoke owner record; nothing to clean')
        return 0
    smoke = Smoke(args.output, cleanup_only=args.cleanup_only)
    if args.cleanup_only:
        smoke.cleanup()
        saved = smoke.evidence / 'summary.json'
        if saved.exists() and smoke.state.get('cleaned'):
            smoke.summary = json.loads(saved.read_text(encoding='utf-8'))
            smoke.checks = smoke.summary['checks']
            smoke.summary['cleanup'] = 'passed'
            smoke.save_summary()
        return 0
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt('Interrupted; cleaning only this smoke project')
    signal.signal(signal.SIGTERM, interrupted)
    failed = False
    try:
        smoke.run_smoke()
        smoke.summary['result'] = 'passed'
    except BaseException as exc:
        failed = True
        smoke.summary.update(result='failed', error=f'{type(exc).__name__}: {exc}')
        print(smoke.summary['error'], file=sys.stderr, flush=True)
    finally:
        try:
            smoke.cleanup()
        except Exception as exc:
            failed = True
            smoke.summary.update(result='failed', cleanup=f'failed: {type(exc).__name__}: {exc}')
        smoke.save_summary()
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
