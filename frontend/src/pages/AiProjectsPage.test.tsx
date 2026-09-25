import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { HttpResponse, http } from 'msw';
import { server } from '../mocks/server';
import AiProjectsPage from './AiProjectsPage';

const renderPage = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><AiProjectsPage /></MemoryRouter></QueryClientProvider>);
};

it('shows project failure without asserting an empty project list and retries only projects', async () => {
  let projects = 0;
  let queue = 0;
  server.use(
    http.get('/ai/v1/projects', () => {
      projects += 1;
      if (projects === 1) return HttpResponse.text('Bad Gateway', { status: 502 });
      return HttpResponse.json({ projects: [] });
    }),
    http.get('/ai/v1/jobs/waiting', () => { queue += 1; return HttpResponse.json({ waiting: [], count: 0 }); }),
  );
  renderPage();
  expect(await screen.findByText('项目加载失败')).toBeInTheDocument();
  expect(screen.queryByText(/共 0 个项目/)).not.toBeInTheDocument();
  expect(screen.queryByText(/暂无项目/)).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '重试项目' }));
  expect(await screen.findByText(/暂无项目/)).toBeInTheDocument();
  expect(projects).toBe(2);
  expect(queue).toBe(1);
});

it('shows queue failure independently and retries only the queue', async () => {
  let projects = 0;
  let queue = 0;
  server.use(
    http.get('/ai/v1/projects', () => { projects += 1; return HttpResponse.json({ projects: [] }); }),
    http.get('/ai/v1/jobs/waiting', () => {
      queue += 1;
      if (queue === 1) return HttpResponse.text('Service Unavailable', { status: 503 });
      return HttpResponse.json({ waiting: [], count: 0 });
    }),
  );
  renderPage();
  expect(await screen.findByText('等待队列加载失败')).toBeInTheDocument();
  expect(screen.queryByText(/当前无排队作业/)).not.toBeInTheDocument();
  expect(screen.getByText(/暂无项目/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '重试队列' }));
  await waitFor(() => expect(screen.getByText(/当前无排队作业/)).toBeInTheDocument());
  expect(projects).toBe(1);
  expect(queue).toBe(2);
});
