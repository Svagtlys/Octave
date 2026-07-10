import HealthCheck from "./components/HealthCheck";

export default function App() {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "100vh" }}>
      <h1 style={{ fontSize: "2.5rem", marginBottom: "1rem" }}>Octave</h1>
      <p style={{ color: "#a0a0b0", marginBottom: "2rem" }}>Local-first agent harness</p>
      <HealthCheck />
    </div>
  );
}
