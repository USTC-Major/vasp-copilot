import { beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
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
beforeEach(() => { cleanup(); localStorage.clear(); viewport(1280, 960); });
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
});
