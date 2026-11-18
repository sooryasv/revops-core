-- Database bootstrap: roles, ownership, schemas and default privileges (07 section 1.3).
-- Run ONCE per database by an administrator (superuser), connected to the target database:
--
--   psql -v ON_ERROR_STOP=1 -d revops \
--        -v dbname=revops -v owner_password=... -v app_password=... -v ai_password=... \
--        -f roles.sql
--
-- Idempotent: safe to run again (it also rotates the three passwords to the values supplied).
-- Roles:
--   revops_owner  runs migrations and owns every object (DDL)
--   revops_app    API runtime: DML on public tables; INSERT and SELECT only on audit_logs (set by migration 0002)
--   ai_reader     agent service: SELECT on schema ai views only; NO access to public

\set ON_ERROR_STOP on

SELECT format('CREATE ROLE revops_owner LOGIN PASSWORD %L', :'owner_password')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'revops_owner') \gexec
SELECT format('CREATE ROLE revops_app LOGIN PASSWORD %L', :'app_password')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'revops_app') \gexec
SELECT format('CREATE ROLE ai_reader LOGIN PASSWORD %L', :'ai_password')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ai_reader') \gexec

SELECT format('ALTER ROLE revops_owner PASSWORD %L', :'owner_password') \gexec
SELECT format('ALTER ROLE revops_app PASSWORD %L', :'app_password') \gexec
SELECT format('ALTER ROLE ai_reader PASSWORD %L', :'ai_password') \gexec

-- The owner owns the database, and therefore (PostgreSQL 15+) the public schema, so migrations can create objects.
SELECT format('ALTER DATABASE %I OWNER TO revops_owner', :'dbname') \gexec

-- Nobody gets anything in public by default; grant explicitly.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO revops_app;

-- Schema for the agent-facing views (created empty here; views arrive in a later migration run by revops_owner).
CREATE SCHEMA IF NOT EXISTS ai AUTHORIZATION revops_owner;
GRANT USAGE ON SCHEMA ai TO ai_reader;

-- Objects created later by revops_owner automatically get the right grants.
ALTER DEFAULT PRIVILEGES FOR ROLE revops_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO revops_app;
ALTER DEFAULT PRIVILEGES FOR ROLE revops_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO revops_app;
ALTER DEFAULT PRIVILEGES FOR ROLE revops_owner IN SCHEMA ai
  GRANT SELECT ON TABLES TO ai_reader;

-- The agent role must never read application tables directly.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ai_reader;
