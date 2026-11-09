-- Version 002: apply offline as schema owner, after metadata and RLS setup.
-- Additive/idempotent, never invoked by an HTTP startup or worker process.
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS dispatch_attempts integer NOT NULL DEFAULT 0;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS dispatch_token varchar(64);
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS dispatch_until integer NOT NULL DEFAULT 0;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS dispatch_next_at integer NOT NULL DEFAULT 0;
ALTER TABLE public.media_purges ADD COLUMN IF NOT EXISTS dispatch_attempts integer NOT NULL DEFAULT 0;
ALTER TABLE public.media_purges ADD COLUMN IF NOT EXISTS dispatch_token varchar(64);
ALTER TABLE public.media_purges ADD COLUMN IF NOT EXISTS dispatch_until integer NOT NULL DEFAULT 0;
ALTER TABLE public.media_purges ADD COLUMN IF NOT EXISTS dispatch_next_at integer NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS jobs_dispatch_queue ON public.jobs(dispatch_next_at,created_at) WHERE state='queued';
CREATE INDEX IF NOT EXISTS jobs_dispatch_expiry ON public.jobs(created_at);
CREATE INDEX IF NOT EXISTS media_purges_dispatch_queue ON public.media_purges(dispatch_next_at,created_at) WHERE state='queued';
CREATE OR REPLACE FUNCTION public.release_claim_work(p_token text)
RETURNS TABLE(kind text,id text,household_id text,requested_by text,action text,lease_token text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE r record; ts integer:=extract(epoch from clock_timestamp())::integer;
BEGIN
 IF p_token IS NULL OR p_token !~ '^[a-f0-9]{64}$' THEN RAISE EXCEPTION 'Invalid dispatch token'; END IF;
 -- Lock at most one indexed candidate per queue, then reserve only the oldest.
 -- SKIP LOCKED lets concurrent workers make progress during another job's IO.
 WITH export_candidate AS MATERIALIZED (
 SELECT q.id,q.household_id,q.requested_by,q.created_at,q.dispatch_attempts,'export'::text AS kind
 FROM public.jobs q WHERE q.kind='collection_export' AND q.dispatch_until<=ts
 AND q.dispatch_next_at<=ts AND (q.created_at<=ts-86400 OR q.state='queued')
 ORDER BY q.created_at,q.id FOR UPDATE SKIP LOCKED LIMIT 1),
 purge_candidate AS MATERIALIZED (
 SELECT q.id,q.household_id,q.requested_by,q.created_at,q.dispatch_attempts,'media_purge'::text AS kind
 FROM public.media_purges q WHERE q.state='queued' AND q.dispatch_until<=ts AND q.dispatch_next_at<=ts
 ORDER BY q.created_at,q.id FOR UPDATE SKIP LOCKED LIMIT 1)
 SELECT * INTO r FROM (SELECT * FROM export_candidate UNION ALL SELECT * FROM purge_candidate) q
 ORDER BY q.created_at,q.kind,q.id LIMIT 1;
 IF NOT FOUND THEN RETURN; END IF;
 IF r.kind='export' THEN
 UPDATE public.jobs q SET dispatch_token=p_token,dispatch_until=ts+600,
 dispatch_attempts=least(q.dispatch_attempts+1,1000) WHERE q.id=r.id;
 ELSE
 UPDATE public.media_purges q SET dispatch_token=p_token,dispatch_until=ts+600,
 dispatch_attempts=least(q.dispatch_attempts+1,1000) WHERE q.id=r.id;
 END IF;
 RETURN QUERY SELECT r.kind::text,r.id::text,r.household_id::text,r.requested_by::text,
 CASE WHEN r.kind='export' AND r.created_at<=ts-86400 THEN 'cleanup'
 WHEN r.dispatch_attempts>=3 THEN 'exhausted' ELSE r.kind END,p_token;
END $$;
CREATE OR REPLACE FUNCTION public.release_finish_work(p_kind text,p_id text,p_token text,p_success boolean)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE r record; ts integer:=extract(epoch from clock_timestamp())::integer; delay integer;
BEGIN
 IF p_kind NOT IN ('export','media_purge') THEN RETURN false; END IF;
 IF p_kind='export' THEN
 SELECT * INTO r FROM public.jobs q WHERE q.id=p_id AND q.dispatch_token=p_token FOR UPDATE;
 ELSE
 SELECT * INTO r FROM public.media_purges q WHERE q.id=p_id AND q.dispatch_token=p_token FOR UPDATE;
 END IF;
 IF NOT FOUND THEN RETURN false; END IF;
 delay:=least(3600,30*power(2,least(r.dispatch_attempts,7))::integer);
 IF p_kind='export' THEN
   IF r.created_at<=ts-86400 THEN
     IF p_success THEN DELETE FROM public.jobs q WHERE q.id=r.id; RETURN true; END IF;
   ELSIF r.state='queued' AND r.dispatch_attempts>=3 THEN
     UPDATE public.jobs q SET state='failed',error_code='worker_attempts_exhausted',completed_at=ts WHERE q.id=r.id;
   ELSIF r.state='failed' AND r.error_code='export_failed' AND r.dispatch_attempts<3 THEN
     UPDATE public.jobs q SET state='queued',error_code=NULL,completed_at=NULL WHERE q.id=r.id;
   END IF;
   UPDATE public.jobs q SET dispatch_token=NULL,dispatch_until=0,dispatch_next_at=ts+delay WHERE q.id=r.id;
 ELSE
   IF r.state='queued' AND r.dispatch_attempts>=3 THEN
     UPDATE public.media_purges q SET state='failed',error_code='worker_attempts_exhausted',completed_at=ts WHERE q.id=r.id;
   END IF;
   UPDATE public.media_purges q SET dispatch_token=NULL,dispatch_until=0,dispatch_next_at=ts+delay WHERE q.id=r.id;
 END IF;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.release_claim_work(text),public.release_finish_work(text,text,text,boolean) FROM PUBLIC;
