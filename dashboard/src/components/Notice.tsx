export function Notice({ error, done }: { error?: string; done?: string }) {
  if (!error && !done) return null;
  return <div className={`notice notice--${error ? "error" : "done"}`} role={error ? "alert" : "status"}>{error ?? done}</div>;
}
