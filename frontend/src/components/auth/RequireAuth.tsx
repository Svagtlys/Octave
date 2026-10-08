/**
 * Route guard (issue #122). Wraps the app shell so anonymous visitors are
 * sent to /login and first-run instances to /setup. While the initial
 * probe is in flight, render nothing rather than flashing the shell.
 */
import type { ReactNode } from 'react';
import { Navigate } from '@tanstack/react-router';
import { useAuth } from '../../lib/auth/useAuth';

export default function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();

  if (status === 'loading') {
    return null;
  }
  if (status === 'setup_required') {
    return <Navigate to="/setup" replace />;
  }
  if (status === 'anonymous') {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}
