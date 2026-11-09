-- Example fixed role names for a NEW staging database only. Run as a cluster
-- administrator. Passwords/connection strings are supplied through your secret
-- manager separately; do not add them to this file or grant roles to the HTTP user.
-- The schema owner used for table creation/RLS functions must remain separate.
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='openfotoframe_app') THEN
   CREATE ROLE openfotoframe_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='openfotoframe_billing') THEN
   CREATE ROLE openfotoframe_billing LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
 END IF;
END $$;
ALTER ROLE openfotoframe_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
ALTER ROLE openfotoframe_billing NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO openfotoframe_app,openfotoframe_billing;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO openfotoframe_app;
GRANT EXECUTE ON FUNCTION public.release_auth_frame(text),public.release_lock_membership(text),
 public.release_create_household(text,text,text,text),
 public.release_lookup_invitation(text),public.release_invitation_issuer_active(text),
 public.release_invitation_household_name(text),public.release_accept_invitation(text,text),
 public.release_quota_usage(text),public.release_storage_usage(text),public.release_quota_entitlement(text),
 public.release_enroll_frame(text,text,text,text),public.release_control_frame(integer,boolean)
 TO openfotoframe_app;
-- Keep migrations and operational state out of user-facing SQL paths.
REVOKE ALL ON public.legacy_migrations FROM openfotoframe_app;

-- The verified-webhook worker can change only billing state and inspect tenant
-- existence. It cannot read media, identity credentials, grants or device sessions.
GRANT SELECT ON public.households TO openfotoframe_billing;
GRANT EXECUTE ON FUNCTION public.release_lock_billing_household(text) TO openfotoframe_billing;
DROP POLICY IF EXISTS release_billing_service_tenant ON public.households;
CREATE POLICY release_billing_service_tenant ON public.households FOR SELECT TO openfotoframe_billing USING (true);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['billing_checkout_requests','billing_subscriptions','billing_subscription_bindings','billing_events','billing_apple_accounts'] LOOP
   IF to_regclass('public.'||t) IS NOT NULL THEN
     EXECUTE format('GRANT SELECT,INSERT,UPDATE ON public.%I TO openfotoframe_billing',t);
     EXECUTE format('DROP POLICY IF EXISTS release_billing_service ON public.%I',t);
     EXECUTE format('CREATE POLICY release_billing_service ON public.%I TO openfotoframe_billing USING (true) WITH CHECK (true)',t);
   END IF;
 END LOOP;
END $$;
