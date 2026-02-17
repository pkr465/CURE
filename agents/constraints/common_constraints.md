# Linux Kernel & Hardware Driver Constraints

## 1. Issue Identification Rules (WHAT TO IGNORE)
*Use these rules to filter out False Positives during analysis.*

### A. Pointer Validity (Hardware Contexts)
*   **Context**: Variables `soc`, `vdev`, `pdev`, `hif`, `hdd_context`, `cdp_soc` are core driver structures.
*   **Rule**: These are allocated at module init. **IGNORE** missing NULL checks in data paths (TX/RX/ISR).
*   **Exception**: Only flag if the pointer is the result of a *local* `kmalloc`/`kzalloc` in the current scope.

### B. Array Bounds (Hardware/Fixed Sizes)
*   **Context**: Arrays sized by `MAX_CONSTANTS` or hardware registers (e.g., `hw_queues`, `irq_id_map`).
*   **Rule**: **IGNORE** missing bounds checks if the index comes from:
    1. A trusted hardware register.
    2. An internal loop counter up to a fixed limit.
*   **Rule**: **FLAG** bounds checks only if the index comes from user-space (ioctl/sysfs).

### C. Function Pointers
*   **Context**: `ops` structures (e.g., `soc->ops->func`).
*   **Rule**: **IGNORE** missing NULL checks for ops function pointers unless the API is explicitly optional.

---

## 2. Issue Resolution Rules (HOW TO FIX)
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