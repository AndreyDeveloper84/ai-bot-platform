/**
 * Compatibility re-export.
 *
 * DRF-2938 promoted the operational state grammar to a shared component so
 * Master, Salon and Client surfaces can reuse one semantic renderer without
 * duplicating copy or recovery rules.
 */
export * from "../OperationalSystemState";
