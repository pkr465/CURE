# Guide: Human Feedback & Constraints

**Instructions for Reviewers:** Use the **Feedback** and **Constraints** columns in the Excel report to guide the automated fixing agent.

## Column: Feedback
**Purpose:** To approve, reject, or categorize the issue.

| User Input                    | Effect on Agent                                                                                                          |
| :---------------------------- | :----------------------------------------------------------------------------------------------------------------------- |
| **(Empty Cell)**              | **Approve.** The agent applies the `Fixed_Code` exactly as originally suggested.                                         |
| `Skip` / `Ignore` / `No Fix`  | **Reject.** The agent touches nothing. Use this for false positives.                                                     |
| `Approved` / `LGTM`           | **Approve.** Explicit confirmation (same as leaving it empty).                                                           |
| `Modify` / `Update` / `Retry` | **Custom Fix.** Signals the agent to ignore the original fix and generate a new one based on the **Constraints** column. |

---

## Column: Constraints
**Purpose:** To provide technical guardrails when the original `Fixed_Code` is insufficient or violates project rules.

### Examples of Valid Constraints:

#### A. Memory & Performance
* "Do not use `std::vector`; use a fixed-size array (`std::array`) to avoid heap allocation."
* "Ensure the fix is `noexcept` compliant."
* "Must use `q_malloc` instead of `malloc` for this module."

#### B. Style & Standards
* "Follow C++98 standard only (no `auto` keyword)."
* "Variable names must be `camelCase`, not `snake_case`."
* "Add a comment marked `// TODO: Refactor` above the fix."

#### C. Thread Safety
* "Wrap this logic in `std::lock_guard<std::mutex> lock(g_mutex);`."
* "Do not use `static` variables here due to reentrancy requirements."

#### D. Business Logic
* "If `value < 0`, log an error instead of throwing an exception."
* "Keep the legacy check for `NULL` before dereferencing."

---

## 3. Agent Logic Workflow

This explains how the **AutoFixAgent** interprets the generated JSONL data:

**1. If `action == "FIX"` (Default/Empty Feedback):**
* The agent trusts the original LLM analysis.
* It applies `suggested_fix` directly to the codebase.

**2. If `action == "SKIP"`:**
* The agent ignores the entry entirely.
* The file is left untouched.

**3. If `action == "FIX_WITH_CONSTRAINTS"`:**
* The agent triggers a **Re-generation Step**.
* It takes the `bad_code_snippet` and the `human_constraints`.
* It calls the LLM again to generate a *new* code fix that adheres to the user's specific rules before applying it.