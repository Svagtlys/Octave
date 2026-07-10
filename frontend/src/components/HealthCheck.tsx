import React, { useState, useEffect } from "react";

type HealthState = "loading" | "healthy" | "error";

export default function HealthCheck() {
  const [state, setState] = useState<HealthState>("loading");

  useEffect(() => {
    fetch("/api/health")
      .then((res) => {
        if (res.ok) setState("healthy");
        else setState("error");
      })
      .catch(() => setState("error"));
  }, []);

  const styles = {
    container: {
      padding: "1rem",
      borderRadius: "8px",
      backgroundColor: state === "healthy" ? "#16a34a22" : state === "error" ? "#dc262622" : "#eab30822",
      border: `1px solid ${state === "healthy" ? "#16a34a" : state === "error" ? "#dc2626" : "#eab308"}`,
      maxWidth: "400px",
      margin: "1rem auto",
    } as React.CSSProperties,
    text: {
      color: state === "healthy" ? "#4ade80" : state === "error" ? "#f87171" : "#facc15",
      fontWeight: 600,
    } as React.CSSProperties,
  };

  return (
    <div style={styles.container}>
      <p style={styles.text}>
        {state === "loading" && "Checking backend health..."}
        {state === "healthy" && "Backend: Healthy"}
        {state === "error" && "Backend: Unreachable"}
      </p>
    </div>
  );
}
