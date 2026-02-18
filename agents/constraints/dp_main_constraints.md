# Constraints for dp_main.c

## 1. Issue Identification Rules

### A. Memory Operations
*   **Target**: `qdf_mem_copy` / `num_entries++`
*   **Rule**: **IGNORE** missing bounds checks here. The `entries` array is sized to match hardware capabilities. Adding checks impacts ISR latency.

### B. Hardware Enums
*   **Target**: `ase->type` indexing
*   **Rule**: **IGNORE** validation suggestions for `ase->type`. This value is hardware-generated and guaranteed to be within the lookup table range.

### C. Operational Structures
*   **Target**: `soc->cdp_soc.ol_ops` inside `dp_put_multi_pages`
*   **Rule**: **IGNORE** missing NULL check. `ol_ops` is fully populated during the attach phase before interrupts are enabled.

### D. Virtual Devices
*   **Target**: `vdev->wds_ext_enabled`
*   **Rule**: **IGNORE** `vdev` NULL validation. `vdev` validation happens at the entry point of the call stack, not in this leaf function.

### E. Vector Counts
*   **Target**: `msi_vector_count`
*   **Rule**: **IGNORE** validation of `msi_vector_count > 0`. The driver would not have loaded if this was 0.

---

## 2. Issue Resolution Rules

### A. IRQ Map Handling
*   **Target**: `irq_id_map` / `num_irq` assignments
*   **Rule**: **DO NOT** suggest adding `irq_id_map_len` as a function parameter.
*   **Rule**: **DO NOT** wrap assignments `irq_id_map[num_irq++]` in capacity checks. `msi_vector_count` is statically validated against `MAX_IRQ` during bus probe.