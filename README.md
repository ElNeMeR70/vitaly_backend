<p align="center">
  <img src="app/static/logo_full.png" alt="Vitaly Logo" width="400" style="background-color: white; border-radius: 12px; padding: 20px;">
</p>

# 🏥 Vitaly Backend API

![Versión](https://img.shields.io/badge/version-0.4.0-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-009688?logo=fastapi)
![Supabase](https://img.shields.io/badge/Supabase-Database-3ECF8E?logo=supabase)

> **"Nuestra prioridad es brindar seguridad, empatía y cariño a las familias, garantizando una atención médica de alta calidad para sus hijos."**

## ✨ Sobre Vitaly

**Vitaly** nace de la palabra "vitalidad", que describe la capacidad de una persona para vivir, crecer y desarrollarse. Representa la capacidad de adaptarse y prosperar en cualquier situación, con el propósito de ayudar a las personas a vivir una vida plena y saludable.

### 🎯 Nuestro Enfoque
Vitaly es una plataforma integral diseñada para que las instituciones educativas cuenten con un equipo médico especializado que realice revisiones continuas a los estudiantes. Nuestro objetivo es:
*   **Detección Temprana**: Identificar patologías de forma anticipada mediante chequeos regulares.
*   **Seguimiento Evolutivo**: Obtener métricas precisas del desarrollo del estudiante desde la infancia hasta la culminación de su etapa secundaria.
*   **Confianza Familiar**: Brindar a los padres la tranquilidad de un control médico continuo y profesional dentro del entorno escolar.

---

## 🚀 Plataformas de Servicio

Accede a los diferentes frentes del ecosistema Vitaly:

| Dashboard | Enlace |
| :--- | :--- |
| **🛡️ Admin Dashboard** | [Acceder aquí](https://vitaly-admin-dashboard.lovable.app) |
| **👨‍👩‍👧 User Dashboard (Familias)** | [Acceder aquí](https://vitaly-care-for-families.lovable.app) |

---

## 🛠️ Tecnologías y Características Actuales (v0.4.0)

El backend está construido con un enfoque en seguridad, escalabilidad y estandarización técnica.

### Funcionalidades Implementadas:
*   **🔐 Autenticación y RBAC**: Sistema de control de acceso basado en roles (`admin_sistema`, `admin_escuela`, `representante`) integrado con Supabase Auth.
*   **📊 Módulos Administrativos**: Gestión de catálogos de salud, instituciones educativas y periodos académicos.
*   **👨‍👩‍👧 Gestión de Representantes**: Sistema de vinculación de estudiantes mediante número de identidad para padres.
*   **📄 Documentación Automatizada**: OpenAPI personalizada con Swagger y ReDoc brandeados.
*   **⚡ Arquitectura Limpia**: Estructura modular basada en servicios, esquemas Pydantic y dependencias inyectables.
*   **📡 Health Monitoring**: Dashboard visual de estado del sistema y servicios integrados.

---

## ⚙️ Configuración del Proyecto

### Requisitos Previos
*   Python 3.10+
*   Entorno virtual (recomendado)

### Instalación
1.  **Clonar y configurar entorno:**
    ```bash
    git clone https://github.com/Teixeira49/vitaly_backend.git
    cd vitaly_backend
    python -m venv venv
    source venv/bin/activate  # venv\Scripts\activate en Windows
    pip install -r requirements.txt
    ```

2.  **Variables de Envierno:**
    Configura tu archivo `.env` con las credenciales de Supabase:
    ```env
    SUPABASE_URL=tu_url
    SUPABASE_KEY=tu_key
    SUPABASE_SERVICE_KEY=tu_service_role_key
    JWT_SECRET=tu_secreto
    ```

    `SUPABASE_SERVICE_KEY` es la clave `service_role` del proyecto. La usan la
    RPC interna de baja propia y la tabla `settings`, que conserva RLS
    activo y sin políticas para que la `anon key` no pueda leerla ni modificarla
    por la Data API. **Nunca debe salir del servidor ni llegar al frontend.** Si
    falta, la API arranca igual: la configuración aplica sus valores por defecto
    y los endpoints `/api/v1/admin/settings` responden 500 indicando la causa.
    `DELETE /api/v1/auth/me` también responde 500 sin modificar datos si falta
    esta clave. Para habilitar la baja, aplicar antes la migración
    `20261106120000_delete_own_account.sql` siguiendo su protocolo de aprobación.

3.  **Ejecutar Servidor en Desarrollo:**
    ```bash
    uvicorn app.main:app --reload
    ```

---

## 📚 Documentación de la API

Una vez que el proyecto esté corriendo, puedes acceder a la documentación interactiva en:
*   **Swagger UI**: `http://localhost:8000/docs`
*   **ReDoc**: `http://localhost:8000/redoc`
*   **Health Status**: `http://localhost:8000/health-visual`

---
*Vitaly - Acompañando el crecimiento, protegiendo la vida.*
