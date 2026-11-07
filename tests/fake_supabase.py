"""Falso cliente supabase-py que aplica los filtros en memoria.

Un ``MagicMock`` encadenable devuelve las filas que se le preparan sin mirar los
``.eq()`` que recibe, así que una consulta con el filtro equivocado —o sin él—
produce el mismo resultado y el test pasa igual. Este doble sí filtra, proyecta
y ordena, de modo que un filtro perdido cambia el resultado y rompe el test.

Fidelidad con PostgREST/PostgreSQL, donde importa:

- ``eq``/``neq`` nunca casan con ``NULL``, igual que en SQL.
- Las comparaciones de rango parsean la marca de tiempo en lugar de comparar
  cadenas: ``2026-04-30T20:00:00-04:00`` y ``2026-05-01T00:00:00+00:00`` son el
  mismo instante, y comparados como texto darían resultados opuestos.
- ``order`` ascendente deja los ``NULL`` al final, como el `NULLS LAST` por
  defecto de PostgreSQL.
- ``count="exact"`` se captura antes de aplicar ``range``/``limit``, porque el
  ``Content-Range`` de PostgREST cuenta el total, no la página.
- ``update`` modifica solo las filas que superan los filtros encadenados después
  y las devuelve, como ``Prefer: return=representation``. Una actualización sin
  coincidencias devuelve ``data=[]``.

Sigue siendo un doble: no valida nombres de columna (PostgREST devolvería 400 a
una columna inexistente) ni implementa joins anidados.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

_MISSING = object()


def _as_instant(value):
    """Convierte una marca de tiempo ISO 8601 en un instante comparable.

    Devuelve ``None`` si el valor no es una fecha, para que el llamante lo trate
    como ``NULL``. Un valor sin zona horaria se interpreta como UTC, igual que
    ``app.services.event_schedule_policy.to_utc``.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class FakeQuery:
    """Consulta supabase-py mínima que filtra, proyecta y ordena de verdad."""

    def __init__(self, rows: list[dict], fail: bool = False):
        # Copia de la lista, no de las filas: ``update`` escribe sobre la tabla.
        self._rows = list(rows)
        self._update: dict | None = None
        self._columns: list[str] | None = None
        self._count = False
        self._total = None
        self._fail = fail

    # ── Proyección ────────────────────────────────────────
    def select(self, columns: str = "*", count=None):
        if columns != "*":
            self._columns = [c.strip() for c in columns.split(",")]
        self._count = count is not None
        return self

    # ── Escritura ─────────────────────────────────────────
    def update(self, values: dict):
        self._update = dict(values)
        return self

    # ── Filtros de igualdad ───────────────────────────────
    def eq(self, column, value):
        self._rows = [r for r in self._rows if r.get(column, _MISSING) == value]
        return self

    def neq(self, column, value):
        # SQL: 'col <> v' descarta los NULL, no los conserva.
        self._rows = [
            r for r in self._rows
            if r.get(column, _MISSING) is not _MISSING
            and r.get(column) is not None
            and r[column] != value
        ]
        return self

    def in_(self, column, values):
        self._rows = [r for r in self._rows if r.get(column, _MISSING) in values]
        return self

    # ── Filtros de rango (sobre instantes, no sobre texto) ─
    def _compare(self, column, value, keep):
        bound = _as_instant(value)
        if bound is None:  # columna no temporal: comparación directa
            self._rows = [
                r for r in self._rows
                if r.get(column) is not None and keep(r[column], value)
            ]
            return self
        self._rows = [
            r for r in self._rows
            if _as_instant(r.get(column)) is not None and keep(_as_instant(r[column]), bound)
        ]
        return self

    def gte(self, column, value):
        return self._compare(column, value, lambda a, b: a >= b)

    def lte(self, column, value):
        return self._compare(column, value, lambda a, b: a <= b)

    def lt(self, column, value):
        return self._compare(column, value, lambda a, b: a < b)

    def gt(self, column, value):
        return self._compare(column, value, lambda a, b: a > b)

    # ── Orden y paginación ────────────────────────────────
    def order(self, column, desc=False):
        # PostgreSQL: ASC deja los NULL al final; DESC, al principio.
        self._rows.sort(
            key=lambda r: (r.get(column) is None, r.get(column) if r.get(column) is not None else ""),
            reverse=desc,
        )
        return self

    def range(self, start, end):
        self._total = len(self._rows)   # el total se cuenta antes de recortar
        self._rows = self._rows[start:end + 1]
        return self

    def limit(self, count):
        self._total = len(self._rows)
        self._rows = self._rows[:count]
        return self

    def execute(self):
        # La E/S real ocurre en execute(), no al construir la consulta: ahí es
        # donde supabase-py levanta APIError y donde el servicio debe envolverlo.
        if self._fail:
            raise RuntimeError("postgrest unavailable")
        if self._update is not None:
            for row in self._rows:
                row.update(self._update)
        rows = self._rows
        total = self._total if self._total is not None else len(rows)
        if self._columns is not None:
            rows = [{c: r.get(c) for c in self._columns} for r in rows]
        else:
            rows = [dict(r) for r in rows]
        return SimpleNamespace(data=rows, count=total if self._count else None)


class FakeSupabase:
    """Sustituto del cliente compartido sobre una base de datos en memoria."""

    def __init__(self, **tables: list[dict]):
        self.db = tables
        self.failing_table: str | None = None

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self.db.get(name, []), fail=name == self.failing_table)
