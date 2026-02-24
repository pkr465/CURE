# Constraints for ath12k (Qualcomm Wi-Fi 7 Driver)
#
# SCOPE: Generic constraints for ALL files under drivers/net/wireless/ath/ath12k/
#
# This file can be passed via --include-custom-constraints ath12k_constraints.md
# or placed in agents/constraints/ for automatic pickup when analyzing ath12k sources.

## 1. Issue Identification Rules

### A. Core Driver Object Hierarchy (Null Pointer Dereference)
*   **Context**: ath12k uses a layered object hierarchy that is fully initialized during
    probe/attach and remains valid throughout the driver lifecycle:
    - `ath12k_hw_group` (`ag`) — top-level MLO device group
    - `ath12k_base` (`ab`) — per-SoC/chip device context
    - `ath12k_pdev` — physical radio device within SoC
    - `struct ath12k` (`ar`) — per-radio MAC instance
    - `ath12k_hw` (`ah`) — mac80211 hardware abstraction
*   **Rule**: **IGNORE** "Potential null pointer dereference" for the following when accessed
    as function parameters or via back-pointers from validated parents:
    - `ab`, `ar`, `ah`, `ag`
    - `ar->ab`, `ar->pdev`, `ar->hw`, `ar->ah`
    - `ab->pdevs[*].ar`, `ab->hw_params`, `ab->dev`
    - `ah->hw`, `ag->ab[]`
*   **Reasoning**: These are allocated at PCI probe (`ath12k_pci_probe`) or core init
    (`ath12k_core_init`) and freed only at module unload. If the driver is executing any
    callback, these objects are guaranteed valid.
*   **Exception**: **FLAG** if the pointer is the result of a local `kzalloc`/`devm_kzalloc`
    in the current scope that could return NULL.

### B. QMI, WMI, HTT, and HTC Handles (Null Pointer Dereference)
*   **Context**: Firmware communication handles are established during firmware boot and
    remain valid while the firmware is running.
*   **Rule**: **IGNORE** NULL checks on:
    - `ab->qmi`, `ab->wmi_ab`, `ar->wmi`
    - `ab->htc`, `ab->htt`
    - `wmi_handle`, `wmi_cmd_hdr`
*   **Reasoning**: These are allocated as part of `ath12k_core_qmi_firmware_ready()` and
    are guaranteed present while the device is operational. Redundant NULL checks in WMI
    event handlers and command paths add dead code.

### C. HAL and DP Structures (Null Pointer Dereference / Chained Dereference)
*   **Context**: Hardware Abstraction Layer (HAL) and Data Path (DP) structures are
    allocated during `ath12k_dp_alloc()` and remain valid until `ath12k_dp_free()`.
*   **Rule**: **IGNORE** missing NULL checks on:
    - `ab->hal`, `ab->dp`, `ar->dp`
    - `dp->reo_cmd_ring`, `dp->rx_refill_buf_ring`, `dp->tx_ring[]`
    - `hal_srng`, `hal_ring_id`, `srng->ring_base_vaddr`
    - `rx_desc`, `tx_desc`, `desc_info`, `skb_cb`
    - `dp->rxdma_buf_ring`, `dp->rx_mon_status_refill_ring`
*   **Rule**: **IGNORE** chained dereferences like `ab->hal.srng_list[ring_id]` and
    `dp->tx_ring[ring_id].tcl_data_ring` when the root (`ab` or `dp`) has been validated.
*   **Reasoning**: DP rings and HAL SRNG structures are pre-allocated and mapped to DMA
    memory during initialization. They do not change at runtime.

### D. Pdev Array Access (Array Out of Bounds)
*   **Context**: `ab->pdevs[]` is indexed by `pdev_id` which is bounded by `ab->num_radios`.
    The maximum is `MAX_RADIOS` (typically 3).
*   **Rule**: **IGNORE** array bounds warnings for `ab->pdevs[pdev_id]` when:
    1. `pdev_id` is bounded by a loop: `for (i = 0; i < ab->num_radios; i++)`
    2. `pdev_id` is validated: `if (pdev_id >= ab->num_radios) return;`
    3. `pdev_id` comes from firmware and is validated against `ab->num_radios` before use.
*   **Rule**: **FLAG** if `pdev_id` comes from an unvalidated firmware event field and
    no bounds check is visible in the same function or its direct caller.

### E. Ring ID and Band Index Arrays (Array Out of Bounds)
*   **Rule**: **IGNORE** bounds warnings for the following hardware-bounded arrays:
    - `ab->hal.srng_list[ring_id]` — ring_id bounded by `HAL_SRNG_RING_ID_MAX`
    - `ar->tx_ring[ring_id]` — bounded by `DP_TCL_NUM_RING_MAX`
    - `wiphy->bands[band]` — indexed by `enum nl80211_band` (max 3 values)
    - `ar->vif_list[]`, `ar->peers[]` — bounded by firmware capability limits
    - `ab->ce.ce_pipe[pipe_id]` — bounded by `CE_COUNT_MAX`
*   **Reasoning**: These are hardware-defined limits. The indices come from firmware
    descriptors or kernel enums with known bounds.
*   **Exception**: **FLAG** if the index is derived from user-space (netlink/ioctl) without
    prior range validation.

### F. Copy Engine and Pipe Arrays (Array Out of Bounds)
*   **Rule**: **IGNORE** bounds warnings for Copy Engine pipe arrays:
    - `ab->ce.ce_pipe[ce_id]`, `ce_pipe->src_ring`, `ce_pipe->dest_ring`
    - `ab->hal.ce_pipe[]`
*   **Reasoning**: `ce_id` is always bounded by `ab->hw_params->ce_count` which equals
    the hardware CE pipe count. Loops iterate `for (i = 0; i < ab->hw_params->ce_count; i++)`.

### G. SKB / Network Buffer Handling (Null Pointer / Resource Leak)
*   **Context**: Socket buffers (`sk_buff`, `skb`) follow strict kernel networking conventions.
*   **Rule**: **IGNORE** "potential NULL dereference" on `skb->data`, `skb->head`,
    `skb_cb->paddr` when the `skb` pointer itself has already been validated.
*   **Rule**: **IGNORE** "resource leak for skb" in the following patterns:
    1. `skb` is queued via `skb_queue_tail()`, `ieee80211_rx_napi()`, `netif_receive_skb()`,
       or `ath12k_dp_rx_deliver_msdu()` — ownership is transferred.
    2. `skb` is consumed by `dev_kfree_skb_any()`, `kfree_skb()`, or `consume_skb()`.
    3. `skb` is forwarded to firmware via `ath12k_wmi_cmd_send()` or `ath12k_htc_send()`.
*   **Exception**: **FLAG** genuine leaks where error paths return without freeing/queuing
    the locally allocated `skb`.

### H. RCU-Protected Accesses (Null Pointer Dereference)
*   **Context**: ath12k uses RCU for lock-free read access to peer, vdev, and arvif
    structures in the data path.
*   **Rule**: **IGNORE** missing NULL checks inside RCU read-side critical sections for:
    - `rcu_dereference(ab->pdevs_active[pdev_id])`
    - `rcu_dereference(ar->ab->ag)`
    - Peer lookups via `ath12k_peer_find_*()` family when the result is used within
      `rcu_read_lock()` / `rcu_read_unlock()` scope and the code handles the NULL case.
*   **Rule**: **FLAG** if a pointer obtained via `rcu_dereference()` is used AFTER
    `rcu_read_unlock()` without taking a reference.

### I. Firmware Event Parameter Validation (Tainted Data)
*   **Context**: WMI event handlers receive firmware-originated data that is "trusted"
    within the driver's threat model (firmware is part of the TCB).
*   **Rule**: **IGNORE** "tainted data" or "unvalidated input" warnings for WMI event
    parameters when:
    1. The parameter has an explicit bounds check against a `MAX_*` constant.
    2. The parameter is used as an index with an `if (idx >= MAX) return` guard.
    3. The parameter is an enum value validated by a `switch` statement with a `default` case.
*   **Rule**: **FLAG** if firmware data is passed directly to `copy_to_user()`,
    `nla_put()`, or any user-space facing interface without validation.
*   **Targets**: `ev->pdev_id`, `ev->vdev_id`, `ev->peer_id`, `ev->num_*`, `ev->status`

### J. Spinlock / Mutex Usage in Data Path (Locking)
*   **Context**: ath12k uses a well-defined locking hierarchy:
    - `ab->base_lock` — protects `ab`-level state
    - `ar->data_lock` — protects per-radio data path state
    - `ar->conf_mutex` — protects configuration changes (not used in IRQ context)
    - `dp->reo_cmd_lock` — protects REO command ring
    - `ab->peer_lock` — protects peer list/table
*   **Rule**: **IGNORE** "missing lock" suggestions in interrupt and NAPI handlers. These
    paths use `ar->data_lock` (spinlock) and are called from softirq context where
    mutexes cannot be held.
*   **Rule**: **IGNORE** "potential deadlock" for `ar->conf_mutex` in `ieee80211_ops`
    callbacks — mac80211 guarantees serialization of most ops via `wiphy_lock()`.
*   **Exception**: **FLAG** if a mutex is used inside a path reachable from hardirq context.

### K. Ops Tables and Function Pointers (Null Pointer Dereference)
*   **Rule**: **IGNORE** missing NULL checks for function pointers in ops tables:
    - `ath12k_hif_ops` (HIF transport operations)
    - `ath12k_hw_ops` (hardware-specific operations)
    - `ab->hif.ops->*` (read32, write32, map_service_to_pipe, etc.)
    - `ar->ops->*`
*   **Reasoning**: Ops tables are populated at compile time or at probe and are never
    partially filled. Every entry is required.

### L. Enum-Bounded Switch Statements (Missing Default / Dead Code)
*   **Rule**: **IGNORE** "missing default case" or "not all enum values handled" in
    switch statements over the following ath12k enums:
    - `enum ath12k_hw_rev` — hardware revision
    - `enum ath12k_firmware_mode` — firmware operating mode
    - `enum ath12k_pdev_state` — pdev lifecycle state
    - `enum wmi_phy_mode` — PHY mode (11a/b/g/n/ac/ax/be)
    - `enum hal_rx_mon_status` — monitor mode status
    - `enum ath12k_scan_state` — scan state machine
*   **Reasoning**: These enums have stable values. `default` cases are intentionally omitted
    when all known values are handled, to generate compiler warnings on new additions.

### M. Hardware Register Access (Unchecked Return Value)
*   **Rule**: **IGNORE** "unchecked return value" for:
    - `ath12k_hif_read32()` / `ath12k_hif_write32()` — MMIO register access, no error path
    - `ath12k_hal_srng_access_begin()` — returns void or always succeeds
    - `ath12k_hal_srng_src_get_next_entry()` — NULL means ring full (handled by caller loop)
*   **Reasoning**: Hardware register reads return the register value directly. There is
    no error return to check. These are deliberate (void) usages.

### N. Unused Parameters in Callback Functions (Unused Variable)
*   **Rule**: **IGNORE** "unused parameter" warnings for any function that is assigned to
    a mac80211 `ieee80211_ops`, `cfg80211_ops`, `net_device_ops`, `wiphy_ops`,
    `ath12k_hif_ops`, or debugfs `file_operations` table entry.
*   **Targets**: Common unused params: `hw`, `vif`, `sta`, `cookie`, `flags`, `changed`
*   **Reasoning**: Callback signatures are defined by the kernel subsystem. Parameters
    cannot be removed even when unused in a particular implementation.

### O. `__rcu` and Sparse Annotations (Type Mismatch)
*   **Rule**: **IGNORE** "incompatible pointer types" or "cast removes address space" for
    RCU-annotated pointers (`__rcu`) when:
    1. The access is wrapped in `rcu_dereference()`, `rcu_assign_pointer()`, or `RCU_INIT_POINTER()`.
    2. The code uses `lockdep_assert_held()` before plain dereference.
*   **Reasoning**: `__rcu` is a Sparse annotation for static analysis. The actual pointer
    type is identical at runtime.

### P. Fallthrough in State Machines (Missing Break)
*   **Rule**: **IGNORE** "missing break" or "implicit fallthrough" in WMI event dispatch,
    HTT message parsing, and scan state machine handlers when the `fallthrough;` keyword
    or `/* fall through */` comment is present.
*   **Rule**: **FLAG** fallthrough only if there is no explicit annotation.

---

## 2. Issue Resolution Rules

### A. Error Handling Pattern
*   **Constraint**: **MUST** use `goto` cleanup pattern for error paths in functions with
    multiple resource acquisitions.
*   **Reasoning**: ath12k follows the Linux kernel `goto err_*` convention for unwinding:
    ```c
    ret = ath12k_something();
    if (ret) {
        ath12k_warn(ab, "failed to do something: %d\n", ret);
        goto err_free_skb;
    }
    ```
*   **Constraint**: **DO NOT** use early returns with inline cleanup. This breaks the
    established pattern and risks resource leaks.

### B. Logging Convention
*   **Constraint**: **MUST** use ath12k logging macros, never raw `pr_err` / `printk`:
    - `ath12k_err(ab, ...)` — errors
    - `ath12k_warn(ab, ...)` — warnings
    - `ath12k_info(ab, ...)` — informational
    - `ath12k_dbg(ab, ATH12K_DBG_*, ...)` — debug (with category mask)
*   **Constraint**: **DO NOT** add logging in hot data paths (TX/RX per-packet). Use
    `ath12k_dbg()` with appropriate debug mask if absolutely necessary.

### C. Memory Allocation
*   **Constraint**: **MUST** use `GFP_ATOMIC` in interrupt/softirq context (NAPI handlers,
    tasklets, timer callbacks).
*   **Constraint**: **MUST** use `GFP_KERNEL` in process context (ioctl handlers, probe, ops callbacks).
*   **Constraint**: **DO NOT** use `GFP_KERNEL` inside `spin_lock` / `rcu_read_lock` sections.
*   **Constraint**: **PREFER** `devm_kzalloc()` for probe-time allocations tied to device lifetime.

### D. Locking Rules
*   **Constraint**: **DO NOT** introduce new mutexes in data-path (TX/RX/IRQ) functions.
    Use `spin_lock_bh()` or lock-free patterns (RCU, atomic operations).
*   **Constraint**: **DO NOT** hold `ar->conf_mutex` while calling any function that may
    sleep in softirq context.
*   **Constraint**: **MUST** follow the locking order: `ab->base_lock` → `ar->data_lock`.
    Never acquire `ab->base_lock` while holding `ar->data_lock`.

### E. Firmware Interface (WMI/HTT)
*   **Constraint**: **DO NOT** modify WMI command structures or TLV definitions — these
    are dictated by firmware ABI compatibility.
*   **Constraint**: **MUST** use `ath12k_wmi_tlv_iter()` or `ath12k_wmi_tlv_parse_alloc()`
    for parsing WMI events. Do not manually offset into event buffers.
*   **Constraint**: **MUST** check `skb->len` against minimum expected event size before
    accessing event fields.

### F. Return Codes
*   **Constraint**: **MUST** use standard Linux kernel error codes (`-EINVAL`, `-ENOMEM`,
    `-ENODEV`, `-EOPNOTSUPP`, `-EBUSY`, `-ETIMEDOUT`).
*   **Constraint**: **DO NOT** return custom or QDF-style status codes.

### G. Endianness
*   **Constraint**: **MUST** use `le32_to_cpu()` / `cpu_to_le32()` (and 16/64-bit variants)
    for all firmware and hardware descriptor fields.
*   **Constraint**: **DO NOT** access `__le32` / `__le16` fields with direct casts.
    Use the endian conversion macros.
*   **Reasoning**: ath12k targets both little-endian (x86) and big-endian (some ARM) hosts.

### H. SKB Handling in Fixes
*   **Constraint**: **DO NOT** replace `dev_kfree_skb_any()` with `kfree_skb()` in paths
    that may be called from both process and interrupt context.
*   **Constraint**: **MUST** use `dev_kfree_skb_any()` (or `consume_skb()`) as the default
    SKB free function unless the context is definitively known.
*   **Constraint**: When adding error paths, ensure every allocated `skb` is freed before
    returning. Prefer a `goto err_free_skb:` label pattern.

### I. Code Style
*   **Constraint**: **MUST** follow Linux kernel coding style (tabs, 80-col soft limit,
    K&R braces).
*   **Constraint**: **DO NOT** add `#ifdef` blocks for conditional compilation. Use
    `IS_ENABLED()` or link-time feature selection.
*   **Constraint**: **PREFER** `sizeof(*ptr)` over `sizeof(struct type)` in allocations.
