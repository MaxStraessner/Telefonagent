import type { ReactNode } from "react";
import { ErrorState, PageSkeleton } from "../components/AsyncState";
import { usePlatformData } from "../api/PlatformDataProvider";
import { ApiError } from "../api/client";

export function DataPage({ children }: { children: ReactNode | ((data: NonNullable<ReturnType<typeof usePlatformData>["data"]>) => ReactNode) }) {
  const state = usePlatformData();
  if (state.loading) return <PageSkeleton />;
  if (state.error || !state.data) return <div className="page"><ErrorState message={state.error ?? "Keine Daten verfügbar."} retry={state.retry} /></div>;
  return <>{typeof children === "function" ? children(state.data) : children}</>;
}

export function PageHeader({ eyebrow, title, description, action }: { eyebrow?: string; title: string; description: string; action?: ReactNode }) {
  return <header className="page-header"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1><p>{description}</p></div>{action}</header>;
}

export const industryLabels: Record<string, string> = { hair_salon: "Friseur & Styling" };

export function accountErrorMessage(cause: unknown, fallback: string): string {
  if (!(cause instanceof ApiError)) return fallback;
  return Object.values(cause.fieldErrors)[0] ?? cause.message;
}

