import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import HealthStatus from '../../src/components/HealthStatus';
import { fetchHealth } from '../../src/lib/api/client';

vi.mock('../../src/lib/api/client', () => ({
  fetchHealth: vi.fn(),
}));

describe('HealthStatus', () => {
  beforeEach(() => {
    vi.mocked(fetchHealth).mockReset();
  });

  it('shows Healthy after a successful health check', async () => {
    vi.mocked(fetchHealth).mockResolvedValue({ status: 'ok' });
    render(<HealthStatus />);
    expect(screen.getByText(/checking/i)).toBeDefined();
    expect(await screen.findByText('Healthy')).toBeDefined();
  });

  it('shows Unreachable when the backend is down', async () => {
    vi.mocked(fetchHealth).mockRejectedValue(new Error('network'));
    render(<HealthStatus />);
    expect(await screen.findByText('Unreachable')).toBeDefined();
  });
});
