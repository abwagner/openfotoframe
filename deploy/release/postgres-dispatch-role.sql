-- Optional worker dispatcher identity; execute only after migration 002 as owner.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='openfotoframe_dispatch') THEN
 CREATE ROLE openfotoframe_dispatch LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
 END IF;
END $$;
ALTER ROLE openfotoframe_dispatch NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM openfotoframe_dispatch;
GRANT USAGE ON SCHEMA public TO openfotoframe_dispatch;
GRANT EXECUTE ON FUNCTION public.release_claim_work(text),public.release_finish_work(text,text,text,boolean) TO openfotoframe_dispatch;
