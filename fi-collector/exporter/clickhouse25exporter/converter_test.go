package clickhouse25exporter

import (
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

// buildOTLPSpan constructs a minimal but representative LLM span: GenAI
// semconv attributes (so the hot-key derivation fires), an OpenInference
// span.kind tag, and a few overflow-class attributes.
func buildOTLPSpan() ptrace.Traces {
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("service.name", "my-llm-app")
	rs.Resource().Attributes().PutStr("fi.project_id", "11111111-1111-4111-8111-111111111111")
	rs.Resource().Attributes().PutStr("fi.org_id", "22222222-2222-4222-8222-222222222222")
	rs.Resource().Attributes().PutStr("fi.semconv", "openinference")

	ss := rs.ScopeSpans().AppendEmpty()
	sp := ss.Spans().AppendEmpty()
	tid := [16]byte{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16}
	sid := [8]byte{9, 8, 7, 6, 5, 4, 3, 2}
	sp.SetTraceID(tid)
	sp.SetSpanID(sid)
	sp.SetName("llm.chat.completion")
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Unix(1700000000, 0)))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Unix(1700000001, 500_000_000)))
	sp.Status().SetCode(ptrace.StatusCodeOk)
	a := sp.Attributes()
	a.PutStr("openinference.span.kind", "LLM")
	a.PutStr("gen_ai.system", "openai")
	a.PutStr("gen_ai.request.model", "gpt-4o-mini")
	a.PutStr("gen_ai.operation.name", "chat")
	a.PutInt("gen_ai.usage.input_tokens", 120)
	a.PutInt("gen_ai.usage.output_tokens", 38)
	a.PutInt("gen_ai.usage.total_tokens", 158)
	a.PutStr("input.value", "Hello, world!")
	a.PutStr("output.value", "Hi there.")
	// Goes to overflow because key starts with llm.prompt
	a.PutStr("llm.prompt.template", "{question}")
	// Goes to attrs_bool
	a.PutBool("user.is_premium", true)
	return traces
}

func TestConvertMinimalGenAISpan(t *testing.T) {
	rows, err := Convert(buildOTLPSpan())
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("rows=%d want 1", len(rows))
	}
	r := rows[0]

	expect := map[string]any{
		"project_id":         "11111111-1111-4111-8111-111111111111",
		"observation_type":   "LLM",
		"service_name":       "my-llm-app",
		"trace_id":           "0102030405060708090a0b0c0d0e0f10",
		"id":                 "0908070605040302",
		"name":               "llm.chat.completion",
		"latency_ms":         int32(1500),
		"status":             "OK",
		"model":              "gpt-4o-mini",
		"provider":           "openai",
		"gen_ai_system":      "openai",
		"gen_ai_operation":   "chat",
		"prompt_tokens":      int32(120),
		"completion_tokens":  int32(38),
		"total_tokens":       int32(158),
		"input":              "Hello, world!",
		"output":             "Hi there.",
		"semconv_source":     "openinference",
		"is_deleted":         uint8(0),
	}
	for k, want := range expect {
		got, ok := r[k]
		if !ok {
			t.Errorf("missing key %q", k)
			continue
		}
		if got != want {
			t.Errorf("%s: got %#v want %#v", k, got, want)
		}
	}

	// attrs_string must contain the GenAI scalar attributes (not the
	// overflow ones) — sanity-check shape.
	as := r["attrs_string"].(map[string]string)
	if as["gen_ai.request.model"] != "gpt-4o-mini" {
		t.Errorf("attrs_string missing gen_ai.request.model: %#v", as)
	}
	if _, present := as["llm.prompt.template"]; present {
		t.Errorf("llm.prompt.* should overflow, not land in attrs_string")
	}
	// attrs_bool: user.is_premium=true → 1
	ab := r["attrs_bool"].(map[string]uint8)
	if ab["user.is_premium"] != 1 {
		t.Errorf("attrs_bool[user.is_premium]=%d want 1", ab["user.is_premium"])
	}
	// overflow: llm.prompt.template
	of := r["attributes_extra"].(map[string]any)
	if _, ok := of["llm.prompt.template"]; !ok {
		t.Errorf("overflow missing llm.prompt.template: %#v", of)
	}

	// start_time must use CH DateTime64(6) text shape.
	st := r["start_time"].(string)
	if !strings.HasPrefix(st, "2023-11-14 22:13:20") {
		t.Errorf("start_time format: got %q", st)
	}
	// _version is non-zero (used by ReplacingMergeTree).
	if r["_version"].(uint64) == 0 {
		t.Errorf("_version must be non-zero (used for dedup)")
	}
}

func TestConvertHandlesMissingProjectID(t *testing.T) {
	traces := buildOTLPSpan()
	// Drop the project_id from resource attrs; fall-through must produce a
	// non-empty UUID (random) so CH's non-nullable column stays satisfied.
	traces.ResourceSpans().At(0).Resource().Attributes().Remove("fi.project_id")
	rows, err := Convert(traces)
	if err != nil {
		t.Fatal(err)
	}
	pid := rows[0]["project_id"].(string)
	if len(pid) != 36 || pid[14] != '4' {
		t.Errorf("expected v4 UUID, got %q", pid)
	}
}

func TestConvertParentSpan(t *testing.T) {
	traces := buildOTLPSpan()
	sp := traces.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0)
	sp.SetParentSpanID([8]byte{0xa, 0xb, 0xc, 0xd, 0xe, 0xf, 0x1, 0x2})
	rows, _ := Convert(traces)
	if rows[0]["parent_span_id"] != "0a0b0c0d0e0f0102" {
		t.Errorf("parent_span_id: got %q", rows[0]["parent_span_id"])
	}
}

func TestConvertErrorStatus(t *testing.T) {
	traces := buildOTLPSpan()
	sp := traces.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0)
	sp.Status().SetCode(ptrace.StatusCodeError)
	sp.Status().SetMessage("boom")
	rows, _ := Convert(traces)
	if rows[0]["status"] != "ERROR" {
		t.Errorf("status: got %v", rows[0]["status"])
	}
	if rows[0]["status_message"] != "boom" {
		t.Errorf("status_message: got %v", rows[0]["status_message"])
	}
}

func TestConvertLatencyClampedForOverflow(t *testing.T) {
	traces := buildOTLPSpan()
	sp := traces.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0)
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Unix(0, 0)))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Unix(1_000_000_000, 0))) // 31y
	rows, _ := Convert(traces)
	// Int32 max ≈ 2.14e9 ms ≈ 24.8 days
	got := rows[0]["latency_ms"].(int32)
	if got <= 0 {
		t.Errorf("clamp should produce positive max-int32; got %d", got)
	}
}
