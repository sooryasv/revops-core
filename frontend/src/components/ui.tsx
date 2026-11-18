import { Component, useEffect, useId, useRef, type ErrorInfo, type ReactNode } from "react";

import { ApiError } from "../api/client";
import { formatPaise, titleCase } from "../lib/format";

export function Money({ paise, className }: { paise: number; className?: string }) {
  return <span className={`money ${className ?? ""}`}>{formatPaise(paise)}</span>;
}

type Tone = "neutral" | "good" | "warn" | "bad" | "info";

const TONES: Record<string, Tone> = {
  draft: "neutral", issued: "info", void: "neutral",
  unpaid: "warn", partially_paid: "warn", paid: "good",
  captured: "good", authorized: "info", failed: "bad",
  lead: "neutral", qualified: "info", proposal: "info", won: "good", lost: "bad",
  admin: "info", finance: "info", sales: "info",
  active: "good", inactive: "neutral", overdue: "bad",
};

/** Status is always conveyed by text, never colour alone (03 section 10). */
export function StatusChip({ value, label }: { value: string; label?: string }) {
  return <span className={`chip chip-${TONES[value] ?? "neutral"}`}>{label ?? titleCase(value)}</span>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" /> {label}
    </div>
  );
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="state empty">
      <p className="state-title">{title}</p>
      {hint && <p className="muted">{hint}</p>}
      {action}
    </div>
  );
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return "You do not have permission to do that.";
    if (error.status === 404) return "We could not find that record.";
    return error.message;
  }
  return "Something went wrong. Please try again.";
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const requestId = error instanceof ApiError ? error.requestId : null;
  return (
    <div className="state error" role="alert">
      <p className="state-title">{errorMessage(error)}</p>
      {requestId && <p className="muted">Reference: {requestId}</p>}
      {onRetry && (
        <button type="button" className="btn" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

interface QueryLike {
  isPending: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
}

/** Every data view renders loading, error, then content (03 section 10). */
export function QueryBoundary({ query, children }: { query: QueryLike; children: ReactNode }) {
  if (query.isPending) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  return <>{children}</>;
}

export function PageHeader({ title, actions, subtitle }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
      </div>
      <div className="actions">{actions}</div>
    </div>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input, select, textarea, button")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref}>
        <div className="modal-head">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="btn btn-ghost" aria-label="Close dialog" onClick={onClose}>
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Pagination({
  total, limit, offset, onChange,
}: { total: number; limit: number; offset: number; onChange: (offset: number) => void }) {
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <nav className="pagination" aria-label="Pagination">
      <span className="muted">
        {from}–{to} of {total}
      </span>
      <button type="button" className="btn" disabled={offset <= 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        Previous
      </button>
      <button type="button" className="btn" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
        Next
      </button>
    </nav>
  );
}

export function Field({
  label, error, hint, children, htmlFor,
}: { label: string; error?: string; hint?: string; htmlFor: string; children: ReactNode }) {
  return (
    <div className="field">
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && !error && <p className="muted small">{hint}</p>}
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export function FormError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="form-error" role="alert">
      {errorMessage(error)}
      {error instanceof ApiError && error.fieldErrors.length > 0 && (
        <ul>
          {error.fieldErrors.map((f, i) => (
            <li key={i}>
              {f.field ? `${f.field}: ` : ""}
              {f.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface BoundaryState { error: Error | null }

/** One boundary per route so a crash in one page never blanks the shell (03 section 10). */
export class RouteErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, BoundaryState> {
  override state: BoundaryState = { error: null };
  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error };
  }
  override componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("route error", error.message, info.componentStack);
  }
  override componentDidUpdate(prev: { resetKey?: string }) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }
  override render() {
    if (this.state.error) {
      return (
        <div className="state error" role="alert">
          <p className="state-title">This page hit an unexpected problem.</p>
          <button type="button" className="btn" onClick={() => this.setState({ error: null })}>
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
