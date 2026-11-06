"""Falso cliente supabase-py que aplica los filtros en memoria.

Un ``MagicMock`` encadenable devuelve las filas que se le preparan sin mirar los
``.eq()`` que recibe, así que una consulta con el filtro equivocado —o sin él—
produce el mismo resultado y el test pasa igual. Este doble sí filtra, proyecta
y ordena, de modo que un filtro perdido cambia el resultado y rompe el test.

Las marcas de tiempo se comparan como cadenas, que es el orden de PostgreSQL
siempre que todas las filas de un mismo test usen el mismo offset UTC.
"""

from types import SimpleNamespace


class FakeQuery:
    """Consulta supabase-py mínima que filtra, proyecta y ordena de verdad."""

    def __init__(self, rows: list[dict], fail: bool = False):
        self._rows = [dict(row) for row in rows]
        self._columns: list[str] | None = None
        self._count = False
        self._fail = fail

    def select(self, columns: str = "*", count=None):
        if columns != "*":
            self._columns = [c.strip() for c in columns.split(",")]
        self._count = count is not None
        return self

    def eq(self, column, value):
        self._rows = [r for r in self._rows if r.get(column) == value]
        return self

    def neq(self, column, value):
        self._rows = [r for r in self._rows if r.get(column) != value]
        return self

    def in_(self, column, values):
        self._rows = [r for r in self._rows if r.get(column) in values]
        return self

    def gte(self, column, value):
        self._rows = [r for r in self._rows if r.get(column) is not None and r[column] >= value]
        return self

    def lte(self, column, value):
        self._rows = [r for r in self._rows if r.get(column) is not None and r[column] <= value]
        return self

    def lt(self, column, value):
        self._rows = [r for r in self._rows if r.get(column) is not None and r[column] < value]
        return self

    def order(self, column, desc=False):
        self._rows.sort(key=lambda r: r.get(column) or "", reverse=desc)
        return self

    def range(self, start, end):
        self._rows = self._rows[start:end + 1]
        return self

    def limit(self, count):
        self._rows = self._rows[:count]
        return self

    def execute(self):
        # La E/S real ocurre en execute(), no al construir la consulta: ahí es
        # donde supabase-py levanta APIError y donde el servicio debe envolverlo.
        if self._fail:
            raise RuntimeError("postgrest unavailable")
        rows = self._rows
        total = len(rows)
        if self._columns is not None:
            rows = [{c: r.get(c) for c in self._columns} for r in rows]
        return SimpleNamespace(data=rows, count=total if self._count else None)


class FakeSupabase:
    """Sustituto del cliente compartido sobre una base de datos en memoria."""

    def __init__(self, **tables: list[dict]):
        self.db = tables
        self.failing_table: str | None = None

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self.db.get(name, []), fail=name == self.failing_table)
