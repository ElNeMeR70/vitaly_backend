-- GH-59: proposal only; apply after the migration report and human approval.
-- Keep email, identity, photos and historical medical/event records reserved.
ALTER TABLE public."user" ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
COMMENT ON COLUMN public."user".deleted_at IS
    'Internal timestamp of logical account deletion; NULL for existing accounts.';

-- Only the trusted backend can choose p_user_id, from the validated JWT sub.
-- Returning a business rejection performs no writes. Any SQL failure rolls back
-- all profile changes, revocation and deletion in this transaction.
CREATE OR REPLACE FUNCTION public.delete_own_account(
    p_user_id bigint, p_token text, p_expires_at bigint
) RETURNS text
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_role text;
    v_school_id bigint;
    v_admin_id bigint;
    v_doctor_id bigint;
    v_parent_id bigint;
BEGIN
    SELECT r.role::text INTO v_role
    FROM public.user_roles r WHERE r.user_id = p_user_id;
    IF v_role IS NULL OR v_role NOT IN ('admin_escuela', 'doctor', 'representante') THEN
        RETURN 'forbidden';
    END IF;

    IF v_role = 'admin_escuela' THEN
        SELECT a.sch_admin_id, a.school_id INTO v_admin_id, v_school_id
        FROM public.school_administrator a WHERE a.user_id = p_user_id;
        IF v_school_id IS NULL THEN RETURN 'not_found'; END IF;
        -- Lock the school before the user: concurrent deletions from this school
        -- serialize before counting the remaining administrators (READ COMMITTED).
        PERFORM s.sch_id FROM public.school s
        WHERE s.sch_id = v_school_id FOR UPDATE;
        IF NOT FOUND THEN RETURN 'not_found'; END IF;
    END IF;

    PERFORM u.id FROM public."user" u WHERE u.id = p_user_id
        AND u.is_active = true AND u.is_deleted = false FOR UPDATE;
    IF NOT FOUND THEN RETURN 'not_found'; END IF;

    IF v_role = 'admin_escuela' THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.school_administrator a
            JOIN public."user" u ON u.id = a.user_id
            JOIN public.user_roles r ON r.user_id = u.id AND r.role::text = 'admin_escuela'
            WHERE a.school_id = v_school_id AND a.user_id <> p_user_id
                AND u.is_active = true AND u.is_deleted = false
        ) THEN RETURN 'last_school_admin'; END IF;
        -- Historical status catalog is ambiguous for school administrators:
        -- preserve it; account availability is governed by user flags.
    ELSIF v_role = 'doctor' THEN
        SELECT d.doc_id INTO v_doctor_id FROM public.doctor d
        WHERE d.user_id = p_user_id;
        IF v_doctor_id IS NULL THEN RETURN 'not_found'; END IF;
    ELSE
        SELECT p.id INTO v_parent_id FROM public.parent p
        WHERE p.user_id = p_user_id AND p.is_deleted = false;
        IF v_parent_id IS NULL THEN RETURN 'not_found'; END IF;
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.medical_case c
        WHERE c.is_deleted = false AND (c.end_date IS NULL OR c."final diagnosis" IS NULL)
            AND ((v_doctor_id IS NOT NULL AND c.doctor_id = v_doctor_id)
                OR (v_parent_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM public.student_representative sr
                    WHERE sr.parent_id = v_parent_id AND sr.student_id = c.student_id
                        AND sr.is_deleted = false
                )))
    ) THEN RETURN 'active_medical_cases'; END IF;

    -- An event is pending until its scheduled start, including equality.
    -- Participation cancellation is intentionally not a filter, matching the
    -- existing event calendar: cancellation/deletion belong to the event.
    IF EXISTS (
        SELECT 1 FROM public.event_participation ep
        JOIN public.event e ON e.id = ep.id_event
        WHERE ep.is_deleted = false AND e.is_deleted = false AND e.is_canceled = false
            AND e.init_time >= CURRENT_TIMESTAMP
            AND ((v_doctor_id IS NOT NULL AND ep.id_doctor = v_doctor_id)
                OR (v_admin_id IS NOT NULL AND ep.id_school_admin = v_admin_id)
                OR (v_parent_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM public.student_representative sr
                    JOIN LATERAL (
                        -- Match the profile's latest-classroom rule. Registrations
                        -- have status/created_at, not a documented deletion flag.
                        SELECT cr.classroom_id FROM public.classroom_registration cr
                        WHERE cr.student_id = sr.student_id
                        ORDER BY cr.created_at DESC NULLS LAST, cr.classroom_id DESC LIMIT 1
                    ) latest ON latest.classroom_id = ep.id_classroom
                    JOIN public.classroom cl ON cl.id = latest.classroom_id
                        AND cl.is_deleted = false
                    WHERE sr.parent_id = v_parent_id AND sr.is_deleted = false
                )))
    ) THEN RETURN 'pending_reviews'; END IF;

    IF v_role = 'doctor' THEN
        UPDATE public.doctor_to_school SET status = 1 WHERE doctor_id = v_doctor_id;
    ELSIF v_role = 'representante' THEN
        UPDATE public.parent SET is_active = false, is_deleted = true
        WHERE id = v_parent_id AND user_id = p_user_id;
        UPDATE public.student_representative SET is_deleted = true
        WHERE parent_id = v_parent_id;
    END IF;

    INSERT INTO public.token_blacklist(token, expires_at)
    SELECT p_token, to_timestamp(p_expires_at)
    WHERE NOT EXISTS (SELECT 1 FROM public.token_blacklist WHERE token = p_token);
    -- The auth dependency checks these flags on every request, invalidating ALL
    -- sessions; the blacklist additionally records the token used for deletion.
    UPDATE public."user" SET is_active = false, is_deleted = true,
        deleted_at = transaction_timestamp() WHERE id = p_user_id;
    RETURN 'ok';
END;
$$;

REVOKE ALL ON FUNCTION public.delete_own_account(bigint, text, bigint)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.delete_own_account(bigint, text, bigint) TO service_role;
