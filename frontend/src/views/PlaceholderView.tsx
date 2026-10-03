import type { CSSProperties } from 'react';
import { colors } from '../theme';

interface PlaceholderViewProps {
  title: string;
  note: string;
}

/** Dashed card shown for routes whose real UI hasn't been built yet. */
export default function PlaceholderView({ title, note }: PlaceholderViewProps) {
  return (
    <div style={styles.card}>
      <h2 style={styles.heading}>{title}</h2>
      <p style={styles.note}>{note}</p>
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  card: {
    border: `1px dashed ${colors.border}`,
    borderRadius: 9,
    padding: '40px 24px',
    textAlign: 'center',
    color: colors.textDim,
    maxWidth: 460,
    margin: '30px auto',
    background: 'rgba(255,255,255,0.015)',
  },
  heading: { color: colors.text, fontSize: '1.05rem', marginBottom: 8 },
  note: { fontSize: '0.85rem', lineHeight: 1.5 },
};
