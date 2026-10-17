import type { CSSProperties, ReactNode } from "react";

interface Props { title?: string; aside?: ReactNode; children: ReactNode; flush?: boolean; delay?: number; className?: string; id?: string }

export function Panel({ title, aside, children, flush, delay = 0, className = "", id }: Props) {
  return (
    <section id={id} className={`panel rise ${className}`} style={{ "--delay": `${delay}ms` } as CSSProperties}>
      {(title || aside) && (
        <header className="panel__head">
          <h2 className="panel__title">{title}</h2>
          {aside}
        </header>
      )}
      <div className={flush ? "panel__body panel__body--flush" : "panel__body"}>{children}</div>
    </section>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><strong>{title}</strong>{children}</div>;
}

export function PageHead({ title, sub, aside }: { title: string; sub?: ReactNode; aside?: ReactNode }) {
  return (
    <div className="page-head rise">
      <div><h1>{title}</h1>{sub && <p>{sub}</p>}</div>
      {aside}
    </div>
  );
}
