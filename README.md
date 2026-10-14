# OptiQuery (OptiSQL)

A SQL-to-NoSQL query compiler with a cost-based optimizer. OptiQuery compiles a
supported subset of SQL through a real compiler pipeline, optimizes equivalent
query plans, and generates executable queries for NoSQL backends (MongoDB and
Redis).

It is **not** a string-rewriting shim: SQL is lexed, parsed into an AST,
semantically analyzed, lowered into a database-independent intermediate
representation (IR), rewritten by optimization passes, compared using a cost
model, and only then translated into a backend query.

## Architecture

```
   SQL text
      │
      ▼
 ┌──────────┐   ┌───────────┐   ┌────────────────┐   ┌────────────┐
 │  Lexer   │──▶│  Parser   │──▶│    Semantic    │──▶│  Lowering  │
 │ (tokens) │   │  (AST)    │   │ (symbol tables,│   │   (IR)     │
 └──────────┘   └───────────┘   │  type checks)  │   └─────┬──────┘
                                └────────────────┘         │
                                                           ▼
                          ┌───────────────────────────────────────────┐
                          │              Optimizer                    │
                          │  rule passes: projection pushdown,        │
                          │  filter reordering, constant folding,     │
                          │  limit pushdown, DAG-based CSE            │
                          │  + cost-based candidate selection         │
                          └───────────────────┬───────────────────────┘
                                              │ optimized plan
                                              ▼
                          ┌───────────────────────────────────────────┐
                          │      Backend adapters (registry)          │
                          │  MongoDB → aggregation pipeline           │
                          │  Redis   → commands for a documented      │
                          │            data model                     │
                          └───────────────────┬───────────────────────┘
                                              │
                                              ▼
                                   generated query
                              (+ optional guarded execution)
```

## Features

- [x] Lexical analysis with source spans and structured diagnostics
- [x] Recursive-descent parser with precedence climbing → typed AST
- [x] Symbol tables, identifier resolution, and type checking
- [x] Database-independent IR (expressions + logical plan operators)
- [x] Optimization passes:
  - [x] Projection pushdown
  - [x] Filter reordering (selectivity heuristic)
  - [x] Constant folding (null propagation, boolean identities)
  - [x] Limit pushdown (below projections)
  - [x] DAG-based common subexpression elimination (subtree sharing)
- [x] Operator-level capability validation per backend
- [x] Cost model with a per-operator cost breakdown
- [x] Cost-based candidate generation and selection
- [x] MongoDB aggregation pipeline generation
- [x] Redis command generation (documented data model)
- [x] Optional, guarded execution through backend adapters
- [x] Plan explanation, JSON output, and diagnostics
- [x] CLI and Python API

## Installation

```bash
# Using uv (recommended)
uv venv .venv
uv pip install -e .

# or with pip
pip install -e .
```

Backends are optional. To enable live execution install the relevant driver:

```bash
pip install pymongo   # MongoDB execution
pip install redis     # Redis execution
```

## Usage

### Command line

```bash
python -m optisql "SELECT name, age FROM users WHERE age >= 18 ORDER BY age LIMIT 10"
python -m optisql --schema examples/schema.json "SELECT country, COUNT(*) FROM users GROUP BY country"
python -m optisql --backend redis "SELECT name FROM users WHERE id = 42"
python -m optisql --no-optimize --explain "SELECT * FROM users"
python -m optisql --json "SELECT name FROM users LIMIT 3"
```

Flags: `--backend/-b`, `--schema/-s`, `--no-optimize`, `--explain`, `--ast`,
`--json`.

### Python API

```python
from optisql.compiler import Compiler
from optisql.backends import get_backend
from optisql.execution import QueryExecutor
from optisql.metadata import Catalog

# A permissive catalog treats unknown collections as unresolved (with a
# warning) instead of an error. Pass a populated Catalog for strict checking.
compiler = Compiler(catalog=Catalog(strict=False), backend="mongodb")
result = compiler.compile("SELECT name FROM users WHERE age >= 18 LIMIT 5")

print(result.ok)                       # True
print(result.generated)                # MongoDB aggregation pipeline
print(result.plan_explanation())       # human-readable optimized plan

# Optional execution (dry-run unless enabled AND connected):
executor = QueryExecutor(backend=get_backend("mongodb"), enabled=False)
outcome = executor.execute(result.optimized_plan)   # executed == False
```

## Backends

### MongoDB

Generates an aggregation pipeline: `$match`, `$project`, `$sort`, `$limit`,
`$skip`, `$group`. `$match` stages use MongoDB **query-document** syntax
(`{age: {$gte: 18}}`); field-to-field comparisons are wrapped in `$expr`.
Supported operators: scans, filters (comparison, `IN`, `BETWEEN`, `LIKE`,
`IS [NOT] NULL`, `AND`/`OR`/`NOT`), projections, sorts, limits/offsets, grouping
and aggregates (`COUNT`, `SUM`, `AVG`, `MIN`, `MAX`), and `DISTINCT` over an
enumerable projection.

### Redis

Redis is not relational, so OptiQuery generates commands only for query shapes
that are correct under an explicit data model (see
`optisql.codegen.redis.RedisDataModel`):

- one Redis **hash per document**, keyed `{prefix}:{collection}:{pk}`;
- a primary-key field (default `id`) is the unique key;
- equality on one **indexed** field uses an application-managed index `SET`
  (`{prefix}:idx:{collection}:{field}:{value}`);
- point lookups project to `HMGET`, otherwise `HGETALL`.

Anything else (ranges, sorting, grouping, joins, `OR`, `DISTINCT`) is rejected
with a structured error instead of emitting a query that would return wrong
results.

## Supported SQL subset

`SELECT` with projection (including `*`), `FROM`, `WHERE`, `GROUP BY`, `HAVING`,
`ORDER BY` (including columns not in the SELECT list and computed keys),
`LIMIT`, `OFFSET`, and `DISTINCT`; aggregates `COUNT`, `SUM`, `AVG`, `MIN`,
`MAX`, including `COUNT`/`SUM`/`AVG` with `DISTINCT`; scalar functions `ABS`,
`LOWER`, `UPPER`, `LENGTH`, `ROUND`, `COALESCE`; predicates `=`, `!=`, `<`, `>`,
`<=`, `>=`, `IN`, `BETWEEN`, `LIKE`, `IS [NOT] NULL`; logical `AND`/`OR`/`NOT`;
`CASE` expressions. Comparisons whose right-hand side is computed (arithmetic,
function call, or another column) are compiled with `$expr`. The parser also
recognizes `INSERT`/`UPDATE`/`DELETE`/`CREATE TABLE` and `JOIN` clauses, but
backend code generation for those is not yet implemented and is reported as an
explicit error.

## Optimization & cost model

The optimizer runs rule-based passes, then generates safe, semantically
equivalent candidate plans (e.g. alternative filter orderings) and selects the
lowest **estimated** cost. The cost model reports a per-operator breakdown
(scan, filter, project, sort, group, move, backend round-trip overhead). Costs
are *estimates from catalog statistics*, not measured runtimes; the compiler
never invents performance numbers it did not compute.

## Diagnostics

Diagnostics carry a machine-readable code and, when available, a source span:

| Range   | Phase                       |
|---------|-----------------------------|
| `E100x` | Lexer                       |
| `E200x` | Parser                      |
| `E300x` | Semantic analysis           |
| `E700x` | MongoDB code generation     |
| `E800x` | Redis code generation       |
| `E900x` | Driver / backend selection  |
| `E910x` | Backend capability checks   |

## Limitations & non-goals

- Compilation works without any database credentials or metadata. Execution is
  **off by default** and requires an installed driver plus an explicitly
  configured client/URI (never hardcoded; see `*Backend.from_environment`).
- MongoDB codegen does not yet emit `INSERT`/`UPDATE`/`DELETE` or joins.
- `SELECT DISTINCT *` over an unknown field set is rejected rather than guessed.
- CSE produces a DAG; downstream phases treat plan nodes as read-only.
- The cost model is a deterministic estimator, not a live profiler.
- Redis supports only the documented subset described above.

## Development

```bash
# Run the test suite (uses pythonpath=src from pyproject.toml)
.venv/bin/python -m pytest tests/ -q
```

## Project layout

```
src/optisql/
  lexer/         tokens and tokenizer
  parser/        recursive-descent parser
  ast/           AST node classes
  metadata/      schema catalog and field types
  symbols/       symbol tables
  semantic/      semantic analysis and type checking
  ir/            intermediate representation (expr, plan, transform, serialize)
  planner/       AST -> IR lowering
  optimizer/     passes, cost model, candidate selection
  codegen/       MongoDB and Redis query generation
  backends/      backend adapters, registry, capability validation
  execution/     optional guarded execution
  cli/           command-line interface
```
