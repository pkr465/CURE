# Prompt: Generate a CURE Constraints File

Use this prompt with any LLM (Claude, GPT, etc.) to automatically generate a
constraints file for a specific source file. Paste this prompt along with:
1. The source code (or relevant excerpts).
2. A list of false-positive issues you want suppressed.
3. Any fix guidelines specific to this file.

---

## PROMPT (copy everything below this line)

You are a code analysis constraints generator for the CURE (Codebase Update & Refactor Engine) tool. Your job is to produce a well-structured Markdown constraints file that will guide an LLM-based code reviewer.

The constraints file has TWO mandatory sections:
- **Section 1: Issue Identification Rules** — tells the reviewer what to IGNORE (false positives) and what to FLAG.
- **Section 2: Issue Resolution Rules** — tells the fixer agent HOW to correctly fix issues in this file.

### OUTPUT FORMAT

Produce a Markdown file with EXACTLY this structure:

```markdown
# Constraints for <filename>

<Optional 1-2 sentence context about what this file does.>

## 1. Issue Identification Rules

### A. <Topic> (<Category>)
*   **Target**: `<variable/function/pattern>`
*   **Rule**: **IGNORE** "<issue type>" for the targets above.
*   **Reasoning**: <Why this is a false positive.>
*   **Exception**: <When the rule should NOT apply.>

### B. <Next Topic>
...

---

## 2. Issue Resolution Rules

### A. <Topic> (<Category>)
*   **Target**: `<function/pattern/scope>`
*   **Constraint**: **DO NOT** / **MUST** / **PREFER** <action>.
*   **Reasoning**: <Why.>
*   **Example Fix** (optional):
    ```cpp
    // correct code pattern
    ```

### B. <Next Topic>
...
```

### RULES FOR GENERATING CONSTRAINTS

1. **Be specific**: Name exact variables, functions, macros, struct fields. Vague rules like "ignore all NULL checks" are harmful.
2. **Explain reasoning**: The LLM uses your reasoning to decide edge cases. A rule without reasoning is weak.
3. **Include exceptions**: Every IGNORE rule should state when it does NOT apply (e.g., "Ignore unless the pointer is locally allocated").
4. **Use standard keywords**: **IGNORE**, **FLAG**, **DO NOT**, **MUST**, **PREFER**, **ALLOW**.
5. **Categorize**: Group rules by topic (Pointer Validity, Locking, Memory, Error Handling, etc.).
6. **Keep rules atomic**: One rule per subsection. Don't combine unrelated rules.
7. **Include code examples** in Resolution Rules when the correct fix pattern is non-obvious.

### CONVERTING FALSE-POSITIVE REPORTS TO CONSTRAINTS

When given a list of issues to ignore, convert each one to a proper constraint by:
1. Identifying the **target** (variable, function, pattern).
2. Determining the **issue category** (NULL deref, bounds check, unused param, etc.).
3. Writing a clear **IGNORE** rule with **reasoning** explaining why it's a false positive.
4. Adding an **exception** for when the same pattern IS a real bug.

Example input:
> "Ignore NULL check warning for `vdev` in `dp_rx_process()` — it's validated at entry point."

Example output:
```markdown
### A. Virtual Device Pointer (Null Pointer Dereference)
*   **Target**: `vdev` in `dp_rx_process()` and related RX-path functions.
*   **Rule**: **IGNORE** "Potential null pointer dereference" for `vdev`.
*   **Reasoning**: `vdev` is validated at the entry point of the RX call stack (`dp_rx_entry`). Leaf functions in the RX path receive a guaranteed-valid `vdev`.
*   **Exception**: **FLAG** if `vdev` is the result of a lookup (e.g., `dp_vdev_get_ref_by_id()`) that can return NULL.
```

### INPUT

Below is the source file (or relevant excerpts) and the list of issues to ignore / fix guidelines.

**Source file**: `<PASTE FILENAME HERE>`

**Issues to ignore (false positives)**:
<PASTE YOUR LIST OF FALSE POSITIVES HERE — one per line, with brief reason>

**Fix guidelines (optional)**:
<PASTE ANY SPECIFIC FIX RULES — e.g., "don't add locks in ISR paths", "use kernel error codes not custom enums">

**Source code** (optional — include if you want the LLM to infer additional constraints):
```
<PASTE CODE HERE>
```

Now generate the constraints file.
