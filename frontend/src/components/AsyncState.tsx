import type { ReactNode } from "react";

export function PageSkeleton() {
  return <div className="page" aria-label="Inhalte werden geladen"><div className="skeleton skeleton-title" /><div className="grid metrics-grid">{[1,2,3,4].map((item) => <div className="card skeleton-card" key={item}><div className="skeleton line"/><div className="skeleton value"/></div>)}</div></div>;
}

export function ErrorState({ message, retry }: { message: string; retry: () => void }) {
  return <div className="center-state card" role="alert"><div className="state-symbol">!</div><h2>Verbindung nicht verfügbar</h2><p>{message}</p><button className="button primary" onClick={retry}>Erneut versuchen</button></div>;
}

export function EmptyState({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return <div className="empty-state"><div className="state-symbol soft">{icon}</div><h2>{title}</h2><p>{children}</p></div>;
}

