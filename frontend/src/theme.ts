// Shared color tokens for the application shell.
// Extends the palette in src/index.css; inline styles reference these constants.

export const colors = {
  bg: '#1a1a2e',
  bgDeep: '#14142a',
  panel: '#1f1f38',
  border: '#2e2e4e',
  text: '#eaeaea',
  textDim: '#a0a0b8',
  accent: '#8b7cf6',
  accentSoft: 'rgba(139, 124, 246, 0.14)',
  ok: '#4ade80',
  okSoft: 'rgba(74, 222, 128, 0.12)',
  err: '#f87171',
  errSoft: 'rgba(248, 113, 113, 0.13)',
  warn: '#facc15',
  warnSoft: 'rgba(250, 204, 21, 0.13)',
} as const;
