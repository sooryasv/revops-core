import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { z } from "zod";

import { useAuth } from "../auth/AuthContext";
import { ApiError } from "../api/client";
import { Field, Loading } from "../components/ui";

const schema = z.object({
  email: z.string().min(1, "Enter your email").email("Enter a valid email"),
  password: z.string().min(1, "Enter your password"),
});
type Values = z.infer<typeof schema>;

export function LoginPage() {
  const { me, loading, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [problem, setProblem] = useState<string | null>(null);
  const {
    register, handleSubmit, formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema) });

  if (loading) return <Loading />;
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  if (me) return <Navigate to={from} replace />;

  const onSubmit = handleSubmit(async (values) => {
    setProblem(null);
    try {
      await login(values.email, values.password);
      navigate(from, { replace: true });
    } catch (e) {
      setProblem(
        e instanceof ApiError && e.status === 429
          ? "Too many attempts. Please wait a minute and try again."
          : e instanceof ApiError && e.status === 401
            ? "Email or password is incorrect."
            : "We could not sign you in. Please try again.",
      );
    }
  });

  return (
    <div className="auth-page">
      <form className="card auth-card" onSubmit={onSubmit} noValidate>
        <h1>Sign in to RevOps Core</h1>
        {problem && (
          <div className="form-error" role="alert">
            {problem}
          </div>
        )}
        <Field label="Email" htmlFor="email" error={errors.email?.message}>
          <input id="email" type="email" autoComplete="username" {...register("email")} />
        </Field>
        <Field label="Password" htmlFor="password" error={errors.password?.message}>
          <input id="password" type="password" autoComplete="current-password" {...register("password")} />
        </Field>
        <button type="submit" className="btn btn-primary" disabled={isSubmitting}>
          {isSubmitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
