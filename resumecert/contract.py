"""Declarative, bounded action contracts. No live state belongs here."""

from dataclasses import dataclass
from hashlib import sha256
from itertools import product
import json

Scalar = str | int | bool
Facts = tuple[tuple[str, Scalar], ...]
Expr = tuple
MAX_CANDIDATE_WORLDS = 256


def same(left: Scalar, right: Scalar) -> bool:
    # Python otherwise treats True and 1 (or False and 0) as equal.
    return type(left) is type(right) and left == right


def _scalar(value: object) -> bool:
    return type(value) in (str, int, bool)


def _name(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def _operand(term, fields):
    if type(term) is not tuple or len(term) != 2:
        raise ValueError("expected a field or literal operand")
    kind, value = term
    if type(kind) is not str:
        raise ValueError("invalid operand kind")
    if kind == "field" and _name(value) and value in fields:
        return
    if kind == "literal" and _scalar(value):
        return
    raise ValueError("invalid operand")


def _validate(expression: Expr, fields, depth: int = 0) -> None:
    if depth > 16 or type(expression) is not tuple or not expression:
        raise ValueError("invalid expression")
    op, *args = expression
    if type(op) is not str:
        raise ValueError("invalid expression operator")
    if op == "eq" and len(args) == 2:
        for arg in args:
            _operand(arg, fields)
    elif (op in ("all", "any") and args) or (op == "not" and len(args) == 1):
        for arg in args:
            _validate(arg, fields, depth + 1)
    else:
        raise ValueError("invalid expression operator or arity")


def _evaluate(expression: Expr, world: dict[str, Scalar]) -> bool:
    op, *args = expression
    if op == "eq":
        a, b = (world[value] if kind == "field" else value for kind, value in args)
        return same(a, b)
    if op == "not":
        return not _evaluate(args[0], world)
    values = (_evaluate(arg, world) for arg in args)
    return all(values) if op == "all" else any(values)


@dataclass(frozen=True, slots=True)
class Query:
    name: str
    fields: tuple[str, ...]
    cost: int = 1


@dataclass(frozen=True, slots=True)
class ActionContract:
    action_id: str
    version: str
    domains: tuple[tuple[str, tuple[Scalar, ...]], ...]
    queries: tuple[Query, ...]
    precondition: Expr
    admissibility: Expr | None = None

    def __post_init__(self):
        if not _name(self.action_id) or not _name(self.version):
            raise ValueError("action ID and version must be nonempty")
        if type(self.domains) is not tuple or not self.domains:
            raise ValueError("provide immutable, nonempty field domains")
        fields = set()
        size = 1
        for domain in self.domains:
            if type(domain) is not tuple or len(domain) != 2:
                raise ValueError("field domain entries must be immutable pairs")
            field, values = domain
            if not _name(field) or field in fields:
                raise ValueError("invalid or duplicate field")
            if type(values) is not tuple or not values or not all(map(_scalar, values)):
                raise ValueError("provide immutable, nonempty scalar domains")
            if len({(type(v), v) for v in values}) != len(values):
                raise ValueError("duplicate domain value")
            size *= len(values)
            if size > MAX_CANDIDATE_WORLDS:
                raise ValueError(f"Cartesian space exceeds {MAX_CANDIDATE_WORLDS} worlds")
            fields.add(field)
        if type(self.queries) is not tuple:
            raise ValueError("provide an immutable query catalog")
        names = set()
        for query in self.queries:
            if type(query) is not Query or not _name(query.name) or query.name in names:
                raise ValueError("invalid or duplicate query")
            if type(query.fields) is not tuple or not query.fields:
                raise ValueError("query must have immutable, nonempty fields")
            if any(not _name(f) or f not in fields for f in query.fields):
                raise ValueError("query uses unknown field")
            if len(set(query.fields)) != len(query.fields):
                raise ValueError("query repeats a field")
            if type(query.cost) is not int or query.cost <= 0:
                raise ValueError("query cost must be a positive integer")
            names.add(query.name)
        _validate(self.precondition, fields)
        if self.admissibility is not None:
            _validate(self.admissibility, fields)
            if next(self.worlds(), None) is None:
                raise ValueError("admissible state space is empty")

    @property
    def digest(self) -> str:
        data = ("resumecert-contract-v1", self.action_id, self.version, self.domains,
                tuple((q.name, q.fields, q.cost) for q in self.queries),
                self.precondition, self.admissibility)
        canonical = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
        return sha256(canonical.encode("ascii")).hexdigest()

    def query(self, name: str) -> Query:
        if type(name) is not str:
            raise ValueError("query name must be a string")
        for query in self.queries:
            if query.name == name:
                return query
        raise ValueError(f"undeclared query: {name!r}")

    def valid_value(self, field: str, value: Scalar) -> bool:
        return any(name == field and any(same(v, value) for v in values)
                   for name, values in self.domains)

    def worlds(self):
        """Yield fresh mappings in canonical order, restricted by admissibility."""
        fields, domains = zip(*self.domains)
        for values in product(*domains):
            world = dict(zip(fields, values))
            if self.admissibility is None or _evaluate(self.admissibility, world):
                yield world

    def permits(self, world: dict[str, Scalar]) -> bool:
        return _evaluate(self.precondition, world)

    def facts(self, values) -> Facts:
        """Copy known facts into immutable field order; reject duplicates."""
        items = tuple(values.items()) if hasattr(values, "items") else tuple(values)
        if len(dict(items)) != len(items):
            raise ValueError("duplicate fact field")
        if any(not self.valid_value(f, v) for f, v in items):
            raise ValueError("fact outside the declared domain")
        known = dict(items)
        return tuple((f, known[f]) for f, _ in self.domains if f in known)
