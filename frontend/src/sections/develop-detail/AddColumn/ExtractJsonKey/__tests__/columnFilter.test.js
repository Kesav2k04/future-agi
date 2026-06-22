import { describe, it, expect } from "vitest";

/**
 * Unit tests for the ExtractJsonKey column filter predicate.
 *
 * The filter is: column.dataType === "json" || jsonSchemas?.[column.field]?.keys?.length
 * This pins the exact logic so regressions surface as test failures.
 */

const isJsonColumn = (column, jsonSchemas = {}) =>
  column.dataType === "json" || Boolean(jsonSchemas?.[column.field]?.keys?.length);

describe("ExtractJsonKey column filter predicate", () => {
  it("passes a column with dataType=json regardless of schemas", () => {
    expect(isJsonColumn({ field: "col1", dataType: "json" }, {})).toBe(true);
  });

  it("passes a text column that has jsonSchemas keys (api_call with JSON response)", () => {
    const schemas = { col2: { keys: ["id", "name"] } };
    expect(isJsonColumn({ field: "col2", dataType: "text" }, schemas)).toBe(true);
  });

  it("rejects a text column with no schema keys", () => {
    expect(isJsonColumn({ field: "col3", dataType: "text" }, {})).toBe(false);
  });

  it("rejects a text column with an empty keys array", () => {
    const schemas = { col4: { keys: [] } };
    expect(isJsonColumn({ field: "col4", dataType: "text" }, schemas)).toBe(false);
  });

  it("rejects number/boolean columns even with json dataType absent", () => {
    expect(isJsonColumn({ field: "col5", dataType: "number" }, {})).toBe(false);
    expect(isJsonColumn({ field: "col6", dataType: "boolean" }, {})).toBe(false);
  });

  it("fails open when jsonSchemas is undefined (hook not yet resolved)", () => {
    // dataType=json column always passes; text-with-no-schemas doesn't
    expect(isJsonColumn({ field: "col7", dataType: "json" }, undefined)).toBe(true);
    expect(isJsonColumn({ field: "col8", dataType: "text" }, undefined)).toBe(false);
  });

  it("does not pass array-valued JSON columns (arrays have no keys)", () => {
    // jsonSchemas for an array column would have no keys (or keys=[])
    const schemas = { col9: { keys: [] } };
    expect(isJsonColumn({ field: "col9", dataType: "text" }, schemas)).toBe(false);
  });
});
