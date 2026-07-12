import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import App from "../src/App";

describe("App", () => {
  it("renders the Octave heading", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: /octave/i })).toBeDefined();
  });
});
