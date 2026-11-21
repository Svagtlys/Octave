/**
 * jsdom polyfills for Radix UI primitives.
 *
 * jsdom doesn't implement pointer capture (used by Radix Select/Dialog on
 * pointer events) or window.scrollTo (called by focus management). Both
 * throw inside Radix handlers, so they're stubbed here.
 * See https://github.com/radix-ui/primitives/issues/1822
 */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (typeof window !== 'undefined') {
  // jsdom lacks ResizeObserver; Radix Tooltip/Select positioning uses it.
  globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
  // jsdom lacks these Element methods; Radix feature-detects via usage.
  const proto = Element.prototype as Element & {
    hasPointerCapture?: (id: number) => boolean;
    setPointerCapture?: (id: number) => void;
    releasePointerCapture?: (id: number) => void;
  };
  proto.hasPointerCapture ??= () => false;
  proto.setPointerCapture ??= () => {};
  proto.releasePointerCapture ??= () => {};

  window.scrollTo ??= (() => {}) as Window['scrollTo'];
  window.HTMLElement.prototype.scrollIntoView ??= () => {};
}
