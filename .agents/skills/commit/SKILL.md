---
name: commit
description: Generate a proper git commit following the Conventional Commits specification
---

When the user invokes the `/commit` command, you must follow this strict operating procedure to ensure proper, professional Git iterations:

## Step 1: Assess the State
1. Run `git status` to identify all modified, staged, and untracked files.
2. Run `git diff --cached` (if files are staged) or `git diff` (if unstaged) to review the exact code changes.
3. Analyze the diffs to understand the *intent* and *scope* of the modifications.

## Step 2: Stage Files (If necessary)
- If the user hasn't staged the files, ask them if they want to stage all modified files, or propose running `git add .` on their behalf.
- Do not commit untracked files like `.env`, `logs/`, or `.db` files unless explicitly instructed. Ensure they are in `.gitignore`.

## Step 3: Format the Commit Message
Generate a professional commit message adhering strictly to the **Conventional Commits** specification:
- `feat:` for new features.
- `fix:` for bug fixes.
- `refactor:` for code changes that neither fix a bug nor add a feature.
- `chore:` for updating tasks, build processes, or auxiliary tools.

Structure:
<type>(<optional scope>): <subject>

<body>
- Bullet point explaining *why* the change was made.
- Bullet point detailing any specific architectural shifts.

## Step 4: Execute
1. Present the drafted commit message to the user for quick review.
2. Upon approval, execute the commit using: `git commit -m "<type>: <subject>" -m "<body>"`
3. If requested, run `git push`.
