import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="state empty">
      <h1>Page not found</h1>
      <p className="muted">That address does not exist.</p>
      <Link to="/" className="btn">
        Back to the dashboard
      </Link>
    </div>
  );
}
