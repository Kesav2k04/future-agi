// Package clickhouse25exporter converts OTLP spans (pdata) to the row
// shape required by the CH 25.3 `spans` table (see
// futureagi/tracer/services/clickhouse/v2/schema/002_spans_v2.sql).
//
// The converter is deliberately decoupled from the wire layer. The OTLP
// receiver hands us ptrace.Traces; we produce []map[string]any rows that
// the chwriter can serialise as JSONEachRow. Keeping the converter
// stand-alone makes it directly testable from `go test` without a CH
// dependency.
package clickhouse25exporter

import (
	"crypto/rand"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/adapter"
	"github.com/future-agi/future-agi/fi-collector/pkg/detid"
	"github.com/google/uuid"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

// OTLP attribute keys for the CH-derived `end_users` / `trace_sessions`
// dimensions (P3b step2). These mirror the EXACT keys the Django ingest path
// reads — span attributes `user.id` / `user.id.type` / `session.id`
// (futureagi/tracer/utils/otel.py SpanAttributes; the fi_instrumentation SDK
// `using_user` / `using_session` set them as SPAN attributes) and the resource
// attribute `project_type` (the SDK sets it on the Resource;
// futureagi/tracer/utils/trace_ingestion.py `_parse_otel_request` reads it).
// Django reads these literal keys directly — NOT via AttributeRegistry
// aliasing (that path only covers span_kind/model/provider/tokens) — so the
// collector must read these exact literals and nothing else.
const (
	attrUserID       = "user.id"
	attrUserIDType   = "user.id.type"
	attrSessionID    = "session.id"
	resAttrProjectTy = "project_type"

	// projectTypeObserve is ProjectType.OBSERVE.value from the SDK
	// (fi_instrumentation fi_types.ProjectType). The Django end_user stamp is
	// gated on `project.trace_type == "observe"`; the collector mirrors that
	// using the resource `project_type` hint, which carries this value.
	projectTypeObserve = "observe"
)

// Convert walks an OTLP Traces payload and returns one map per span. Maps
// are designed for JSONEachRow encoding: all values are JSON-natural types
// (string, float64, bool, nil, slice, map). UUID-typed CH columns receive
// canonical 36-char strings; CH parses those on insert.
//
// Returns rows or an error. We DO NOT silently drop malformed spans; the
// caller decides retry/dead-letter policy.
func Convert(traces ptrace.Traces) ([]map[string]any, error) {
	rows := make([]map[string]any, 0, traces.SpanCount())

	rss := traces.ResourceSpans()
	for i := 0; i < rss.Len(); i++ {
		rs := rss.At(i)
		resourceAttrs := flattenAttrs(rs.Resource().Attributes())
		serviceName := stringAttr(rs.Resource().Attributes(), "service.name", "")
		projectID := stringAttr(rs.Resource().Attributes(), "fi.project_id", "")
		orgID := stringAttr(rs.Resource().Attributes(), "fi.org_id", "")
		// `fi.semconv` lets producers tag which semantic convention they
		// emitted (openinference / openllmetry / langfuse / fi_native /
		// otel_genai). Used downstream for filtering and debugging.
		semconv := stringAttr(rs.Resource().Attributes(), "fi.semconv", "")
		// `project_type` ("observe" / "experiment") is the SDK-set resource
		// attribute (fi_instrumentation). Used ONLY to gate the deterministic
		// end_user_id stamp, mirroring Django's `project.trace_type ==
		// "observe"` gate (create_otel_span.py). Empty when the producer
		// didn't tag it (legacy SDKs) — see the gating note in spanToRow.
		projectType := stringAttr(rs.Resource().Attributes(), resAttrProjectTy, "")

		sss := rs.ScopeSpans()
		for j := 0; j < sss.Len(); j++ {
			scope := sss.At(j)
			ss := scope.Spans()
			for k := 0; k < ss.Len(); k++ {
				span := ss.At(k)
				row, err := spanToRow(span, projectID, orgID, serviceName, semconv, projectType, resourceAttrs)
				if err != nil {
					return nil, fmt.Errorf("span %s: %w", span.SpanID().String(), err)
				}
				rows = append(rows, row)
			}
		}
	}
	return rows, nil
}

// spanToRow does the per-span conversion. Keeping this in one function
// makes it grep-friendly when a column is added: search for the column name
// and you find the one place it's populated.
func spanToRow(
	span ptrace.Span,
	projectID, orgID, serviceName, semconv, projectType string,
	resourceAttrs map[string]any,
) (map[string]any, error) {
	// Pre-allocate destination maps. Sizing is a heuristic — typical LLM
	// spans have 20-50 attrs, but customer-instrumented spans run smaller.
	attrsStr := make(map[string]string, 16)
	attrsNum := make(map[string]float64, 8)
	attrsBool := make(map[string]uint8, 4)
	overflow := make(map[string]any, 4)

	adapter.Split(span.Attributes(), attrsStr, attrsNum, attrsBool, overflow)
	hot := adapter.DeriveHotKeys(attrsStr, attrsNum)

	startNanos := span.StartTimestamp().AsTime()
	endNanos := span.EndTimestamp().AsTime()
	var endTime any
	var latencyMs int32
	if !endNanos.IsZero() {
		endTime = formatDateTime64(endNanos)
		// CH 25.3 stores latency_ms as Int32, capping at ~24.8 days.
		// Clamp defensively — a 25-day span is almost certainly corrupt
		// (forgot to call Finish()) and we'd rather log a max value than
		// overflow silently.
		ms := endNanos.Sub(startNanos).Milliseconds()
		if ms < 0 {
			ms = 0
		} else if ms > int64(^uint32(0)>>1) {
			ms = int64(^uint32(0) >> 1)
		}
		latencyMs = int32(ms)
	}

	// trace_id is the 16-byte OTel value, but PG `tracer_trace.id` is a UUID
	// and the migration backfill lands it as the 36-char DASHED uuid string in
	// `spans`/`traces`. We must match that exactly: live spans have to join the
	// backfilled history on trace_id, and spans.trace_name resolves the trace
	// name via toUUID(trace_id) against trace_dict (v2 schema 015) — toUUID()
	// only parses the dashed form. `span.TraceID().String()` emits 32-char hex
	// (no dashes), so we format the bytes as a dashed UUID instead.
	//
	// span_id / parent_span_id are 8-byte values stored as 16-char hex — that
	// already matches PG `tracer_observation_span.id`, so leave them as-is.
	traceID := traceIDToUUIDString(span.TraceID())
	spanID := strings.ToLower(span.SpanID().String())
	parentID := ""
	if !span.ParentSpanID().IsEmpty() {
		parentID = strings.ToLower(span.ParentSpanID().String())
	}

	// observation_type: prefer the OTel-GenAI `gen_ai.operation.name`
	// (chat / embedding / completion) when present; fall back to the
	// SDK-provided `openinference.span.kind` (LLM / CHAIN / TOOL); else
	// generic span kind. Matches the legacy adapter behaviour.
	observationType := strings.ToUpper(attrsStr["openinference.span.kind"])
	if observationType == "" {
		observationType = strings.ToUpper(attrsStr["fi.span.kind"])
	}
	if observationType == "" {
		observationType = "SPAN"
	}

	// Inputs/outputs: extracted from openinference convention if present.
	// `input.value` / `output.value` route to the overflow tier (per
	// adapter.overflowKeyPrefixes — they're often nested objects whose
	// shape varies row-to-row), so we lift them from `overflow` rather
	// than `attrsStr`. The hot string columns are populated with the
	// serialized form when the value is a plain string; nested values
	// stay in attributes_extra and dashboards query them from there.
	input := overflowAsString(overflow, "input.value")
	output := overflowAsString(overflow, "output.value")

	// CH-derived dimensions (P3b step2): stamp the DETERMINISTIC end_user_id /
	// trace_session_id so collector-written spans unify with the read-side
	// remap WITHOUT a hot-path PG get_or_create. Byte-exact mirror of the
	// Django stamp (futureagi/tracer/utils/create_otel_span.py +
	// services/clickhouse/v2/deterministic_id.py). Both columns are
	// Nullable(UUID) (schema 002_spans_v2.sql) — nil when the gating signal is
	// absent, so JSONEachRow lands a SQL NULL.
	endUserID := deriveEndUserID(span.Attributes(), projectID, orgID, projectType)
	traceSessionID := deriveTraceSessionID(span.Attributes(), projectID)

	row := map[string]any{
		"project_id":        coalesceUUID(projectID),
		"observation_type":  observationType,
		"service_name":      serviceName,
		"start_time":        formatDateTime64(startNanos),
		"trace_id":          traceID,
		"id":                spanID,
		"parent_span_id":    parentID,
		"name":              span.Name(),
		"end_time":          endTime,
		"latency_ms":        latencyMs,
		"org_id":            nullableUUID(orgID),
		"end_user_id":       endUserID,      // Nullable(UUID); nil → SQL NULL
		"trace_session_id":  traceSessionID, // Nullable(UUID); nil → SQL NULL
		"status":            statusString(span.Status().Code()),
		"status_message":    span.Status().Message(),
		"model":             hot.Model,
		"provider":          hot.Provider,
		"gen_ai_system":     hot.GenAISystem,
		"gen_ai_operation":  hot.GenAIOperation,
		"operation_name":    hot.OperationName,
		"prompt_tokens":     hot.PromptTokens,
		"completion_tokens": hot.CompletionTokens,
		"total_tokens":      hot.TotalTokens,
		"cost":              hot.Cost,
		"attrs_string":      attrsStr,
		"attrs_number":      attrsNum,
		"attrs_bool":        attrsBool,
		"attributes_extra":  overflow,
		"resource_attrs":    resourceAttrs,
		"metadata":          map[string]any{}, // reserved; collectors may inject
		"input":             input,
		"output":            output,
		"tags":              "[]",
		"span_events":       spanEventsJSON(span.Events()),
		"semconv_source":    semconv,
		// _version comes from start_time nanos so newer spans always win
		// the ReplacingMergeTree dedup; matches the adapter.py convention.
		"_version":   uint64(startNanos.UnixNano()),
		"is_deleted": uint8(0),
	}
	return row, nil
}

// deriveEndUserID computes the deterministic end_user_id for a span, or
// returns nil (→ SQL NULL) when the identity isn't stampable. It is a
// byte-exact mirror of the Django stamp in create_otel_span.py:
//
//	if attributes.get(USER_ID):                              # truthy user.id
//	    end_user = {"user_id": ..., "user_id_type": get_user_id_type(...)}
//	if end_user["user_id"] is not None and project.trace_type == "observe":
//	    eu_id = deterministic_end_user_id(project.id, org_id,
//	                                      user_id, user_id_type)
//
// Gating, in order:
//   - project_type must be "observe" (mirrors `project.trace_type ==
//     "observe"`). The collector has no PG, so it uses the SDK-set resource
//     `project_type` hint. fi_instrumentation always sets it; when a non-FI
//     producer omits it we conservatively DO NOT stamp (NULL) — never stamp an
//     end_user onto a non-observe / unknown project, matching Django's intent.
//   - project_id and org_id must be real (the converter's coalesceUUID random
//     fallback must NOT seed an id; we check the raw projectID/orgID here).
//   - user.id must be present AND truthy (mirrors Python's `if
//     attributes.get(USER_ID):` — empty-string / numeric-zero are falsy and
//     produce no end_user).
//
// The user.id value is coerced with AsString() so a string and a numeric
// user.id both match Python's f-string str()-coercion (and a numeric id never
// loses precision via the float64 attrs_number tier). user.id.type is
// normalized + sentinel-mapped exactly like Python — see normalizeUserIDType.
func deriveEndUserID(attrs pcommon.Map, projectID, orgID, projectType string) any {
	if projectType != projectTypeObserve {
		return nil
	}
	// Canonicalize project_id / org_id to the lowercase-dashed form Python's
	// str(uuid.UUID) / PG's UUID text produced when the FROZEN ids were
	// derived. fi.project_id / fi.org_id are already that shape in practice
	// (the gateway sets them from the PG UUID), so this is a no-op there — but
	// it makes the key byte-match the contract even if a producer sent an
	// uppercase / braced form, and refuses to stamp a malformed-key id.
	pid, ok := canonicalUUID(projectID)
	if !ok {
		return nil
	}
	oid, ok := canonicalUUID(orgID)
	if !ok {
		return nil
	}
	v, ok := attrs.Get(attrUserID)
	if !ok || !isTruthyAttr(v) {
		return nil
	}
	userID := v.AsString()
	userIDType := normalizeUserIDType(attrs)
	return detid.EndUserID(pid, oid, userID, userIDType).String()
}

// deriveTraceSessionID computes the deterministic trace_session_id for a span,
// or nil (→ SQL NULL) when no session is present. Mirrors the Django stamp:
//
//	session_name = attributes.get(SESSION_ID)
//	if session_name is not None:
//	    ts_id = deterministic_trace_session_id(project.id, session_name)
//
// Gating mirrors Python's `session_name is not None` on a bare `.get`: a
// PRESENT session.id (even empty-string "") stamps; an ABSENT one does not.
// So we use presence (comma-ok), NOT truthiness — distinct from the end_user
// gate above. project_id must be real (no coalesceUUID random fallback).
//
// Note: there is NO project_type gate here — Django stamps the session for
// any project type (only the end_user stamp is observe-gated). The session
// name is coerced with AsString() to match Python's f-string str().
func deriveTraceSessionID(attrs pcommon.Map, projectID string) any {
	pid, ok := canonicalUUID(projectID)
	if !ok {
		return nil
	}
	v, ok := attrs.Get(attrSessionID)
	if !ok {
		return nil
	}
	name := v.AsString()
	return detid.TraceSessionID(pid, name).String()
}

// canonicalUUID parses s and re-emits the canonical lowercase-dashed UUID
// string — the exact form Python's str(uuid.UUID) and PG's UUID text column
// produced when the frozen deterministic ids were derived. Returns ok=false
// for an empty or unparseable value so the caller declines to stamp (a NULL
// dimension is backfillable; a malformed-key id is corruption). A
// already-canonical input round-trips unchanged.
func canonicalUUID(s string) (string, bool) {
	if s == "" {
		return "", false
	}
	u, err := uuid.Parse(s)
	if err != nil {
		return "", false
	}
	return u.String(), true
}

// normalizeUserIDType mirrors futureagi/tracer/utils/otel.py get_user_id_type
// COMPOSED WITH deterministic_id.py's `user_id_type or ""` sentinel, returning
// the EXACT token that goes into the end_user_id key string:
//
//	get_user_id_type(None)        -> None  ; then `None or ""`  -> ""
//	get_user_id_type("")          -> "custom" (""!=None → case _) ; stays "custom"
//	get_user_id_type("email")     -> "email"
//	get_user_id_type("phone")     -> "phone"
//	get_user_id_type("uuid")      -> "uuid"
//	get_user_id_type("anything")  -> "custom"
//
// The ABSENT-vs-PRESENT-empty distinction is load-bearing: an ABSENT
// user.id.type → "" sentinel (consolidates with NULL-type history), but a
// PRESENT empty-string user.id.type → "custom". We therefore branch on the
// comma-ok presence flag, never on Go's zero-value "".
func normalizeUserIDType(attrs pcommon.Map) string {
	v, ok := attrs.Get(attrUserIDType)
	if !ok {
		// Absent → Python None → `None or ""` sentinel.
		return ""
	}
	raw := v.AsString()
	switch raw {
	case "email":
		return "email"
	case "phone":
		return "phone"
	case "uuid":
		return "uuid"
	default:
		// Present but not a known type (INCLUDING the empty string) → "custom".
		// "custom" is truthy so Python's `or ""` leaves it unchanged.
		return "custom"
	}
}

// isTruthyAttr mirrors Python truthiness of `attributes.get(USER_ID)` for the
// end_user extraction gate (`if attributes.get(USER_ID):`). A string is truthy
// iff non-empty; a number iff non-zero; a bool iff true; everything else
// (maps/slices/bytes) iff its string form is non-empty. Empty / zero / unset
// values are falsy and must NOT seed an end_user.
func isTruthyAttr(v pcommon.Value) bool {
	switch v.Type() {
	case pcommon.ValueTypeStr:
		return v.Str() != ""
	case pcommon.ValueTypeInt:
		return v.Int() != 0
	case pcommon.ValueTypeDouble:
		return v.Double() != 0
	case pcommon.ValueTypeBool:
		return v.Bool()
	case pcommon.ValueTypeEmpty:
		return false
	default:
		return v.AsString() != ""
	}
}

// flattenAttrs converts a pcommon.Map into a plain map[string]any. Resource
// attribute values are simple — strings, ints, bools — so this isn't the
// hot path that adapter.Split is.
func flattenAttrs(m pcommon.Map) map[string]any {
	out := make(map[string]any, m.Len())
	m.Range(func(k string, v pcommon.Value) bool {
		switch v.Type() {
		case pcommon.ValueTypeStr:
			out[k] = v.Str()
		case pcommon.ValueTypeBool:
			out[k] = v.Bool()
		case pcommon.ValueTypeInt:
			out[k] = v.Int()
		case pcommon.ValueTypeDouble:
			out[k] = v.Double()
		default:
			out[k] = v.AsString()
		}
		return true
	})
	return out
}

func stringAttr(m pcommon.Map, key, def string) string {
	v, ok := m.Get(key)
	if !ok {
		return def
	}
	if v.Type() == pcommon.ValueTypeStr {
		return v.Str()
	}
	return v.AsString()
}

func stringFromMap(m map[string]string, key string) string {
	if v, ok := m[key]; ok {
		return v
	}
	return ""
}

// overflowAsString lifts the value at `key` from the overflow map and
// returns its string form. Plain strings pass through; nested objects get
// JSON-encoded so the hot column still holds something useful. Missing
// key → empty string (CH `input` is `String DEFAULT ”`).
func overflowAsString(overflow map[string]any, key string) string {
	v, ok := overflow[key]
	if !ok || v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	b, err := json.Marshal(v)
	if err != nil {
		return ""
	}
	return string(b)
}

// statusString maps OTel StatusCode → CH `status` LowCardinality strings.
// Keeps the existing PG enum values so dashboards continue to work.
func statusString(c ptrace.StatusCode) string {
	switch c {
	case ptrace.StatusCodeOk:
		return "OK"
	case ptrace.StatusCodeError:
		return "ERROR"
	default:
		return "UNSET"
	}
}

// spanEventsJSON serialises events as a JSON array. We hand back a JSON
// string (not []any) because the CH `span_events String` column stores it
// verbatim — saves a re-serialise on the writer side.
func spanEventsJSON(events ptrace.SpanEventSlice) string {
	if events.Len() == 0 {
		return "[]"
	}
	var b strings.Builder
	b.WriteByte('[')
	for i := 0; i < events.Len(); i++ {
		if i > 0 {
			b.WriteByte(',')
		}
		ev := events.At(i)
		b.WriteByte('{')
		fmt.Fprintf(&b, `"name":%q,"timestamp":%q`,
			ev.Name(), formatDateTime64(ev.Timestamp().AsTime()))
		b.WriteByte('}')
	}
	b.WriteByte(']')
	return b.String()
}

// formatDateTime64 emits CH's DateTime64(6) text form: "YYYY-MM-DD HH:MM:SS.ffffff".
// JSONEachRow accepts this verbatim for DateTime64 columns; we avoid the
// nanosecond suffix (CH rejects 9-digit fractional seconds for (6)).
func formatDateTime64(t time.Time) string {
	return t.UTC().Format("2006-01-02 15:04:05.000000")
}

// coalesceUUID returns a valid UUID. If `s` is empty we emit a random one
// because `project_id UUID` is non-nullable. In production every span must
// have a tagged project; this fallback exists for SDK-misconfigured cases
// so the writer doesn't drop the row entirely.
func coalesceUUID(s string) string {
	if s == "" {
		return randomUUID()
	}
	return s
}

// nullableUUID returns nil when empty so CH's JSONEachRow parser handles
// the Nullable column correctly. (An empty string would fail parsing.)
func nullableUUID(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// traceIDToUUIDString formats an OTel 16-byte trace id as the canonical
// 36-char dashed UUID (8-4-4-4-12), matching PG `tracer_trace.id` and the
// migration backfill. Uses the same byte-formatting idiom as randomUUID.
// An empty/zero trace id yields the all-zero UUID string; the caller's
// upstream validation rejects spans without a trace before we get here.
func traceIDToUUIDString(t pcommon.TraceID) string {
	b := t // pcommon.TraceID is [16]byte
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x",
		b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}

func randomUUID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	b[6] = (b[6] & 0x0f) | 0x40 // v4
	b[8] = (b[8] & 0x3f) | 0x80 // RFC 4122
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x",
		b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}
