from fastapi import APIRouter
from app.api.v1.endpoints import auth, admin, admin_school, admin_settings, catalogs, school, academic_year, school_admin, school_admin_students, school_admin_classrooms, school_admin_events, school_admin_medical, parent, doctor, doctor_events

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(auth.client_router, prefix="/auth/client", tags=["Autenticación - Clientes"])
api_router.include_router(auth.admin_router, prefix="/auth/admin", tags=["Autenticación - Administración"])
api_router.include_router(admin.router, prefix="/admin", tags=["Administrador de Sistema"])
api_router.include_router(admin_school.router, prefix="/admin/schools", tags=["Administrador de Sistema - Feat. Escuela"])
api_router.include_router(admin_settings.router, prefix="/admin/settings", tags=["Administrador de Sistema - Configuración"])
api_router.include_router(catalogs.router, prefix="/catalogs", tags=["Catálogos Públicos"])
api_router.include_router(school.router, prefix="/schools", tags=["Escuelas Públicas"])
api_router.include_router(academic_year.router, prefix="/academic-year", tags=["Año Académico"])
api_router.include_router(school_admin.router, prefix="/school-admin", tags=["Administrador de Escuela"])
api_router.include_router(school_admin_students.router, prefix="/school-admin", tags=["Administrador de Escuela - Estudiantes"])
api_router.include_router(school_admin_classrooms.router, prefix="/school-admin/classrooms", tags=["Administrador de Escuela - Salones"])
api_router.include_router(school_admin_events.router, prefix="/school-admin/events", tags=["Administrador de Escuela - Eventos"])
api_router.include_router(school_admin_medical.router, prefix="/school-admin", tags=["Administrador de Escuela - Médico"])
api_router.include_router(parent.router, prefix="/parent", tags=["Representante"])
api_router.include_router(doctor.router, prefix="/doctor", tags=["Médico"])
api_router.include_router(doctor_events.router, prefix="/doctor/events", tags=["Médico - Eventos"])

