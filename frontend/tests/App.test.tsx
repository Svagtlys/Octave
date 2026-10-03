import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { createMemoryHistory, createRouter, RouterProvider } from '@tanstack/react-router';
import { routeTree } from '../src/router';
import { fetchHealth } from '../src/lib/api/client';

// Full-tree renders mount Sidebar -> HealthStatus; keep tests hermetic.
vi.mock('../src/lib/api/client', () => ({ fetchHealth: vi.fn() }));
vi.mocked(fetchHealth).mockResolvedValue({ status: 'ok' });

function renderAt(path: string) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  render(<RouterProvider router={router} />);
}

describe('App routing', () => {
  it('redirects / to /sessions (shows Sessions view)', async () => {
    renderAt('/');
    expect(await screen.findByRole('heading', { level: 2, name: 'Sessions' })).toBeDefined();
  });

  it('renders the Vault placeholder at /vault', async () => {
    renderAt('/vault');
    expect(await screen.findByRole('heading', { level: 2, name: 'Vault' })).toBeDefined();
  });

  it('renders the MCP placeholder at /mcp', async () => {
    renderAt('/mcp');
    expect(await screen.findByRole('heading', { level: 2, name: 'MCP Servers' })).toBeDefined();
  });

  it('renders the Agents placeholder at /agents', async () => {
    renderAt('/agents');
    expect(await screen.findByRole('heading', { level: 2, name: 'Agents' })).toBeDefined();
  });

  it('renders the Settings placeholder at /settings', async () => {
    renderAt('/settings');
    expect(await screen.findByRole('heading', { level: 2, name: 'Settings' })).toBeDefined();
  });

  it('renders a Not Found view for unknown routes', async () => {
    renderAt('/nope');
    expect(await screen.findByRole('heading', { level: 2, name: 'Not Found' })).toBeDefined();
  });

  it('shows the brand and sidebar on every route', async () => {
    renderAt('/agents');
    expect(await screen.findByText('Octave')).toBeDefined();
    expect(screen.getByRole('complementary')).toBeDefined();
  });

  it('provides a skip link targeting the main content', async () => {
    renderAt('/sessions');
    const skip = await screen.findByRole('link', { name: /skip to main content/i });
    expect(skip.getAttribute('href')).toBe('#main-content');
    expect(document.getElementById('main-content')).not.toBeNull();
  });
});
