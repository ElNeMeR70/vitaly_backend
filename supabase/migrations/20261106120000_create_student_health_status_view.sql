-- Vista de apoyo para la búsqueda global de estudiantes (issue #66).
--
-- Mueve a SQL la MISMA clasificación que hoy corre en Python en
-- SchoolAdminService.get_students_by_classroom (app/services/school_admin_service.py:554-565).
-- No cambia umbrales ni etiquetas: 18.5 / 24.9, 'DESNUTRIDO' / 'OPTIMO' / 'SOBREPESO' /
-- 'SIN DATOS'. El objetivo es que el filtro por estado nutricional se resuelva en la base de
-- datos, de modo que la paginación sea real (count exacto + range) y el endpoint del médico
-- no dependa de un .in_() con miles de IDs.
--
-- Decisiones de modelado:
--   * DISTINCT ON (s.id) garantiza una fila por estudiante. Si un estudiante tuviera más de
--     una inscripción vigente, gana la más reciente por cr.id.
--   * LATERAL ... LIMIT 1 replica la defensa de get_student_detail (:685-687), que filtra
--     is_current y ADEMÁS ordena y limita: el código no asume que is_current sea único.
--   * El JOIN con academic_year ancla la búsqueda al año vigente, igual que
--     get_students_resume (:305). Sin él, un buscador global devolvería egresados.
--   * La altura se almacena en centímetros: _calculate_bmi (:90-99) divide entre 100.
--   * No se filtra por is_active; se expone como columna, igual que el listado por salón.

CREATE OR REPLACE VIEW public.student_health_status AS
SELECT DISTINCT ON (s.id)
    s.id                                      AS student_id,
    c.school_id                               AS school_id,
    s.name                                    AS name,
    s.lastname                                AS lastname,
    trim(concat_ws(' ', s.name, s.lastname))  AS full_name,
    s.birthday                                AS birthday,
    s.gender                                  AS gender,
    s.blood_type                              AS blood_type,
    s.identity_number                         AS identity_number,
    s.is_active                               AS is_active,
    c.id                                      AS classroom_id,
    c.category                                AS category,
    c.level                                   AS level,
    c.section                                 AS section,
    c.academic_year_id                        AS academic_year_id,
    m.weight                                  AS weight,
    m.height                                  AS height,
    round(m.bmi::numeric, 2)                  AS bmi,
    CASE
        WHEN m.bmi IS NULL  THEN 'SIN DATOS'
        WHEN m.bmi < 18.5   THEN 'DESNUTRIDO'
        WHEN m.bmi <= 24.9  THEN 'OPTIMO'
        ELSE                     'SOBREPESO'
    END                                       AS bmi_status,
    EXISTS (
        SELECT 1
        FROM public.medical_case mc
        WHERE mc.student_id = s.id
          AND mc.end_date IS NULL
          AND mc.is_deleted = false
    )                                         AS has_active_medical_case
FROM public.student s
JOIN public.classroom_registration cr
    ON cr.student_id = s.id
JOIN public.classroom c
    ON c.id = cr.classroom_id
   AND c.is_deleted = false
JOIN public.academic_year ay
    ON ay.id = c.academic_year_id
   AND ay.is_current = true
   AND ay.is_deleted = false
LEFT JOIN LATERAL (
    SELECT
        sm.weight,
        sm.height,
        CASE
            WHEN sm.weight > 0 AND sm.height > 0
            THEN sm.weight / power(sm.height / 100.0, 2)
        END AS bmi
    FROM public.student_metrics sm
    WHERE sm.student_id = s.id
      AND sm.is_current = true
      AND sm.is_deleted = false
    ORDER BY sm.updated_at DESC
    LIMIT 1
) m ON true
WHERE s.is_deleted = false
ORDER BY s.id, cr.id DESC;

COMMENT ON VIEW public.student_health_status IS
    'Estudiantes del año académico vigente con su estado nutricional por IMC y si tienen un '
    'caso médico abierto. Una fila por estudiante. Replica la clasificación de '
    'SchoolAdminService.get_students_by_classroom; ambas deben moverse juntas.';

-- Índices de apoyo. Aditivos e idempotentes: no alteran datos ni esquema lógico.
CREATE INDEX IF NOT EXISTS idx_classroom_school_id
    ON public.classroom (school_id);

CREATE INDEX IF NOT EXISTS idx_classroom_registration_classroom_id
    ON public.classroom_registration (classroom_id);

CREATE INDEX IF NOT EXISTS idx_classroom_registration_student_id
    ON public.classroom_registration (student_id);

CREATE INDEX IF NOT EXISTS idx_student_metrics_student_current
    ON public.student_metrics (student_id, updated_at DESC)
    WHERE is_current AND NOT is_deleted;

CREATE INDEX IF NOT EXISTS idx_medical_case_student_open
    ON public.medical_case (student_id)
    WHERE end_date IS NULL AND NOT is_deleted;
