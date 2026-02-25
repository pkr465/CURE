# Linux Kernel & Hardware Driver Constraints

## 1. Issue Identification Rules
*Use these rules to filter out false positives during analysis.*

### A. Pointer Validity (Hardware Contexts)
*   **Context**: Variables `soc`, `vdev`, `pdev`, `hif`, `hdd_context`, `cdp_soc` are core driver structures.
*   **Rule**: These are allocated at module init. **IGNORE** missing NULL checks in data paths (TX/RX/ISR).
*   **Exception**: Only flag if the pointer is the result of a *local* `kmalloc`/`kzalloc` in the current scope.

### B. Single-Point NULL Validation (Embedded Architecture)
*   **Context**: This codebase follows a **single-point validation** architecture. Pointer parameters
    are validated once at the public API boundary and are then considered valid for the remainder of
    the call chain. Adding redundant NULL checks deep in the call stack adds latency and code bloat
    that is unacceptable in real-time embedded paths.
*   **Rule**: **IGNORE** missing NULL checks on pointer parameters in internal/static/helper functions
    when any of the following hold:
    1. The function is `static` (file-internal, only called from validated contexts).
    2. The function name begins with `_` or `__` (private/internal convention).
    3. The pointer is a struct member accessed via an already-dereferenced parent
       (e.g., `soc->pdev->ops` — if `soc` was dereferenced, `pdev` was already validated).
    4. The pointer is passed down from a caller that has already been analyzed in the same file.
*   **Rule**: **FLAG** missing NULL checks ONLY when:
    1. The pointer comes directly from a dynamic allocation in the current scope
       (`malloc`, `calloc`, `realloc`, `kzalloc`, `kmalloc`, `devm_kzalloc`, `vzalloc`).
    2. The pointer is returned from a function explicitly documented or prototyped as
       returning NULL on failure (e.g., `find_device()`, `lookup_entry()`).
    3. The pointer comes from user-space input (`copy_from_user`, `get_user`, ioctl data).
*   **Rule**: When flagging a potential NULL dereference, set Confidence to **POSSIBLE** (not CERTAIN)
    unless you can prove no validation exists anywhere in the visible code or constraints.

### C. Hardware-Initialized Structures
*   **Context**: Structures that are filled or populated by hardware layers, firmware, or boot-time
    initialization are **guaranteed valid** throughout the driver lifecycle. These include:
    1. **Device context structs**: `pdev`, `soc`, `hif_ctx`, `wmi_handle`, `htc_handle`,
       `cdp_soc`, `hdd_ctx`, `dp_soc`, `dp_pdev`, `dp_vdev`, `dp_peer`.
    2. **Hardware descriptor structs**: Structs obtained from DMA rings, completion rings,
       or hardware descriptor pools (e.g., `rx_desc`, `tx_desc`, `ring_entry`, `hal_soc`).
    3. **Configuration structs**: Structs populated during probe/attach and read-only thereafter
       (e.g., `target_info`, `tgt_cfg`, `hw_params`, `dev_config`).
*   **Rule**: **IGNORE** NULL checks on all of the above. These are never NULL in the data path.

### D. Array Bounds (Hardware/Fixed Sizes)
*   **Context**: Arrays sized by `MAX_CONSTANTS` or hardware registers (e.g., `hw_queues`, `irq_id_map`).
*   **Rule**: **IGNORE** missing bounds checks if the index comes from:
    1. A trusted hardware register.
    2. An internal loop counter up to a fixed limit.
    3. An enum value where the enum's MAX matches the array size.
*   **Rule**: **FLAG** bounds checks only if the index comes from user-space (ioctl/sysfs).

### E. Function Pointers & Ops Tables
*   **Context**: `ops` structures (e.g., `soc->ops->func`, `vdev->osif_ops->tx`).
*   **Rule**: **IGNORE** missing NULL checks for ops function pointers. Ops tables are populated
    at init time and are structurally guaranteed to be complete.
*   **Exception**: Flag only if the ops table is explicitly documented as supporting optional callbacks.

### F. Chained Dereference Patterns
*   **Context**: Embedded drivers frequently chain multiple dereferences in a single expression
    (e.g., `soc->pdev->hif_ctx->ce_info[ring_id]`).
*   **Rule**: If the root pointer (`soc`) has been validated or is in the hardware-initialized
    category (Section C), **IGNORE** NULL checks on all intermediate pointers in the chain.
    The chain is only as strong as its root — and in embedded code, the root is validated at entry.

### G. Defensive Programming (Not a Bug)
*   **IGNORE**: Extra null checks, multi-layer bounds validation, error checks on low-failure ops, switch default branches
*   **DO NOT** suggest removing defensive checks
*   **DO NOT** flag issues based on speculation about future code changes

---

## 2. Issue Resolution Rules
*Use these rules when generating code fixes.*

### A. Performance & Flow (Critical Constraints)
*   **Locking**: **DO NOT** introduce locking (mutex/spinlocks) in Interrupt Service Routines (ISR) or hot paths unless explicitly requested.
*   **Signatures**: **DO NOT** change function signatures in public headers (e.g., adding `size_t len`) for globally defined fixed-size arrays. Fixes must be contained within implementation (`.c`/`.cpp`) bodies.
*   **Memory Copy (Linter Override QCT001)**: 
    *   **Constraint**: **DO NOT** replace `std::memcpy`, `memcpy`, or `memmove` with `memscpy`, `memcpy_s`, or secure API wrappers in data paths (Budget: < 5μs per packet).
    *   **Required Action**: **RETAIN** `memcpy`. Ensure bounds safety via **explicit pre-validation** using standard C/C++ logic.
    *   **Example Fix**:
        ```cpp
        // ✅ ALLOWED: Manual check + memcpy
        if (copy_len > out_size) {
            // Handle error (log, truncate, or return error code)
            return ERR_BUFFER_OVERFLOW;
        }
        std::memcpy(out, in.data(), copy_len);
        ```

### B. Pointer & Type Usage (Linter Override QCT055/SYS009)
*   **Smart Pointers**: **RESTRICTED**. Do not introduce `std::shared_ptr` in `framework/platform/bpl` or kernel-adjacent code. Use `std::unique_ptr` only if zero-overhead can be guaranteed.
*   **Initialization**: **CONTEXT AWARE**. Ensure variable initialization (to `0`/`nullptr`) does not occur inside a hot loop unless absolutely necessary.

### C. Error Handling
*   **Defensive Coding**: In `void` functions, prefer `return;` over complex error handling if a trusted pointer is unexpectedly NULL. Do not clutter code with logs.
*   **Return Codes**: Use `QDF_STATUS` or kernel standard error codes (`-EINVAL`, `-ENOMEM`) rather than generic integers.
*   **Comments**: If a security rule is suppressed or solved via manual checks (like the `memcpy` case), add a comment explaining why:
    ```cpp
    // INTENTIONAL: Using memcpy for performance. Bounds checked above.
    std::memcpy(out, in, len);
    ```

### D. General Code Integrity
*   **Dependencies**: **DO NOT** import new external libraries (e.g., Boost, Abseil) to solve syntax issues. Use only the Standard Template Library (STL) or existing project utilities.

### E. Code Integrity Rules (LLM Fix Generation — Compilation Safety)
*These rules prevent the most common compilation-breaking errors in LLM-generated fixes.*

*   **E.1 — Blank Line Preservation**:
    - Blank lines around closing braces `}` are **CRITICAL** for compilation and readability.
    - If the original code has `}\n\nvoid foo()`, the fixed code **MUST** maintain this spacing.
    - **DO NOT** merge lines by removing newlines between functions, blocks, or after closing braces.
    - Merging `}` with the next statement on the same line causes parsing/syntax errors.

*   **E.2 — Function Signature Consistency**:
    - When modifying a function CALL (e.g., adding an argument), the function DEFINITION **MUST** also be updated.
    - Argument counts in calls **MUST** match argument counts in definitions.
    - **DO NOT** add extra parameters to function calls without changing the corresponding definition.
    - If the function definition is not visible in the current chunk, **DO NOT** change the call arguments.

*   **E.3 — Variable Type Declaration Preservation**:
    - For-loop variable declarations **MUST** retain their type: `for (int i = 0; i < N; i++)`
    - **DO NOT** transform `for (int i = 1; i < X; i++)` to `for (i = (A_UINT32)1; i < X; i++)`
    - Removing the type declaration from a for-loop initializer causes "undeclared identifier" errors.
    - **DO NOT** add unnecessary type casts to integer literals — rely on C/C++ implicit promotion.

*   **E.4 — Macro Availability Constraints**:
    - Use only macros that are **defined** in the current file or explicitly included headers.
    - For array sizing, **prefer** the standard C/C++ idiom: `sizeof(arr) / sizeof(arr[0])`
    - **DO NOT** use `A_ARRAY_SIZE(arr)` or `ARRAY_SIZE(arr)` unless visible in the code or context.
    - Application-specific macros (A_\*, QDF_\*, OL_\*) may not be visible in this translation unit.

*   **E.5 — Macro Argument Count Validation**:
    - `A_COMPILE_TIME_ASSERT` requires exactly **2 arguments**: `A_COMPILE_TIME_ASSERT(condition, message_string)`
    - **WRONG**: `A_COMPILE_TIME_ASSERT(sizeof(x) == 4)` — missing second argument.
    - **CORRECT**: `A_COMPILE_TIME_ASSERT(sizeof(x) == 4, "size check failed")`
    - When in doubt, use standard C++11 `static_assert(condition, "message")` instead.
    - Before using ANY assertion macro, verify its expected argument count from visible definitions.