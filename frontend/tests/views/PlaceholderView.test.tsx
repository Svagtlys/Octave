import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import PlaceholderView from '../../src/views/PlaceholderView';

describe('PlaceholderView', () => {
  it('renders the view title as a heading and the note', () => {
    render(<PlaceholderView title="Vault" note="Comes later." />);
    expect(screen.getByRole('heading', { level: 2, name: 'Vault' })).toBeDefined();
    expect(screen.getByText('Comes later.')).toBeDefined();
  });
});
