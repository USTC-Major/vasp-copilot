import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ChatPanel from './ChatPanel';
vi.mock('../../hooks/useApi', () => ({
  useChatHistory: () => ({ data: { messages: [] } }),
  useChatHistoryClear: () => ({ mutate: vi.fn() }),
  useChatHistorySave: () => ({ mutate: vi.fn() }),
  useChatSend: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useLlmConfig: () => ({ data: { usable: false }, isLoading: false }),
}));
function viewport(width: number, height: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: height });
}
function expectVisible(element: HTMLElement, width: number, height: number) {
  const left = Number.parseFloat(element.style.left);
  const top = Number.parseFloat(element.style.top);
  expect(left).toBeGreaterThanOrEqual(8);
  expect(top).toBeGreaterThanOrEqual(8);
  expect(left + width).toBeLessThanOrEqual(window.innerWidth - 8);
  expect(top + height).toBeLessThanOrEqual(window.innerHeight - 8);
}
const frames = new Map<number, FrameRequestCallback>();
let frameId = 0;
function runFrames() {
  const queued = [...frames.values()];
  frames.clear();
  act(() => queued.forEach(callback => callback(0)));
}
beforeEach(() => {
  cleanup(); localStorage.clear(); viewport(1280, 960); frames.clear(); frameId = 0;
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation(callback => { frames.set(++frameId, callback); return frameId; });
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(id => { frames.delete(id); });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
describe('AI assistant viewport bounds', () => {
  it('clamps persisted desktop positions on a narrow first mount', () => {
    localStorage.setItem('vasp-ai-assistant-fab-pos', JSON.stringify({ left: 1093, top: 900 }));
    localStorage.setItem('vasp-ai-assistant-panel-pos', JSON.stringify({ left: 900, top: 450 }));
    viewport(375, 844);
    render(<ChatPanel onOpenSettings={vi.fn()} />);
    const fab = screen.getByRole('button', { name: '打开 AI 助手' });
    expectVisible(fab, 52, 52);
    fireEvent.click(fab);
    expectVisible(document.querySelector('.wf-chat-panel') as HTMLElement, 327, 560);
  });
  it('keeps the button and open panel in bounds after resize while retaining drag behavior', () => {
    render(<ChatPanel onOpenSettings={vi.fn()} />);
    const fab = screen.getByRole('button', { name: '打开 AI 助手' });
    fireEvent.mouseDown(fab, { button: 0, clientX: 1210, clientY: 890 });
    fireEvent.mouseMove(window, { clientX: 1180, clientY: 880 });
    fireEvent.mouseUp(window, { clientX: 1180, clientY: 880 });
    expect(Number.parseFloat(fab.style.left)).toBe(1174);
    // A second stationary press clears the existing suppression of a drag click.
    fireEvent.mouseDown(fab, { button: 0, clientX: 1180, clientY: 880 });
    fireEvent.mouseUp(window, { clientX: 1180, clientY: 880 });
    fireEvent.click(fab);
    viewport(375, 844);
    fireEvent(window, new Event('resize'));
    expectVisible(fab, 52, 52);
    expectVisible(document.querySelector('.wf-chat-panel') as HTMLElement, 327, 560);
  });

  it('coalesces moves without position storage writes and flushes the actual mouseup endpoint', () => {
    render(<ChatPanel onOpenSettings={vi.fn()} />);
    const fab = screen.getByRole('button', { name: '打开 AI 助手' });
    const writes = vi.spyOn(Storage.prototype, 'setItem');
    writes.mockClear();
    fireEvent.mouseDown(fab, { button: 0, clientX: 1210, clientY: 890 });
    fireEvent.mouseMove(window, { clientX: 1190, clientY: 880 });
    fireEvent.mouseMove(window, { clientX: 1170, clientY: 870 });
    expect(frames.size).toBe(1);
    expect(fab.style.left).toBe('1204px');
    expect(writes).not.toHaveBeenCalled();
    runFrames();
    expect(fab.style.left).toBe('1164px');
    expect(writes).not.toHaveBeenCalled();
    fireEvent.mouseMove(window, { clientX: 1150, clientY: 860 });
    fireEvent.mouseUp(window, { clientX: 1130, clientY: 850 });
    expect(fab.style.left).toBe('1124px');
    expect(fab.style.top).toBe('844px');
    expect(writes).toHaveBeenCalledExactlyOnceWith('vasp-ai-assistant-fab-pos', JSON.stringify({ left: 1124, top: 844 }));
    expect(frames.size).toBe(0);
    expect(fab.style.transitionProperty.split(',').map(value => value.trim())).toEqual(['background-color', 'border-color', 'color', 'box-shadow']);
  });

  it('suppresses a drag click even after returning to the origin, then allows keyboard and normal clicks', async () => {
    const user = userEvent.setup();
    render(<ChatPanel onOpenSettings={vi.fn()} />);
    const fab = screen.getByRole('button', { name: '打开 AI 助手' });
    fireEvent.mouseDown(fab, { button: 0, clientX: 1210, clientY: 890 });
    fireEvent.mouseMove(window, { clientX: 1170, clientY: 850 });
    fireEvent.mouseMove(window, { clientX: 1210, clientY: 890 });
    fireEvent.mouseUp(window, { clientX: 1210, clientY: 890 });
    fireEvent.click(fab, { detail: 1 });
    expect(screen.getByRole('button', { name: '打开 AI 助手' })).toBe(fab);
    fab.focus();
    await user.keyboard('{Enter}');
    expect(screen.getByRole('button', { name: '关闭 AI 助手' })).toBe(fab);
    await user.click(fab);
    expect(screen.getByRole('button', { name: '打开 AI 助手' })).toBe(fab);
  });

  it('blur flushes the last observed move and cancels frames; unmount removes listeners without stale updates', () => {
    const { unmount } = render(<ChatPanel onOpenSettings={vi.fn()} />);
    const fab = screen.getByRole('button', { name: '打开 AI 助手' });
    fireEvent.mouseDown(fab, { button: 0, clientX: 1210, clientY: 890 });
    fireEvent.mouseMove(window, { clientX: 1180, clientY: 860 });
    fireEvent(window, new Event('blur'));
    expect(fab.style.left).toBe('1174px');
    expect(fab.style.top).toBe('854px');
    expect(frames.size).toBe(0);
    expect(JSON.parse(localStorage.getItem('vasp-ai-assistant-fab-pos')!)).toEqual({ left: 1174, top: 854 });
    fireEvent.mouseMove(window, { clientX: 10, clientY: 10 });
    expect(frames.size).toBe(0);
    fireEvent.mouseDown(fab, { button: 0, clientX: 1180, clientY: 860 });
    fireEvent.mouseMove(window, { clientX: 1160, clientY: 850 });
    const remove = vi.spyOn(window, 'removeEventListener');
    unmount();
    expect(frames.size).toBe(0);
    expect(JSON.parse(localStorage.getItem('vasp-ai-assistant-fab-pos')!)).toEqual({ left: 1154, top: 844 });
    for (const event of ['mousemove', 'mouseup', 'resize', 'blur']) expect(remove).toHaveBeenCalledWith(event, expect.any(Function));
    fireEvent.mouseMove(window, { clientX: 0, clientY: 0 });
    expect(frames.size).toBe(0);
  });

  it('resize cancels an in-flight panel drag and clamps both positions before a stale frame can run', () => {
    render(<ChatPanel onOpenSettings={vi.fn()} />);
    const fab = screen.getByRole('button', { name: '打开 AI 助手' });
    fireEvent.click(fab);
    const panel = document.querySelector('.wf-chat-panel') as HTMLElement;
    const header = screen.getByText('AI 助手').parentElement!;
    fireEvent.mouseDown(header, { button: 0, clientX: 900, clientY: 300 });
    fireEvent.mouseMove(window, { clientX: 1000, clientY: 400 });
    expect(frames.size).toBe(1);
    viewport(375, 844);
    fireEvent(window, new Event('resize'));
    expect(frames.size).toBe(0);
    expectVisible(fab, 52, 52);
    expectVisible(panel, 327, 560);
    const position = { left: panel.style.left, top: panel.style.top };
    runFrames();
    fireEvent.mouseUp(window, { clientX: 1000, clientY: 400 });
    expect({ left: panel.style.left, top: panel.style.top }).toEqual(position);
    expect(JSON.parse(localStorage.getItem('vasp-ai-assistant-panel-pos')!)).toEqual({ left: Number.parseFloat(panel.style.left), top: Number.parseFloat(panel.style.top) });
  });
});
