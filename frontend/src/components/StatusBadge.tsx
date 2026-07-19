export function StatusBadge({ active, children }: { active: boolean; children: React.ReactNode }) {
  return <span className={`status-badge ${active ? "ready" : "pending"}`}><span className="status-dot" />{children}</span>;
}

