-- RUN ONLY on an explicitly authorized, disposable PostgreSQL 15 database:
-- psql "$ACCOUNT_DELETION_TEST_DSN" -X -v ON_ERROR_STOP=1 -f tests/sql/account_deletion.sql
-- This synthetic schema proves function behavior, not production schema parity.
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
    IF current_setting('server_version_num')::integer < 150000 THEN
        RAISE EXCEPTION 'PostgreSQL 15+ required';
    END IF;
    IF to_regclass('public."user"') IS NOT NULL THEN
        RAISE EXCEPTION 'Refusing an existing application database; use a disposable empty database';
    END IF;
END $$;
CREATE ROLE anon;
CREATE ROLE authenticated;
CREATE ROLE service_role;
CREATE TABLE public."user" (
    id bigint PRIMARY KEY, email text UNIQUE, photo_url text,
    is_active boolean DEFAULT true, is_deleted boolean DEFAULT false
);
CREATE TABLE public.user_roles (user_id bigint REFERENCES public."user", role text);
CREATE TABLE public.school (sch_id bigint PRIMARY KEY);
CREATE TABLE public.school_administrator (
    sch_admin_id bigint PRIMARY KEY, user_id bigint REFERENCES public."user",
    school_id bigint REFERENCES public.school, status integer DEFAULT 2
);
CREATE TABLE public.doctor (doc_id bigint PRIMARY KEY, user_id bigint REFERENCES public."user");
CREATE TABLE public.parent (
    id bigint PRIMARY KEY, user_id bigint REFERENCES public."user",
    is_active boolean DEFAULT true, is_deleted boolean DEFAULT false
);
CREATE TABLE public.doctor_to_school (
    doctor_id bigint REFERENCES public.doctor, school_id bigint REFERENCES public.school, status integer
);
CREATE TABLE public.student_representative (parent_id bigint REFERENCES public.parent, student_id bigint, is_deleted boolean DEFAULT false);
CREATE TABLE public.medical_case (
    id bigint PRIMARY KEY, doctor_id bigint REFERENCES public.doctor, student_id bigint,
    end_date date, "final diagnosis" text, is_deleted boolean DEFAULT false
);
CREATE TABLE public.event (id bigint PRIMARY KEY, init_time timestamptz, is_deleted boolean DEFAULT false, is_canceled boolean DEFAULT false);
CREATE TABLE public.classroom (id bigint PRIMARY KEY, is_deleted boolean DEFAULT false);
CREATE TABLE public.classroom_registration (
    id bigint PRIMARY KEY, student_id bigint, classroom_id bigint REFERENCES public.classroom,
    created_at timestamptz DEFAULT CURRENT_TIMESTAMP, status integer DEFAULT 2
);
CREATE TABLE public.event_participation (
    id_event bigint REFERENCES public.event, id_doctor bigint REFERENCES public.doctor,
    id_school_admin bigint REFERENCES public.school_administrator, is_deleted boolean DEFAULT false,
    id_classroom bigint REFERENCES public.classroom
);
CREATE TABLE public.token_blacklist (id bigserial PRIMARY KEY, token text, expires_at timestamptz);
\ir ../../supabase/migrations/20261106120000_delete_own_account.sql
GRANT USAGE ON SCHEMA public TO service_role;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO service_role;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO service_role;

INSERT INTO public."user" (id, email, photo_url)
SELECT n, n || '@example.test', 'https://example.test/photo.jpg' FROM generate_series(1, 6) n;
INSERT INTO public.user_roles VALUES (1,'doctor'),(2,'representante'),(3,'admin_escuela'),(4,'admin_escuela'),(5,'admin_sistema'),(6,'admin_escuela');
INSERT INTO public.school VALUES (100),(101);
INSERT INTO public.school_administrator VALUES (33,3,100,2),(44,4,100,2),(66,6,101,2);
INSERT INTO public.doctor VALUES (11,1);
INSERT INTO public.parent VALUES (22,2,true,false);
INSERT INTO public.doctor_to_school VALUES (11,100,2),(11,101,2);
INSERT INTO public.student_representative VALUES (22,99,false);

CREATE FUNCTION public.test_snapshot() RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE t text; result jsonb := '{}'; rows jsonb;
BEGIN
    FOREACH t IN ARRAY ARRAY['user','parent','doctor_to_school','student_representative','school_administrator','token_blacklist'] LOOP
        EXECUTE format('SELECT coalesce(jsonb_agg(to_jsonb(x) ORDER BY to_jsonb(x)::text), ''[]''::jsonb) FROM public.%I x', t) INTO rows;
        result := result || jsonb_build_object(t, rows);
    END LOOP;
    RETURN result;
END $$;
CREATE FUNCTION public.test_result(who bigint, expected text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE before_state jsonb := public.test_snapshot(); actual text;
BEGIN
    actual := public.delete_own_account(who, 'test-' || who, 4102444800);
    IF actual IS DISTINCT FROM expected THEN RAISE EXCEPTION 'user %: expected %, got %', who, expected, actual; END IF;
    IF expected <> 'ok' AND public.test_snapshot() IS DISTINCT FROM before_state THEN
        RAISE EXCEPTION 'Guard % wrote data', expected;
    END IF;
END $$;

SAVEPOINT fixtures;
-- Successful calls execute with the actual backend role, not a superuser.
SET LOCAL ROLE service_role;
SELECT public.test_result(1,'ok'), public.test_result(2,'ok'), public.test_result(3,'ok');
RESET ROLE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public."user" WHERE id IN (1,2,3) AND (is_active OR NOT is_deleted OR deleted_at IS NULL))
        OR EXISTS (SELECT 1 FROM public.doctor_to_school WHERE status <> 1)
        OR EXISTS (SELECT 1 FROM public.parent WHERE is_active OR NOT is_deleted)
        OR EXISTS (SELECT 1 FROM public.student_representative WHERE NOT is_deleted)
        OR (SELECT count(*) FROM public.token_blacklist) <> 3
        OR EXISTS (SELECT 1 FROM public."user" WHERE email IS NULL OR photo_url IS NULL)
        OR EXISTS (SELECT 1 FROM public.school_administrator WHERE status <> 2)
    THEN RAISE EXCEPTION 'Successful deletion effects or data retention incorrect'; END IF;
END $$;
SELECT public.test_result(4,'last_school_admin'), public.test_result(1,'not_found');
ROLLBACK TO fixtures;

SELECT public.test_result(6,'last_school_admin'), public.test_result(5,'forbidden'), public.test_result(999,'forbidden');
INSERT INTO public.medical_case VALUES (1,11,99,NULL,NULL,false);
SELECT public.test_result(1,'active_medical_cases'), public.test_result(2,'active_medical_cases');
UPDATE public.medical_case SET end_date = CURRENT_DATE;
SELECT public.test_result(1,'active_medical_cases');
UPDATE public.medical_case SET end_date = NULL, "final diagnosis" = 'Resolved';
SELECT public.test_result(1,'active_medical_cases');
UPDATE public.medical_case SET end_date = CURRENT_DATE;
SELECT public.test_result(1,'ok');
ROLLBACK TO fixtures;
-- Only live parent-child links count; another student's case does not block.
INSERT INTO public.medical_case VALUES (1,11,100,NULL,NULL,false);
SELECT public.test_result(2,'ok');
ROLLBACK TO fixtures;
INSERT INTO public.medical_case VALUES (1,11,99,NULL,NULL,false);
UPDATE public.student_representative SET is_deleted = true;
SELECT public.test_result(2,'ok');
ROLLBACK TO fixtures;
INSERT INTO public.medical_case VALUES (1,11,99,NULL,NULL,true);
SELECT public.test_result(1,'ok');
ROLLBACK TO fixtures;

INSERT INTO public.event VALUES (1,CURRENT_TIMESTAMP + interval '1 day',false,false);
INSERT INTO public.event_participation VALUES (1,11,33,false);
SELECT public.test_result(1,'pending_reviews'), public.test_result(3,'pending_reviews');
UPDATE public.event SET init_time = CURRENT_TIMESTAMP;
SELECT public.test_result(1,'pending_reviews');
UPDATE public.event SET init_time = CURRENT_TIMESTAMP - interval '1 day';
SELECT public.test_result(1,'ok');
ROLLBACK TO fixtures;
INSERT INTO public.event VALUES (1,CURRENT_TIMESTAMP + interval '1 day',true,false);
INSERT INTO public.event_participation VALUES (1,11,33,false);
SELECT public.test_result(1,'ok');
ROLLBACK TO fixtures;
INSERT INTO public.event VALUES (1,CURRENT_TIMESTAMP + interval '1 day',false,true);
INSERT INTO public.event_participation VALUES (1,11,33,false);
SELECT public.test_result(1,'ok');
ROLLBACK TO fixtures;

INSERT INTO public.classroom VALUES (77,false),(88,false);
INSERT INTO public.classroom_registration VALUES (1,99,77,CURRENT_TIMESTAMP,2);
INSERT INTO public.event VALUES (1,CURRENT_TIMESTAMP + interval '1 day',false,false);
INSERT INTO public.event_participation VALUES (1,NULL,33,false,77);
SELECT public.test_result(2,'pending_reviews');
SAVEPOINT parent_review;
UPDATE public.student_representative SET is_deleted = true;
SELECT public.test_result(2,'ok');
ROLLBACK TO parent_review;
UPDATE public.classroom_registration SET student_id = 100;
SELECT public.test_result(2,'ok');
ROLLBACK TO parent_review;
UPDATE public.event SET init_time = CURRENT_TIMESTAMP - interval '1 day';
SELECT public.test_result(2,'ok');
ROLLBACK TO fixtures;
-- A future review in a former classroom must not block the representative.
INSERT INTO public.classroom VALUES (77,false),(88,false);
INSERT INTO public.classroom_registration VALUES (1,99,77,CURRENT_TIMESTAMP - interval '1 day',2),(2,99,88,CURRENT_TIMESTAMP,2);
INSERT INTO public.event VALUES (1,CURRENT_TIMESTAMP + interval '1 day',false,false);
INSERT INTO public.event_participation VALUES (1,NULL,33,false,77);
SELECT public.test_result(2,'ok');
ROLLBACK TO fixtures;

-- Fail after profile changes and prove the entire RPC is rolled back.
ALTER TABLE public.token_blacklist ADD CONSTRAINT forced_failure CHECK (token = 'never');
DO $$ DECLARE before_state jsonb := public.test_snapshot(); BEGIN
    BEGIN
        PERFORM public.delete_own_account(2,'forced-error',4102444800);
        RAISE EXCEPTION 'Expected blacklist constraint failure';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    IF public.test_snapshot() IS DISTINCT FROM before_state THEN RAISE EXCEPTION 'Partial deletion escaped rollback'; END IF;
END $$;
ROLLBACK TO fixtures;

DO $$ BEGIN
    IF has_function_privilege('anon','public.delete_own_account(bigint,text,bigint)','EXECUTE')
        OR has_function_privilege('authenticated','public.delete_own_account(bigint,text,bigint)','EXECUTE')
        OR NOT has_function_privilege('service_role','public.delete_own_account(bigint,text,bigint)','EXECUTE')
    THEN RAISE EXCEPTION 'RPC ACL incorrect'; END IF;
END $$;
COMMIT;
\echo 'Account deletion SQL tests passed; synthetic fixtures retained for concurrency test.'
