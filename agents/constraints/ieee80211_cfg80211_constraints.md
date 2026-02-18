# Constraints for ieee80211_cfg80211.c

This file defines specific static analysis constraints for the Linux Kernel Interface layer. 
Context: This file bridges the Linux `cfg80211` subsystem and the WLAN driver.

## 1. Issue Identification Rules

### A. Kernel Object Lifecycle (Null Pointer Dereference)
**Rule:** **IGNORE** "Potential null pointer dereference" or "Uninitialized variable" for the following primary kernel objects.
*   **Targets:** `wiphy`, `net_device`, `dev`, `regulatory_request`, `key_params`.
*   **Reasoning:** These objects are allocated and managed by the Linux kernel `cfg80211` subsystem. The kernel guarantees they are valid before invoking any `cfg80211_ops` callback.
*   **Condition:** Ignore ONLY if the variable is passed as a function argument. (Do not ignore if it is a local allocation that could fail).

### B. Driver Context & Private Data
**Rule:** **IGNORE** NULL validation checks for private data accessors.
*   **Targets:** `hdd_context_t`, `hdd_adapter_t`, `hdd_station_ctx_t`.
*   **Patterns:** 
    *   `wiphy_priv(wiphy)`
    *   `netdev_priv(dev)`
    *   `hdd_get_context(...)`
*   **Reasoning:** Driver context is initialized at module load (`probe`). If the interface is active (allowing callbacks), the context is guaranteed to exist. Adding NULL checks here is dead code.

### C. Input Validation (Tainted Data / Tainted Scalar)
**Rule:** **IGNORE** "Tainted data" or "Unvalidated input" warnings for `cfg80211` request parameters under specific conditions.
*   **Targets:** `params`, `req`, `ext_cmd` (data from user-space/Netlink).
*   **Condition:** **IGNORE IF** the code performs a range check or validity check (e.g., `if (val > MAX_VAL) return -EINVAL;`) before use.
*   **Condition:** **IGNORE IF** the data is used as an index for a fixed-size loop (e.g., iterating over `params->n_frequencies` where the loop limit is hardcoded to hardware max).
*   **Reasoning:** The analysis tool often fails to associate the validation check with the subsequent usage.

### D. Array Bounds & Buffer Access
**Rule:** **IGNORE** "Out of bounds access" for hardware capability arrays.
*   **Targets:** `wiphy->bands`, `wiphy->cipher_suites`, `wiphy->n_channels`.
*   **Reasoning:** These arrays are static hardware descriptors populated during the `attach` phase. Indices derived from valid `enum` types (e.g., `NL80211_BAND_*`) are safe by definition.

### E. Unused Parameters (Interface Compliance)
**Rule:** **IGNORE** "Unused parameter" warnings for any function in the `cfg80211_ops` table.
*   **Targets:** `cookie`, `data`, `wiphy` (in specific ops).
*   **Reasoning:** We must strictly adhere to the function pointer signatures defined by the Linux kernel. We cannot remove unused arguments.

### F. Locking & Concurrency
**Rule:** **IGNORE** "Missing lock" suggestions for general configuration paths.
*   **Reasoning:** `cfg80211` operations are serialized by the kernel's **RTNL (Routing Netlink) Lock**. Adding internal driver locks often causes deadlocks (ABBA locking issues).
*   **Exception:** Do NOT ignore if the warning pertains to a purely internal list (e.g., `adapter->peer_list`) that is NOT protected by RTNL.

---

## 2. Issue Resolution Rules

### A. Return Codes & Error Handling
**Rule:** **MUST USE** Standard Linux Kernel error codes.
*   **Constraint:** Return `-EINVAL`, `-ENOMEM`, `-EBUSY`, `-EOPNOTSUPP` directly.
*   **Prohibited:** Do NOT return `QDF_STATUS` or `CDF_STATUS` enums to the kernel.
*   **Refactoring:** If a helper returns `QDF_STATUS`, use `qdf_status_to_os_return()` (or equivalent) to convert it before returning.

### B. Memory Management (DevM vs Standard)
**Rule:** **PREFER** Managed Device Resources (`devm_kzalloc`) for configuration data.
*   **Reasoning:** Ensures automatic cleanup if the driver is unloaded or crashes, preventing leaks in the interface layer.
*   **Constraint:** For `skb` (socket buffer) allocations intended for the firmware/TX path, use standard `qdf_nbuf_alloc` as ownership is transferred.

### C. Switch/Case Fallthrough
**Rule:** **ALLOW** Fallthrough in attribute parsing loops if explicitly marked.
*   **Constraint:** Must use the `fallthrough;` keyword (pseudo-keyword in modern kernels) or `/* fall through */` comment to suppress warnings in large switch-case state machines.

### D. Complex Macros
**Rule:** **DO NOT EXPAND** macros to fix "Complex Function" warnings.
*   **Reasoning:** `cfg80211` code often uses macros like `wlan_hdd_enter()` for tracing. Expanding these makes code unmaintainable.
*   **Fix:** If a function is flagged as "Too Complex" due to huge `switch` statements, extract the `case` logic into helper functions (e.g., `__cfg80211_handle_scan_ssid`).