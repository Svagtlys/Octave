/**
 * Sign-in screen (issue #122). A 401 from /auth/login is form feedback —
 * it renders inline rather than bouncing through the auth interceptor.
 */
import { useState, type CSSProperties, type FormEvent } from 'react';
import { Navigate, useNavigate } from '@tanstack/react-router';
import { ApiError } from '../lib/api/client';
import { useAuth } from '../lib/auth/useAuth';
import { colors } from '../theme';

export default function LoginView() {
  const { login, status } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (status === 'authed') {
    return <Navigate to="/sessions" replace />;
  }
  if (status === 'setup_required') {
    return <Navigate to="/setup" replace />;
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username, password);
      navigate({ to: '/sessions' });
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 401
          ? 'Invalid username or password.'
          : 'Could not reach the server. Try again.'
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={styles.card}>
      <h2 style={styles.heading}>Sign in</h2>
      <form onSubmit={onSubmit} style={styles.form}>
        <label style={styles.label}>
          Username
          <input
            name="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            style={styles.input}
          />
        </label>
        <label style={styles.label}>
          Password
          <input
            name="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            style={styles.input}
          />
        </label>
        {error !== null && (
          <p role="alert" style={styles.error}>
            {error}
          </p>
        )}
        <button type="submit" disabled={submitting} style={styles.button}>
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  card: {
    maxWidth: 340,
    margin: '80px auto',
    padding: 28,
    border: `1px solid ${colors.border}`,
    borderRadius: 12,
    background: colors.panel,
  },
  heading: { fontSize: '1.2rem', marginBottom: 18, textAlign: 'center' },
  form: { display: 'flex', flexDirection: 'column', gap: 14 },
  label: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    fontSize: '0.85rem',
    color: colors.textDim,
  },
  input: {
    background: colors.bgDeep,
    border: `1px solid ${colors.border}`,
    borderRadius: 7,
    padding: '9px 12px',
    color: colors.text,
    fontSize: '0.9rem',
  },
  error: { color: colors.err, fontSize: '0.85rem', margin: 0 },
  button: {
    marginTop: 4,
    background: colors.accent,
    color: '#fff',
    border: 'none',
    borderRadius: 7,
    padding: '10px 12px',
    fontSize: '0.9rem',
    fontWeight: 600,
    cursor: 'pointer',
  },
};
