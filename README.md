# OptiQuery (OptiSQL)

A SQL-to-NoSQL query compiler with cost-based query optimization. OptiQuery compiles a supported subset of SQL through a formal compiler pipeline, optimizes equivalent query execution plans, and generates executable queries for NoSQL backends (MongoDB and Redis).

## Overview

OptiQuery demonstrates compiler design principles, database query optimization, intermediate representations, and practical NoSQL query generation. It follows a proper compiler pipeline with distinct phases rather than simple string-to-string translation.

## Features (Planned)

- [ ] Lexical analysis and parsing
- [ ] Abstract Syntax Tree (AST) construction
- [ ] Symbol tables and semantic analysis
- [ ] Type checking and identifier validation
- [ ] Database-independent IR
- [ ] Query optimization (filter reordering, projection pushdown, CSE, constant folding)
- [ ] Cost-based query planner
- [ ] MongoDB aggregation pipeline generation
- [ ] Redis command generation
- [ ] Query explanation and diagnostics

## Installation

```bash
pip install -e .
```

## Development

Run tests:
```bash
pytest
```
