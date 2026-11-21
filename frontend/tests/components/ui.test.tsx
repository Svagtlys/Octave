import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  Badge,
  Button,
  Checkbox,
  Field,
  Input,
  Modal,
  Select,
  Tooltip,
  TooltipProvider,
} from '../../src/components/ui';

describe('Button', () => {
  it('renders and handles clicks', async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Save</Button>);
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('does not fire clicks while disabled', async () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        Save
      </Button>
    );
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe('Field + Input (elicitation flat-form)', () => {
  it('associates the label with the input', () => {
    render(
      <Field htmlFor="f1" label="Server name">
        <Input id="f1" />
      </Field>
    );
    expect(screen.getByLabelText('Server name')).toBeDefined();
  });

  it('exposes the error via role=alert and aria-invalid', () => {
    render(
      <Field htmlFor="f2" label="Name" error="Required field">
        <Input id="f2" invalid />
      </Field>
    );
    expect(screen.getByRole('alert').textContent).toBe('Required field');
    expect(screen.getByLabelText('Name').getAttribute('aria-invalid')).toBe('true');
  });
});

describe('Checkbox', () => {
  it('toggles on click and via keyboard', async () => {
    const onCheckedChange = vi.fn();
    render(<Checkbox id="c1" label="Agree" checked={false} onCheckedChange={onCheckedChange} />);
    // Click focuses the control in jsdom, so Space activates it directly.
    await userEvent.click(screen.getByRole('checkbox'));
    expect(onCheckedChange).toHaveBeenLastCalledWith(true);
    await userEvent.keyboard(' ');
    expect(onCheckedChange).toHaveBeenCalledTimes(2);
  });
});

describe('Select', () => {
  it('opens with keyboard and selects an option', async () => {
    const onValueChange = vi.fn();
    render(
      <Select
        id="s1"
        placeholder="Pick"
        onValueChange={onValueChange}
        options={[
          { value: 'a', label: 'Apple' },
          { value: 'b', label: 'Banana' },
        ]}
      />
    );
    const trigger = screen.getByRole('combobox');
    await userEvent.click(trigger);
    await userEvent.click(await screen.findByRole('option', { name: 'Banana' }));
    expect(onValueChange).toHaveBeenCalledWith('b');
  });

  it('disables individual options', async () => {
    render(
      <Select
        options={[
          { value: 'a', label: 'Apple' },
          { value: 'd', label: 'Durian', disabled: true },
        ]}
      />
    );
    await userEvent.click(screen.getByRole('combobox'));
    const durian = await screen.findByRole('option', { name: 'Durian' });
    expect(durian.getAttribute('aria-disabled')).toBe('true');
  });
});

describe('Modal', () => {
  it('renders dialog semantics and closes on ESC', async () => {
    const onOpenChange = vi.fn();
    render(
      <Modal open onOpenChange={onOpenChange} title="Confirm">
        <p>Body</p>
      </Modal>
    );
    // Radix Dialog hides content behind the dialog via aria-hidden on siblings
    // (react-remove-scroll) instead of an aria-modal attribute.
    const dialog = screen.getByRole('dialog', { name: 'Confirm' });
    expect(dialog).toBeDefined();
    await userEvent.keyboard('{Escape}');
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('traps focus inside the dialog', async () => {
    render(
      <Modal open onOpenChange={() => {}} title="Trap" footer={<Button>Confirm</Button>}>
        <Input id="modal-input" />
      </Modal>
    );
    const dialog = screen.getByRole('dialog');
    const tabbable = dialog.querySelectorAll('button, input, [tabindex]');
    expect(tabbable.length).toBeGreaterThanOrEqual(2);
    // Cycling Tab should keep focus within the dialog.
    await userEvent.tab();
    await userEvent.tab();
    await userEvent.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
  });
});

describe('Tooltip', () => {
  it('shows content on focus', async () => {
    render(
      <TooltipProvider>
        <Tooltip content="Helpful hint">
          <button>Target</button>
        </Tooltip>
      </TooltipProvider>
    );
    await userEvent.tab();
    expect(await screen.findByRole('tooltip')).toBeDefined();
    expect(screen.getByText('Helpful hint')).toBeDefined();
  });
});

describe('Badge', () => {
  it('renders text content', () => {
    render(<Badge tone="ok">connected</Badge>);
    expect(screen.getByText('connected')).toBeDefined();
  });
});
