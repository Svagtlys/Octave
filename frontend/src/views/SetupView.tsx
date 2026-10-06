/**
 * First-run owner creation (issue #122). Rendered only while the backend
 * reports setup_required; submitting calls /auth/bootstrap which sets the
 * session cookie and transitions straight to authed.
 */
import { useState, type CSSProperties, type FormEvent } from 'react';
import { Navigate } from '@tanstack/react-router';
import { ApiError } from '../lib/api/client';
import { useAuth } from '../lib/auth/useAuth';
import { colors } from '../theme';

export default function SetupView() {
  const { bootstrap, status } = useAuth();
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (status === 'authed') {
    return <Navigate to="/sessions" replace />;
  }
  if (status === 'anonymous') {
    // /setup is only for first-run instances.
    return <Navigate to="/login" replace />;
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await bootstrap({
        username,
        password,
        display_name: displayName.trim() === '' ? username : displayName,
      });
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? 'Setup has already been completed. Sign in instead.'
          : e instanceof ApiError && e.status === 400
            ? 'Username or password does not meet the requirements.'
            : 'Could not reach the server. Try again.'
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={styles.card}>
      <h2 style={styles.heading}>Welcome to Octave</h2>
      <p style={styles.note}>Create the first account. It becomes the owner.</p>
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
          Display name
          <input
            name="display_name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
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
            autoComplete="new-password"
            style={styles.input}
          />
        </label>
        {error !== null && (
          <p role="alert" style={styles.error}>
            {error}
          </p>
        )}
        <button type="submit" disabled={submitting} style={styles.button}>
          {submitting ? 'Creating…' : 'Create owner account'}
        </button>
      </form>
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  card: {
    maxWidth: 360,
    margin: '60px auto',
    padding: 28,
    border: `1px solid ${colors.border}`,
    borderRadius: 12,
    background: colors.panel,
  },
  heading: { fontSize: '1.2rem', marginBottom: 6, textAlign: 'center' },
  note: { fontSize: '0.85rem', color: colors.textDim, marginBottom: 18, textAlign: 'center' },
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
