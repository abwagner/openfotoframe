-- Run as the schema migration owner after metadata.create_all(), never as the HTTP
-- role. The HTTP role must not own these tables/functions or have BYPASSRLS.
-- Values below are transaction-local and set only after server credential checks.
BEGIN;

CREATE OR REPLACE FUNCTION public.release_user_id() RETURNS text
LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.user_id', true), '') $$;
CREATE OR REPLACE FUNCTION public.release_household_id() RETURNS text
LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.household_id', true), '') $$;
CREATE OR REPLACE FUNCTION public.release_frame_id() RETURNS text
LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.frame_id', true), '') $$;

CREATE OR REPLACE FUNCTION public.release_member(hid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT EXISTS (SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
 WHERE m.household_id=hid AND m.user_id=public.release_user_id() AND m.active AND NOT u.disabled)
$$;
CREATE OR REPLACE FUNCTION public.release_admin(hid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT EXISTS (SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
 WHERE m.household_id=hid AND m.user_id=public.release_user_id() AND m.active
 AND m.role IN ('owner','admin') AND NOT u.disabled)
$$;
CREATE OR REPLACE FUNCTION public.release_owner(hid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT EXISTS (SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
 WHERE m.household_id=hid AND m.user_id=public.release_user_id() AND m.active
 AND m.role='owner' AND NOT u.disabled)
$$;
CREATE OR REPLACE FUNCTION public.release_scope(hid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT hid=public.release_household_id() AND (public.release_member(hid) OR EXISTS
 (SELECT 1 FROM public.frames f WHERE f.id=public.release_frame_id() AND f.household_id=hid))
$$;
CREATE OR REPLACE FUNCTION public.release_collection(hid text, cid text, operation text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT public.release_scope(hid) AND (public.release_admin(hid) OR EXISTS
 (SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
 JOIN public.collection_grants g ON g.membership_id=m.id AND g.household_id=m.household_id
 WHERE m.household_id=hid AND m.user_id=public.release_user_id() AND m.active AND NOT u.disabled
 AND g.collection_id=cid AND (operation='read' OR (operation='upload' AND g.role IN ('contributor','editor'))
 OR (operation='write' AND g.role='editor'))) OR (operation='read' AND EXISTS
 (SELECT 1 FROM public.frame_collections fc WHERE fc.household_id=hid
 AND fc.frame_id=public.release_frame_id() AND fc.collection_id=cid)))
$$;
CREATE OR REPLACE FUNCTION public.release_frame(hid text, fid text, operation text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT public.release_scope(hid) AND (public.release_admin(hid) OR EXISTS
 (SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
 JOIN public.frame_grants g ON g.membership_id=m.id AND g.household_id=m.household_id
 WHERE m.household_id=hid AND m.user_id=public.release_user_id() AND m.active AND NOT u.disabled
 AND g.frame_id=fid AND (operation='read' OR g.role='controller'))
 OR (operation='read' AND fid=public.release_frame_id()))
$$;

-- The only credential lookup that must precede frame/household scope. Returning a
-- hash-matched, non-revoked row is not itself authority: the application sets scope
-- from this row and every following media request uses collection policies.
CREATE OR REPLACE FUNCTION public.release_auth_frame(credential_hash text)
RETURNS SETOF public.frame_credentials
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT * FROM public.frame_credentials WHERE token_hash=credential_hash AND NOT revoked
$$;

CREATE OR REPLACE FUNCTION public.release_lock_membership(hid text)
RETURNS SETOF public.memberships
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT m.* FROM public.memberships m JOIN public.users u ON u.id=m.user_id
 WHERE hid=public.release_household_id() AND m.household_id=hid
 AND m.user_id=public.release_user_id() AND m.active AND NOT u.disabled
 FOR UPDATE OF m
$$;

CREATE OR REPLACE FUNCTION public.release_create_household(hid text, household_name text,
 membership_id text, collection_id text) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE actor text := public.release_user_id();
BEGIN
 IF actor IS NULL OR NOT EXISTS (SELECT 1 FROM public.users WHERE id=actor AND NOT disabled AND NOT must_change_password)
    OR length(hid)>64 OR length(membership_id)>64 OR length(collection_id)>64
    OR length(trim(household_name)) NOT BETWEEN 1 AND 200 THEN
   RAISE EXCEPTION 'invalid household creation';
 END IF;
 INSERT INTO public.households(id,name,settings,created_at)
 VALUES(hid,trim(household_name),'{}'::json,extract(epoch from clock_timestamp())::bigint);
 INSERT INTO public.memberships(id,household_id,user_id,role,active)
 VALUES(membership_id,hid,actor,'owner',true);
 INSERT INTO public.collections(id,household_id,name,settings)
 VALUES(collection_id,hid,'Photos','{}'::json);
 RETURN hid;
END $$;

CREATE OR REPLACE FUNCTION public.release_invitation_issuer_active(secret_hash text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT EXISTS (SELECT 1 FROM public.invitations i JOIN public.memberships m
 ON m.household_id=i.household_id AND m.user_id=i.issuer_id JOIN public.users u ON u.id=m.user_id
 WHERE i.token_hash=secret_hash AND m.active AND m.role IN ('owner','admin') AND NOT u.disabled)
$$;
CREATE OR REPLACE FUNCTION public.release_invitation_household_name(secret_hash text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT h.name FROM public.households h JOIN public.invitations i ON i.household_id=h.id
 WHERE i.token_hash=secret_hash AND NOT i.revoked AND public.release_invitation_issuer_active(secret_hash)
 AND (i.expires_at>extract(epoch from clock_timestamp()) OR i.accepted_by=public.release_user_id())
$$;
CREATE OR REPLACE FUNCTION public.release_lookup_invitation(secret_hash text)
RETURNS SETOF public.invitations
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT * FROM public.invitations i WHERE i.token_hash=secret_hash AND NOT i.revoked
 AND public.release_invitation_issuer_active(secret_hash)
 AND (i.expires_at>extract(epoch from clock_timestamp()) OR i.accepted_by=public.release_user_id())
$$;
CREATE OR REPLACE FUNCTION public.release_accept_invitation(secret_hash text, accepting_user text)
RETURNS text LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE inv public.invitations%ROWTYPE; person public.users%ROWTYPE;
 member public.memberships%ROWTYPE; member_id text; grant_record jsonb;
BEGIN
 IF accepting_user IS DISTINCT FROM public.release_user_id() OR accepting_user IS NULL THEN
   RAISE EXCEPTION 'invalid invitation';
 END IF;
 SELECT * INTO person FROM public.users WHERE id=accepting_user AND NOT disabled AND NOT must_change_password;
 IF NOT FOUND THEN RAISE EXCEPTION 'invalid invitation'; END IF;
 SELECT * INTO inv FROM public.invitations WHERE token_hash=secret_hash FOR UPDATE;
 IF NOT FOUND OR inv.revoked OR NOT public.release_invitation_issuer_active(secret_hash) THEN
   RAISE EXCEPTION 'invalid invitation';
 END IF;
 IF inv.accepted_by IS NOT NULL THEN
   IF inv.accepted_by=accepting_user AND EXISTS (SELECT 1 FROM public.memberships
     WHERE household_id=inv.household_id AND user_id=accepting_user AND active) THEN
     RETURN inv.household_id;
   END IF;
   RAISE EXCEPTION 'invalid invitation';
 END IF;
 IF inv.expires_at<=extract(epoch from clock_timestamp()) THEN RAISE EXCEPTION 'invalid invitation'; END IF;
 IF inv.recipient_email IS NOT NULL THEN
   IF NOT person.email_verified OR lower(inv.recipient_email)<>lower(person.email) THEN RAISE EXCEPTION 'invalid invitation'; END IF;
 ELSIF NOT inv.transferable THEN RAISE EXCEPTION 'invalid invitation';
 END IF;
 IF jsonb_typeof(inv.grants::jsonb->'collections') IS DISTINCT FROM 'array'
 OR jsonb_typeof(inv.grants::jsonb->'frames') IS DISTINCT FROM 'array'
 OR jsonb_array_length(inv.grants::jsonb->'collections')>100
 OR jsonb_array_length(inv.grants::jsonb->'frames')>100 THEN RAISE EXCEPTION 'invalid invitation'; END IF;
 FOR grant_record IN SELECT value FROM jsonb_array_elements(inv.grants::jsonb->'collections') LOOP
   IF NOT EXISTS (SELECT 1 FROM public.collections WHERE id=grant_record->>'id' AND household_id=inv.household_id)
   OR grant_record->>'role' NOT IN ('viewer','contributor','editor') THEN RAISE EXCEPTION 'invalid invitation'; END IF;
 END LOOP;
 FOR grant_record IN SELECT value FROM jsonb_array_elements(inv.grants::jsonb->'frames') LOOP
   IF NOT EXISTS (SELECT 1 FROM public.frames WHERE id=grant_record->>'id' AND household_id=inv.household_id)
   OR grant_record->>'role' NOT IN ('viewer','controller') THEN RAISE EXCEPTION 'invalid invitation'; END IF;
 END LOOP;
 SELECT * INTO member FROM public.memberships WHERE household_id=inv.household_id AND user_id=accepting_user FOR UPDATE;
 IF FOUND THEN
   member_id := member.id;
   IF NOT member.active THEN
     -- Reactivation is explicit but must not resurrect historical unselected grants.
     DELETE FROM public.collection_grants WHERE membership_id=member_id;
     DELETE FROM public.frame_grants WHERE membership_id=member_id;
     UPDATE public.memberships SET active=true,role='member' WHERE id=member_id;
   END IF;
 ELSE
   member_id := md5(inv.id||':'||accepting_user);
   INSERT INTO public.memberships(id,household_id,user_id,role,active)
   VALUES(member_id,inv.household_id,accepting_user,'member',true);
 END IF;
 FOR grant_record IN SELECT value FROM jsonb_array_elements(inv.grants::jsonb->'collections') LOOP
   INSERT INTO public.collection_grants(id,household_id,membership_id,collection_id,role)
   VALUES(md5(inv.id||':collection:'||(grant_record->>'id')||':'||accepting_user),inv.household_id,member_id,
    grant_record->>'id',grant_record->>'role')
   ON CONFLICT(membership_id,collection_id) DO UPDATE SET role=CASE
     WHEN collection_grants.role='editor' OR excluded.role='editor' THEN 'editor'
     WHEN collection_grants.role='contributor' OR excluded.role='contributor' THEN 'contributor'
     ELSE 'viewer' END;
 END LOOP;
 FOR grant_record IN SELECT value FROM jsonb_array_elements(inv.grants::jsonb->'frames') LOOP
   INSERT INTO public.frame_grants(id,household_id,membership_id,frame_id,role)
   VALUES(md5(inv.id||':frame:'||(grant_record->>'id')||':'||accepting_user),inv.household_id,member_id,
    grant_record->>'id',grant_record->>'role')
   ON CONFLICT(membership_id,frame_id) DO UPDATE SET role=CASE
     WHEN frame_grants.role='controller' OR excluded.role='controller' THEN 'controller' ELSE 'viewer' END;
 END LOOP;
 UPDATE public.invitations SET accepted_by=accepting_user WHERE id=inv.id;
 RETURN inv.household_id;
END $$;

-- Internal quota computation must include ungranted collections and soft-deleted
-- retained bytes. Do not expose this aggregate through a member-facing HTTP API.
CREATE OR REPLACE FUNCTION public.release_quota_usage(hid text)
RETURNS TABLE(total_bytes bigint,active_assets bigint,frame_count bigint)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
 IF hid IS DISTINCT FROM public.release_household_id() OR NOT public.release_member(hid) THEN
   RAISE EXCEPTION 'invalid quota scope';
 END IF;
 PERFORM id FROM public.households WHERE id=hid FOR UPDATE;
 RETURN QUERY SELECT coalesce(sum(a.byte_size),0)::bigint,
   count(*) FILTER (WHERE NOT a.deleted)::bigint,
   (SELECT count(*) FROM public.frames f WHERE f.household_id=hid)::bigint
   FROM public.assets a WHERE a.household_id=hid;
END $$;
CREATE OR REPLACE FUNCTION public.release_quota_entitlement(hid text)
RETURNS SETOF public.billing_subscriptions
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT * FROM public.billing_subscriptions WHERE household_id=hid
 AND hid=public.release_household_id() AND public.release_member(hid)
$$;
CREATE OR REPLACE FUNCTION public.release_lock_billing_household(hid text)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
 -- EXECUTE is granted only to the bounded verified-event billing role. That role
 -- has no UPDATE privilege on household settings/content merely to reserve a row.
 PERFORM id FROM public.households WHERE id=hid FOR UPDATE;
 RETURN FOUND;
END $$;

ALTER TABLE public.households ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS release_read ON public.households;
CREATE POLICY release_read ON public.households FOR SELECT USING
 (public.release_member(id) OR (id=public.release_household_id() AND public.release_frame_id() IS NOT NULL));
DROP POLICY IF EXISTS release_write ON public.households;
CREATE POLICY release_write ON public.households FOR UPDATE USING
 (id=public.release_household_id() AND public.release_admin(id)) WITH CHECK (public.release_admin(id));
-- New household creation is an explicitly scoped service operation. The HTTP role
-- has no permissive INSERT/DELETE policy on tenant roots.

ALTER TABLE public.memberships ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS release_read ON public.memberships;
CREATE POLICY release_read ON public.memberships FOR SELECT USING
 (user_id=public.release_user_id() OR (public.release_scope(household_id) AND public.release_admin(household_id)));
DROP POLICY IF EXISTS release_write ON public.memberships;
CREATE POLICY release_write ON public.memberships FOR ALL USING
 (public.release_scope(household_id) AND public.release_admin(household_id))
 WITH CHECK (public.release_scope(household_id) AND public.release_admin(household_id));

ALTER TABLE public.collections ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS release_read ON public.collections;
CREATE POLICY release_read ON public.collections FOR SELECT USING (public.release_collection(household_id,id,'read'));
DROP POLICY IF EXISTS release_write ON public.collections;
CREATE POLICY release_write ON public.collections FOR ALL USING
 (public.release_scope(household_id) AND public.release_admin(household_id))
 WITH CHECK (public.release_scope(household_id) AND public.release_admin(household_id));
DROP POLICY IF EXISTS release_editor_settings ON public.collections;
CREATE POLICY release_editor_settings ON public.collections FOR UPDATE USING
 (public.release_collection(household_id,id,'write'))
 WITH CHECK (public.release_collection(household_id,id,'write'));

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['collection_grants','frame_grants','invitations','frame_credentials','audit_events'] LOOP
   EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
   EXECUTE format('DROP POLICY IF EXISTS release_scoped ON public.%I',t);
   EXECUTE format('CREATE POLICY release_scoped ON public.%I USING (public.release_scope(household_id) AND public.release_admin(household_id)) WITH CHECK (public.release_scope(household_id) AND public.release_admin(household_id))',t);
 END LOOP;
END $$;
-- Members must be able to read their own current grants after membership lookup.
DROP POLICY IF EXISTS release_own_grants ON public.collection_grants;
CREATE POLICY release_own_grants ON public.collection_grants FOR SELECT USING
 (public.release_scope(household_id) AND EXISTS (SELECT 1 FROM public.memberships m
 WHERE m.id=membership_id AND m.household_id=collection_grants.household_id AND m.user_id=public.release_user_id() AND m.active));
DROP POLICY IF EXISTS release_own_grants ON public.frame_grants;
CREATE POLICY release_own_grants ON public.frame_grants FOR SELECT USING
 (public.release_scope(household_id) AND EXISTS (SELECT 1 FROM public.memberships m
 WHERE m.id=membership_id AND m.household_id=frame_grants.household_id AND m.user_id=public.release_user_id() AND m.active));
DROP POLICY IF EXISTS release_actor_audit ON public.audit_events;
CREATE POLICY release_actor_audit ON public.audit_events FOR INSERT WITH CHECK
 (public.release_scope(household_id) AND actor_id=public.release_user_id());

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['assets','groups','group_assets'] LOOP
   EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
   EXECUTE format('DROP POLICY IF EXISTS release_read ON public.%I',t);
   EXECUTE format('CREATE POLICY release_read ON public.%I FOR SELECT USING (public.release_collection(household_id,collection_id,''read''))',t);
   EXECUTE format('DROP POLICY IF EXISTS release_write ON public.%I',t);
   EXECUTE format('CREATE POLICY release_write ON public.%I FOR ALL USING (public.release_collection(household_id,collection_id,''write'')) WITH CHECK (public.release_collection(household_id,collection_id,''write''))',t);
 END LOOP;
END $$;
DROP POLICY IF EXISTS release_upload ON public.assets;
CREATE POLICY release_upload ON public.assets FOR INSERT WITH CHECK
 (public.release_collection(household_id,collection_id,'upload') AND uploaded_by=public.release_user_id());
DROP POLICY IF EXISTS release_own_photo_update ON public.assets;
CREATE POLICY release_own_photo_update ON public.assets FOR UPDATE USING
 (uploaded_by=public.release_user_id() AND public.release_collection(household_id,collection_id,'upload'))
 WITH CHECK (uploaded_by=public.release_user_id() AND public.release_collection(household_id,collection_id,'upload'));
DROP POLICY IF EXISTS release_remove_own_photo_group ON public.group_assets;
CREATE POLICY release_remove_own_photo_group ON public.group_assets FOR DELETE USING
 (public.release_collection(household_id,collection_id,'upload') AND EXISTS
 (SELECT 1 FROM public.assets a WHERE a.id=asset_id AND a.household_id=group_assets.household_id
 AND a.collection_id=group_assets.collection_id AND a.uploaded_by=public.release_user_id()));

ALTER TABLE public.frames ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS release_read ON public.frames;
CREATE POLICY release_read ON public.frames FOR SELECT USING (public.release_frame(household_id,id,'read'));
DROP POLICY IF EXISTS release_control ON public.frames;
CREATE POLICY release_control ON public.frames FOR UPDATE USING (public.release_frame(household_id,id,'control'))
 WITH CHECK (public.release_frame(household_id,id,'control'));
DROP POLICY IF EXISTS release_manage ON public.frames;
CREATE POLICY release_manage ON public.frames FOR ALL USING
 (public.release_scope(household_id) AND public.release_admin(household_id))
 WITH CHECK (public.release_scope(household_id) AND public.release_admin(household_id));

ALTER TABLE public.frame_collections ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS release_read ON public.frame_collections;
CREATE POLICY release_read ON public.frame_collections FOR SELECT USING (public.release_frame(household_id,frame_id,'read'));
DROP POLICY IF EXISTS release_manage ON public.frame_collections;
CREATE POLICY release_manage ON public.frame_collections FOR ALL USING
 (public.release_scope(household_id) AND public.release_admin(household_id))
 WITH CHECK (public.release_scope(household_id) AND public.release_admin(household_id));

ALTER TABLE public.jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS release_own_job ON public.jobs;
CREATE POLICY release_own_job ON public.jobs USING
 (requested_by=public.release_user_id() AND public.release_collection(household_id,collection_id,'read'))
 WITH CHECK (requested_by=public.release_user_id() AND public.release_collection(household_id,collection_id,'read'));

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['billing_checkout_requests','billing_subscriptions','billing_subscription_bindings','billing_events','billing_apple_accounts'] LOOP
   IF to_regclass('public.'||t) IS NOT NULL THEN
     EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
     EXECUTE format('DROP POLICY IF EXISTS release_billing_owner ON public.%I',t);
     EXECUTE format('CREATE POLICY release_billing_owner ON public.%I USING (public.release_scope(household_id) AND public.release_owner(household_id)) WITH CHECK (public.release_scope(household_id) AND public.release_owner(household_id))',t);
   END IF;
 END LOOP;
END $$;

-- Do not give PUBLIC the privileged frame lookup. Grant EXECUTE only to the
-- intended HTTP application role in the separately reviewed role setup step.
REVOKE ALL ON FUNCTION public.release_auth_frame(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.release_lock_membership(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.release_create_household(text,text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.release_lookup_invitation(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.release_invitation_issuer_active(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.release_invitation_household_name(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.release_accept_invitation(text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.release_quota_usage(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.release_quota_entitlement(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.release_lock_billing_household(text) FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- One-use frame enrollment derives all authority from an issued hashed challenge.
CREATE OR REPLACE FUNCTION public.release_enroll_frame(enrollment_hash text,
 credential_id text, credential_hash text, csrf_hash_value text)
RETURNS TABLE(household_id text,frame_id text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE pending public.auth_challenges%ROWTYPE; hid text; fid text;
BEGIN
 SELECT * INTO pending FROM public.auth_challenges
 WHERE token_hash=enrollment_hash AND purpose='frame_enrollment' FOR UPDATE;
 IF NOT FOUND OR pending.consumed OR pending.expires_at<=extract(epoch from clock_timestamp()) THEN
   RAISE EXCEPTION 'invalid frame enrollment';
 END IF;
 hid:=pending.data->>'household_id';fid:=pending.data->>'frame_id';
 IF NOT EXISTS(SELECT 1 FROM public.memberships member JOIN public.users person ON person.id=member.user_id
 WHERE member.household_id=hid AND member.user_id=pending.user_id AND member.active
 AND member.role IN ('owner','admin') AND NOT person.disabled)
 OR NOT EXISTS(SELECT 1 FROM public.frames target WHERE target.id=fid AND target.household_id=hid)
 OR length(credential_id)>64 OR length(credential_hash)<>64 OR length(csrf_hash_value)<>64 THEN
   RAISE EXCEPTION 'invalid frame enrollment';
 END IF;
 UPDATE public.auth_challenges SET consumed=true WHERE id=pending.id;
 UPDATE public.frame_credentials SET revoked=true WHERE public.frame_credentials.household_id=hid AND public.frame_credentials.frame_id=fid;
 INSERT INTO public.frame_credentials(id,household_id,frame_id,token_hash,csrf_hash,revoked)
 VALUES(credential_id,hid,fid,credential_hash,csrf_hash_value,false);
 RETURN QUERY SELECT hid,fid;
END $$;

CREATE OR REPLACE FUNCTION public.release_control_frame(new_index integer,new_paused boolean)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE hid text:=public.release_household_id();fid text:=public.release_frame_id();
BEGIN
 IF hid IS NULL OR fid IS NULL OR new_index NOT BETWEEN 0 AND 100000
 OR NOT EXISTS(SELECT 1 FROM public.frame_credentials c WHERE c.household_id=hid AND c.frame_id=fid AND NOT c.revoked) THEN
   RAISE EXCEPTION 'invalid frame control';
 END IF;
 UPDATE public.frames SET state=jsonb_set(jsonb_set(state::jsonb,'{index}',to_jsonb(new_index)),'{paused}',to_jsonb(new_paused))::json
 WHERE id=fid AND household_id=hid;
 RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION public.release_enroll_frame(text,text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.release_control_frame(integer,boolean) FROM PUBLIC;
COMMIT;
