# Constraints for <FILENAME>
#
# NAMING CONVENTION:
#   Save this file as: <filename_without_extension>_constraints.md
#   Examples:
#     Source file: dp_main.c        → dp_main_constraints.md
#     Source file: ratectrl.cpp     → ratectrl_constraints.md
#     Source file: wlan_hdd_main.c  → wlan_hdd_main_constraints.md
#
# PLACEMENT:
#   Place this file in: agents/constraints/
#   Subdirectories are supported — the tool searches recursively.
#
# HOW IT WORKS:
#   CURE loads constraints at two stages:
#   1. ANALYSIS (CodebaseLLMAgent) — Reads "Issue Identification Rules" to
#      suppress false positives BEFORE the LLM reports them.
#   2. FIXING (CodebaseFixerAgent) — Reads "Issue Resolution Rules" to guide
#      HOW the LLM generates code fixes.
#
#   Both stages also load common_constraints.md (global rules).
#   File-specific constraints override or supplement the global rules.
#
# INSTRUCTIONS FOR CREATING A CONSTRAINTS FILE:
#   1. Copy this template and rename it per the naming convention above.
#   2. Fill in Section 1 with issues the LLM should IGNORE for this file.
#   3. Fill in Section 2 with rules the LLM should FOLLOW when fixing code.
#   4. Delete any placeholder sections you don't need.
#   5. Use ### subsections (A, B, C…) to organize rules by topic.
#
# TIPS:
#   - Be specific: name variables, functions, macros, or patterns.
#   - Explain WHY a rule exists (the LLM uses reasoning to apply rules).
#   - Use **IGNORE**, **FLAG**, **DO NOT**, **MUST**, **PREFER** for clarity.
#   - Include code examples where helpful — they anchor the LLM's understanding.
#

## 1. Issue Identification Rules

<!--
  Rules in this section tell the LLM what to IGNORE (false positives)
  and what to FLAG (true positives) during code analysis.

  Each rule should include:
    - **Target**: The variable, function, pattern, or code construct.
    - **Rule**: IGNORE or FLAG, with the condition.
    - **Reasoning**: Why this rule exists (helps the LLM make decisions).
    - **Exception** (optional): When the rule does NOT apply.
-->

### A. [Topic Name] ([Issue Category])
*   **Target**: `variable_name`, `function_name()`, or code pattern.
*   **Rule**: **IGNORE** "[issue description]" for the targets listed above.
*   **Reasoning**: [Explain why this is a false positive in this file's context.]
*   **Exception**: [When should this NOT be ignored? e.g., "Flag if the pointer is locally allocated."]

### B. [Topic Name]
*   **Target**: `another_variable`, `another_pattern`.
*   **Rule**: **FLAG** only if [specific condition].
*   **Reasoning**: [Why this matters.]

<!-- Add more subsections (C, D, E…) as needed. -->

---

## 2. Issue Resolution Rules

<!--
  Rules in this section tell the LLM HOW to fix issues in this file.
  These are applied by the Fixer Agent when generating code patches.

  Each rule should include:
    - **Target**: What code or pattern this rule applies to.
    - **Rule/Constraint**: What the LLM MUST or MUST NOT do.
    - **Reasoning**: Why this constraint exists.
    - **Example** (optional): A code snippet showing the correct fix pattern.
-->

### A. [Topic Name] ([Fix Category])
*   **Target**: `function_name()`, code pattern, or module scope.
*   **Constraint**: **DO NOT** [describe prohibited action].
*   **Reasoning**: [Why this would be harmful.]
*   **Example Fix**:
    ```cpp
    // Correct pattern:
    if (condition) {
        // safe operation
    }
    ```

### B. [Topic Name]
*   **Target**: `pattern` or scope.
*   **Constraint**: **MUST** [describe required behavior].
*   **Reasoning**: [Why this is the correct approach.]

<!-- Add more subsections (C, D, E…) as needed. -->

---

## 3. Issues to Ignore (Quick List)

<!--
  OPTIONAL shorthand section for simple false positives that don't need
  detailed reasoning. The LLM agent reads Section 1 for the detailed rules,
  but this quick list can serve as a summary or as input for an LLM to
  generate a full constraints file.

  Format: One issue per line with the pattern and reason.
-->

<!-- Example entries (uncomment and modify):
- IGNORE: NULL check for `ctx` in `process_event()` — allocated at init, always valid in callbacks.
- IGNORE: Bounds check for `queue_id` in `enqueue_packet()` — hardware-limited to MAX_QUEUES.
- IGNORE: Unused parameter `cookie` in `handle_scan_done()` — required by kernel callback signature.
- IGNORE: "Tainted scalar" for `req->channel` — validated by caller before dispatch.
- FLAG:   Missing free for `skb` in error path of `send_frame()` — genuine resource leak.
-->
