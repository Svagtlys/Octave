import { Badge } from '../ui';
import styles from './ToolCallCard.module.css';

export type ToolCallStatus = 'calling' | 'done' | 'error';

export interface ToolCallCardProps {
  name: string;
  status: ToolCallStatus;
  /** Raw (possibly partial) JSON arguments string. */
  arguments: string;
  output?: string | null;
}

const STATUS_LABEL: Record<ToolCallStatus, string> = {
  calling: 'calling tool',
  done: 'done',
  error: 'error',
};

const STATUS_TONE = {
  calling: 'accent',
  done: 'ok',
  error: 'err',
} as const;

/**
 * Live tool-activity card (issue #45): name, status, streaming arguments,
 * and eventual output. Provisional state — ephemeral by design (spec
 * Decision 4); committed tool events survive reload once the transcript
 * API exposes them (follow-up issue).
 */
export function ToolCallCard({ name, status, arguments: args, output }: ToolCallCardProps) {
  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <span className={styles.name}>{name}</span>
        <Badge tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Badge>
      </div>
      {args && <pre className={styles.args}>{args}</pre>}
      {output != null && <pre className={styles.output}>{output}</pre>}
    </div>
  );
}
