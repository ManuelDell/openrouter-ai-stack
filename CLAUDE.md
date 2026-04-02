# Coding Principles for this Project

All code in this repository must follow these principles strictly.

## SOLID

**Single Responsibility**
Each function/class does exactly one thing well. If you need "and" to describe what it does, split it.

**Open/Closed**
New behavior via new functions or modules — never by editing working logic inside existing ones.

**Liskov Substitution**
Any dispatcher or service can be swapped without breaking callers. Define clear input/output contracts.

**Interface Segregation**
Small, focused interfaces. A cost tracker should not know about routing. A dispatcher should not know about the DB schema.

**Dependency Inversion**
Depend on abstractions (function signatures, base URLs from env) not on concrete implementations (hardcoded model names, direct DB paths).

## DRY — Don't Repeat Yourself

Logic lives in exactly one place. If you copy more than 2 lines, extract a shared helper.
Model names, URLs, and thresholds come from env vars — never repeated as literals.

## KISS — Keep It Simple

Write the simplest code that works. No clever one-liners that need explaining. No speculative abstractions. No premature optimization.
When in doubt: a flat function beats a class, and a dict beats an ORM.

---

## Project-Specific Rules

- All new service code goes under `services/<service>/`
- Dispatchers are stateless — no global mutable state
- Every new endpoint must have a `/health` or be reachable via the router's `/health`
- Costs are tracked after **every** OpenRouter API call — never skipped
- Env vars for all configuration — nothing hardcoded
- New images must be pushed to `ghcr.io/manueldell/openrouter-ai-stack/<name>:latest`
