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
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
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

		sss := rs.ScopeSpans()
		for j := 0; j < sss.Len(); j++ {
			scope := sss.At(j)
			ss := scope.Spans()
			for k := 0; k < ss.Len(); k++ {
				span := ss.At(k)
				row, err := spanToRow(span, projectID, orgID, serviceName, semconv, resourceAttrs)
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
	projectID, orgID, serviceName, semconv string,
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

	// trace_id / span_id are 16- and 8-byte values in OTel; CH stores them
	// as hex Strings (matches our PG legacy data shape).
	traceID := strings.ToLower(span.TraceID().String())
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

	row := map[string]any{
		"project_id":         coalesceUUID(projectID),
		"observation_type":   observationType,
		"service_name":       serviceName,
		"start_time":         formatDateTime64(startNanos),
		"trace_id":           traceID,
		"id":                 spanID,
		"parent_span_id":     parentID,
		"name":               span.Name(),
		"end_time":           endTime,
		"latency_ms":         latencyMs,
		"org_id":             nullableUUID(orgID),
		"status":             statusString(span.Status().Code()),
		"status_message":     span.Status().Message(),
		"model":              hot.Model,
		"provider":           hot.Provider,
		"gen_ai_system":      hot.GenAISystem,
		"gen_ai_operation":   hot.GenAIOperation,
		"operation_name":     hot.OperationName,
		"prompt_tokens":      hot.PromptTokens,
		"completion_tokens":  hot.CompletionTokens,
		"total_tokens":       hot.TotalTokens,
		"cost":               hot.Cost,
		"attrs_string":       attrsStr,
		"attrs_number":       attrsNum,
		"attrs_bool":         attrsBool,
		"attributes_extra":   overflow,
		"resource_attrs":     resourceAttrs,
		"metadata":           map[string]any{}, // reserved; collectors may inject
		"input":              input,
		"output":             output,
		"tags":               "[]",
		"span_events":        spanEventsJSON(span.Events()),
		"semconv_source":     semconv,
		// _version comes from start_time nanos so newer spans always win
		// the ReplacingMergeTree dedup; matches the adapter.py convention.
		"_version":   uint64(startNanos.UnixNano()),
		"is_deleted": uint8(0),
	}
	return row, nil
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
// key → empty string (CH `input` is `String DEFAULT ''`).
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

func randomUUID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	b[6] = (b[6] & 0x0f) | 0x40 // v4
	b[8] = (b[8] & 0x3f) | 0x80 // RFC 4122
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x",
		b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}
