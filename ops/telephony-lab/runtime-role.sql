-- Synthetic disposable local lab only. Never execute against a user database.
CREATE ROLE telefonagent_lab_runtime LOGIN PASSWORD 'synthetic-local-lab-only'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
GRANT CONNECT ON DATABASE telefonagent_auth_test TO telefonagent_lab_runtime;
GRANT USAGE ON SCHEMA public TO telefonagent_lab_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO telefonagent_lab_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO telefonagent_lab_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT EXECUTE ON FUNCTIONS TO telefonagent_lab_runtime;
