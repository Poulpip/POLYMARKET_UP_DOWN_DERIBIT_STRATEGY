---
name: create_skill
description: Guides the agent on how to create new Antigravity skills using the 5-level best practices framework (Basic Router, Asset Utilization, Learning by Example, Procedural Logic, and The Architect). Use this when the user asks to create, build, or design a new skill.
---

# Skill Creation Guide

When the user asks you to create a new skill, you must design it according to the 5-Level Skill Framework. A skill is not just a prompt; it is an orchestrated combination of instructions, examples, static resources, and scripts.

## Core Rules
1. **Directory Structure**: Always place the skill in the appropriate directory (e.g., `.agents/skills/<skill_name>/` for workspace skills).
2. **SKILL.md**: Every skill MUST have a `SKILL.md` file with YAML frontmatter containing `name` and `description`.
3. **Keep it Lean**: Do not bloat `SKILL.md` with huge code examples, static text, or complex deterministic logic. Delegate those to `examples/`, `resources/`, or `scripts/`.

## The 5 Levels of Skill Design

Evaluate the user's request and implement the appropriate level of complexity:

### Level 1: The Basic Router
*Use for simple instruction-following (e.g., formatting, style guides).*
- **Structure**: Just a `SKILL.md`.
- **Content**: Clear rules, allowed parameters, and concise instructions.
- **Example**: A `git-commit-formatter` that just lists the Conventional Commits rules.

### Level 2: Asset Utilization (The "Reference" Pattern)
*Use when the skill requires injecting static boilerplate, legal text, or templates.*
- **Structure**: `SKILL.md` + `resources/<template_file>`
- **Content**: The `SKILL.md` instructs the agent to read the resource file verbatim.
- **Example**: A `license-header-adder` that reads `resources/HEADER_TEMPLATE.txt`.

### Level 3: Learning by Example (The "Few-Shot" Pattern)
*Use when complex heuristics or style matching is required (e.g., JSON to Pydantic).*
- **Structure**: `SKILL.md` + `examples/<input>` + `examples/<output>`
- **Content**: The `SKILL.md` instructs the agent to look at the golden examples before generating its own output. LLMs are pattern matchers, and this reduces token bloat and hallucinations.
- **Example**: `examples/input_data.json` mapped to `examples/output_model.py`.

### Level 4: Procedural Logic (The "Tool Use" Pattern)
*Use when deterministic truth, math, or strict validation is required (e.g., Schema checking).*
- **Structure**: `SKILL.md` + `scripts/<script_name>.py`
- **Content**: Do not let the LLM "eyeball" safety checks. The `SKILL.md` must instruct the agent to run the Python/Bash script, then parse the exit code.
- **Example**: A schema validator that runs `python scripts/validate_schema.py <file>`.

### Level 5: The Architect
*Use for complex code scaffolding that combines templates, scripts, and examples.*
- **Structure**: `SKILL.md` + `resources/` + `scripts/` + `examples/`
- **Content**: Instructs the agent to first run a scaffolding script, then read a template, and finally look at an example to fill in the complex logic.
- **Example**: `adk-tool-scaffold` which generates boilerplate code using a script and then guides the LLM to write the API calls using an example.

## Execution Steps for the Agent
1. Determine which of the 5 Levels best fits the requested skill.
2. Create the necessary folders (`scripts/`, `examples/`, `resources/`).
3. Generate the `SKILL.md` and any associated auxiliary files using the proper tools.
