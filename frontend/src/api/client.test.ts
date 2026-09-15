import { aiApi } from './client';

const encoder = new TextEncoder();

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
