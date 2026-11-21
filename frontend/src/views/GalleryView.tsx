import { useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Field,
  Input,
  Modal,
  Select,
  Textarea,
  Tooltip,
  TooltipProvider,
} from '../components/ui';
import styles from './GalleryView.module.css';

/**
 * Dev-only showcase of the shared UI primitives (issue #7).
 * Review target for look & feel before the primitives are considered final.
 * Mounted at /gallery without auth so it's reachable without the backend.
 */
export default function GalleryView() {
  const [modalOpen, setModalOpen] = useState(false);
  const [checked, setChecked] = useState(true);
  const [fruit, setFruit] = useState('apple');
  const [name, setName] = useState('');

  const nameError = name.length > 0 && name.length < 3 ? 'Too short — minimum 3 characters.' : undefined;

  return (
    <TooltipProvider>
      <div className={styles.page}>
        <header className={styles.pageHeader}>
          <h1>UI Primitives Gallery</h1>
          <p>
            Shared component library from issue #7 — review look & feel here before approving.
          </p>
        </header>

        <section className={styles.section}>
          <h2>Buttons</h2>
          <div className={styles.row}>
            <Button>Primary</Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="danger">Danger</Button>
          </div>
          <div className={styles.row}>
            <Button size="sm">Small primary</Button>
            <Button size="sm" variant="secondary">
              Small secondary
            </Button>
            <Button disabled>Disabled primary</Button>
            <Button variant="secondary" disabled>
              Disabled secondary
            </Button>
          </div>
        </section>

        <section className={styles.section}>
          <h2>Badges</h2>
          <div className={styles.row}>
            <Badge>Neutral</Badge>
            <Badge tone="accent">Accent</Badge>
            <Badge tone="ok">Connected</Badge>
            <Badge tone="warn">Degraded</Badge>
            <Badge tone="err">Failed</Badge>
          </div>
        </section>

        <section className={styles.section}>
          <h2>Form fields (elicitation-ready)</h2>
          <div className={styles.formGrid}>
            <Field
              htmlFor="g-name"
              label="Server name"
              description="Shown in the MCP server list."
              error={nameError}
            >
              <Input
                id="g-name"
                placeholder="e.g. home-assistant"
                value={name}
                invalid={Boolean(nameError)}
                onChange={(e) => setName(e.target.value)}
              />
            </Field>

            <Field htmlFor="g-fruit" label="Enum field (elicitation select)">
              <Select
                id="g-fruit"
                value={fruit}
                onValueChange={setFruit}
                options={[
                  { value: 'apple', label: 'Apple' },
                  { value: 'banana', label: 'Banana' },
                  { value: 'cherry', label: 'Cherry' },
                  { value: 'durian', label: 'Durian (banned)', disabled: true },
                ]}
              />
            </Field>

            <Field htmlFor="g-notes" label="Textarea">
              <Textarea id="g-notes" placeholder="Optional notes…" />
            </Field>

            <Field htmlFor="g-disabled" label="Disabled input">
              <Input id="g-disabled" disabled defaultValue="read-only value" />
            </Field>

            <Checkbox
              id="g-check"
              label="Boolean field (elicitation checkbox)"
              checked={checked}
              onCheckedChange={(v) => setChecked(v === true)}
            />
          </div>
        </section>

        <section className={styles.section}>
          <h2>Cards</h2>
          <div className={styles.cardGrid}>
            <Card header="MCP Server" footer={<Button size="sm">Configure</Button>}>
              <p>
                Card with header and footer. Body content flows here — server status, tool counts,
                connection info.
              </p>
            </Card>
            <Card>
              <Badge tone="ok">connected</Badge>
              <p className={styles.cardGap}>Bare card, no header. Badges inline with text.</p>
            </Card>
          </div>
        </section>

        <section className={styles.section}>
          <h2>Overlays</h2>
          <div className={styles.row}>
            <Button onClick={() => setModalOpen(true)}>Open modal</Button>
            <Tooltip content="Tooltips appear on hover and keyboard focus.">
              <Button variant="secondary">Hover me (tooltip)</Button>
            </Tooltip>
          </div>
        </section>

        <Modal
          open={modalOpen}
          onOpenChange={setModalOpen}
          title="Confirm deletion"
          description="Focus is trapped inside; ESC or the backdrop closes. Actions go in the footer."
          footer={
            <>
              <Button variant="ghost" onClick={() => setModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="danger" onClick={() => setModalOpen(false)}>
                Delete
              </Button>
            </>
          }
        >
          <p>
            This modal demonstrates the dialog primitive: overlay, focus trap, scroll lock, and
            title/description slots wired for screen readers.
          </p>
        </Modal>
      </div>
    </TooltipProvider>
  );
}
