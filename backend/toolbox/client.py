"""HTTP-only execution adapter; failures never fall back to local execution."""
import os
import httpx
from .contracts import ToolboxError

class ToolboxClient:
    def __init__(self, *, client=None, url=None):
        self._owns_client = client is None
        self.client = client or httpx.Client(base_url=url or os.environ.get('TOOLBOX_URL', 'http://127.0.0.1:8000'), timeout=120)

    def request(self, method, path, **kwargs):
        try:
            response = self.client.request(method, '/api/v1/toolbox' + path, **kwargs)
        except httpx.HTTPError as exc:
            raise ToolboxError('TOOLBOX_UNAVAILABLE', 'Toolbox连接中断；请核对任务状态，写操作不会自动重试', 503) from exc
        try:
            body = response.json()
        except ValueError:
            raise ToolboxError('TOOLBOX_BAD_RESPONSE', 'Toolbox响应无法解析；操作不会重放', 503) from None
        if response.status_code >= 400:
            error = body.get('error') or {}
            raise ToolboxError(error.get('code', 'TOOLBOX_ERROR'), error.get('message', 'Toolbox请求失败'), response.status_code, error.get('retryable', False))
        return body

    @staticmethod
    def task_path(project_id, task_id):
        from urllib.parse import quote
        return '/projects/' + quote(project_id, safe='') + '/tasks/' + quote(task_id, safe='')

    def tool(self, project_id, task_id, name, args):
        return self.request('POST', self.task_path(project_id, task_id) + '/tools', json={'name': name, 'args': args})

    def close(self):
        if self._owns_client:
            self.client.close()
