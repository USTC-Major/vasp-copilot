import { aiApi, ApiError } from './client';

const encoder = new TextEncoder();

describe('aiApi unavailable responses', () => {
  afterEach(() => vi.restoreAllMocks());

  it('classifies a non-JSON 502 as unavailable for a read', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('<html>Bad Gateway</html>', { status: 502 }));
    await expect(aiApi.getSettings()).rejects.toMatchObject({
      code: 'AI_UNAVAILABLE', status: 502, retryable: true,
    } satisfies Partial<ApiError>);
  });

  it('classifies a connection failure without making a write retryable', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(aiApi.saveSettings({ max_jobs: 2 })).rejects.toMatchObject({
      code: 'AI_UNAVAILABLE', status: 0, retryable: false,
    } satisfies Partial<ApiError>);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('preserves an explicit disabled-service retryable false on a 503 read', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      error: { code: 'AI_MODE_DISABLED', message: '智能模式未启用', retryable: false },
    }), { status: 503, headers: { 'Content-Type': 'application/json' } }));
    await expect(aiApi.getSettings()).rejects.toMatchObject({
      code: 'AI_MODE_DISABLED', status: 503, retryable: false,
    } satisfies Partial<ApiError>);
  });
});

describe('aiApi.resolveConsent execution receipts', () => {
  afterEach(() => vi.restoreAllMocks());
  const base = { mode: 'ai', ok: false, kind: 'copy_inputs', approved: true };
  const businessError = { code: 'CONSENT_FAILED', message: '操作失败且未重试：registered source changed after confirmation', retryable: false };
  const resolve = () => aiApi.resolveConsent('project a', 'task/1', 'card-1', true);
  const mockJson = (body: unknown, status = 200) => vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  );

  it.each(['failed', 'expired', 'unknown', 'executed'])('保留 HTTP 2xx state=%s 的完整 error 执行回执，不按 ok 分类、不重试', async (state) => {
    const body = { ...base, state, error: businessError, card: { state, result: '执行凭据', reason: '执行原因' }, result: '顶层凭据', replayed: true };
    const fetchMock = mockJson(body, 201);
    await expect(resolve()).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/ai/v1/projects/project%20a/tasks/task%2F1/messages/consent', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ card_id: 'card-1', approved: true, note: '' }),
    }));
  });

  it.each(['executed', 'rejected'])('真实成功/拒绝 state=%s 的 error:null 及可空凭据原样保留', async (state) => {
    const body = { ...base, ok: true, state, error: null, result: null, card: { state, result: null, reason: null } };
    mockJson(body);
    await expect(resolve()).resolves.toEqual(body);
  });

  it.each([
    { error: businessError },
    { error: businessError, state: 'failed', card: { state: 'executed' } },
    { error: businessError, state: 'mystery' },
    { error: businessError, card: { state: 'failed' } },
  ])('把缺失/冲突/未知或仅 card.state 的回执交给页面分类 %#', async (receipt) => {
    const body = { ...base, ...receipt };
    mockJson(body);
    await expect(resolve()).resolves.toEqual(body);
  });

  it.each([400, 503])('HTTP %s 即使携带 executed 回执仍抛 ApiError，不重试', async (status) => {
    const fetchMock = mockJson({ ...base, state: 'executed', card: { state: 'executed' }, error: businessError }, status);
    await expect(resolve()).rejects.toMatchObject({ status, retryable: false } satisfies Partial<ApiError>);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(['not JSON', 'null', '[]', '"executed"', '{}', JSON.stringify({ ...base, state: 'executed', card: 'invalid' }),
    JSON.stringify({ ...base, state: 'executed', error: { message: { invalid: true } } })])('拒绝坏 JSON 或畸形结构，不能形成成功回执 %#', async (body) => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(body, { status: 200 }));
    await expect(resolve()).rejects.toMatchObject({ code: 'INVALID_CONSENT_RESPONSE', status: 200, retryable: false });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('网络失败仍抛不可重试错误', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(resolve()).rejects.toMatchObject({ code: 'AI_UNAVAILABLE', status: 0, retryable: false });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('普通 settings 的 HTTP 200 error 仍抛异常', async () => {
    mockJson({ ...base, state: 'executed', card: { state: 'executed' }, error: businessError });
    await expect(aiApi.getSettings()).rejects.toMatchObject({ code: 'CONSENT_FAILED', status: 200 });
  });
});

function responseFromChunks(chunks: string[]): { response: Response; stream: ReadableStream<Uint8Array> } {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return {
    stream,
    response: new Response(stream, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    }),
  };
}

async function collectStream(response: Response) {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(response);
  const events = [];
  for await (const event of aiApi.sendMessageStream('project a', 'task/1', 'hello')) {
    events.push(event);
  }
  return events;
}

describe('aiApi.sendMessageStream', () => {
  afterEach(() => vi.restoreAllMocks());

  it('解析跨 chunk 的 SSE，并在 done 后释放 reader', async () => {
    const { response, stream } = responseFromChunks([
      'data: {"type":"answer","text":"半',
      '段"}\n\n',
      'data: {"type":"done","answer":"半段"}',
    ]);

    await expect(collectStream(response)).resolves.toEqual([
      { type: 'answer', text: '半段' },
      { type: 'done', answer: '半段' },
    ]);
    expect(stream.locked).toBe(false);
  });

  it('EOF 前没有 done/stopped 时明确报错并释放 reader', async () => {
    const { response, stream } = responseFromChunks([
      'data: {"type":"answer","text":"未完成"}\n\n',
    ]);

    await expect(collectStream(response)).rejects.toThrow('未收到完成标记');
    expect(stream.locked).toBe(false);
  });

  it('调用方提前结束消费时取消流并释放 reader', async () => {
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"type":"thinking","text":"处理中"}\n\n'));
      },
      cancel() {
        cancelled = true;
      },
    });
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(stream, { status: 200 }));
    const generator = aiApi.sendMessageStream('p', 't', 'hello');

    await expect(generator.next()).resolves.toMatchObject({
      done: false,
      value: { type: 'thinking', text: '处理中' },
    });
    await generator.return(undefined);

    expect(cancelled).toBe(true);
    expect(stream.locked).toBe(false);
  });
});
