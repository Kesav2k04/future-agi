package clickhouse25exporter

import (
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/detid"
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
		"project_id":       "11111111-1111-4111-8111-111111111111",
		"observation_type": "LLM",
		"service_name":     "my-llm-app",
		// trace_id is emitted as the 36-char DASHED UUID (not 32-char hex) so it
		// matches PG tracer_trace.id + the migration backfill and resolves via
		// toUUID() in the trace_dict lookup. span id stays 16-char hex.
		"trace_id":          "01020304-0506-0708-090a-0b0c0d0e0f10",
		"id":                "0908070605040302",
		"name":              "llm.chat.completion",
		"latency_ms":        int32(1500),
		"status":            "OK",
		"model":             "gpt-4o-mini",
		"provider":          "openai",
		"gen_ai_system":     "openai",
		"gen_ai_operation":  "chat",
		"prompt_tokens":     int32(120),
		"completion_tokens": int32(38),
		"total_tokens":      int32(158),
		"input":             "Hello, world!",
		"output":            "Hi there.",
		"semconv_source":    "openinference",
		"is_deleted":        uint8(0),
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

// ─── CH-derived dimensions (P3b step2): deterministic id stamping ──────────
//
// These tests pin the CONVERTER's extraction + gating. The id BYTES are
// covered by pkg/detid's parity gate; here we verify the converter pulls the
// right OTLP keys, applies the same gates as Django, and lands NULL when it
// should. We assert exact ids by re-deriving with detid (same formula) — the
// detid package separately proves detid == Python.

const (
	stampProject = "11111111-1111-4111-8111-111111111111" // matches buildOTLPSpan
	stampOrg     = "22222222-2222-4222-8222-222222222222"
)

// buildObserveSpanWith builds an LLM span tagged as an observe project with the
// given user.id / user.id.type / session.id span attributes. `set*` flags
// distinguish ABSENT from present (present-empty has different semantics).
func buildObserveSpanWith(userID string, setUser bool, userType string, setType bool, sessionID string, setSession bool) ptrace.Traces {
	traces := buildOTLPSpan()
	rs := traces.ResourceSpans().At(0)
	rs.Resource().Attributes().PutStr("project_type", "observe")
	a := rs.ScopeSpans().At(0).Spans().At(0).Attributes()
	if setUser {
		a.PutStr("user.id", userID)
	}
	if setType {
		a.PutStr("user.id.type", userType)
	}
	if setSession {
		a.PutStr("session.id", sessionID)
	}
	return traces
}

func TestStampEndUserAndSession_ObserveProject(t *testing.T) {
	traces := buildObserveSpanWith("sarthak@futureagi.com", true, "", false, "sess-123", true)
	rows, err := Convert(traces)
	if err != nil {
		t.Fatal(err)
	}
	r := rows[0]

	// end_user_id: type absent → "" sentinel → detid.EndUserID(..., "").
	wantEU := detid.EndUserID(stampProject, stampOrg, "sarthak@futureagi.com", "").String()
	if r["end_user_id"] != wantEU {
		t.Errorf("end_user_id: got %#v want %s", r["end_user_id"], wantEU)
	}
	// trace_session_id: present session.id → detid.TraceSessionID.
	wantSess := detid.TraceSessionID(stampProject, "sess-123").String()
	if r["trace_session_id"] != wantSess {
		t.Errorf("trace_session_id: got %#v want %s", r["trace_session_id"], wantSess)
	}
}

func TestStampEndUser_NonObserveProject_NullEndUser(t *testing.T) {
	// experiment project (the default buildOTLPSpan has NO project_type) must
	// NOT stamp end_user_id, but session is not observe-gated so it still
	// stamps.
	traces := buildOTLPSpan() // no project_type resource attr
	a := traces.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).Attributes()
	a.PutStr("user.id", "u1")
	a.PutStr("session.id", "s1")
	rows, _ := Convert(traces)
	r := rows[0]
	if r["end_user_id"] != nil {
		t.Errorf("end_user_id must be nil for non-observe project, got %#v", r["end_user_id"])
	}
	wantSess := detid.TraceSessionID(stampProject, "s1").String()
	if r["trace_session_id"] != wantSess {
		t.Errorf("trace_session_id must stamp regardless of project_type: got %#v want %s", r["trace_session_id"], wantSess)
	}
}

func TestStampEndUser_AbsentUserID_Null(t *testing.T) {
	traces := buildObserveSpanWith("", false, "", false, "", false) // nothing set
	rows, _ := Convert(traces)
	r := rows[0]
	if r["end_user_id"] != nil {
		t.Errorf("end_user_id must be nil when user.id absent, got %#v", r["end_user_id"])
	}
	if r["trace_session_id"] != nil {
		t.Errorf("trace_session_id must be nil when session.id absent, got %#v", r["trace_session_id"])
	}
}

func TestStampEndUser_EmptyUserID_Null(t *testing.T) {
	// Present-but-empty user.id is FALSY in Python (`if attributes.get(USER_ID):`)
	// → no end_user. Mirror that: empty user.id → NULL.
	traces := buildObserveSpanWith("", true, "", false, "", false)
	rows, _ := Convert(traces)
	if rows[0]["end_user_id"] != nil {
		t.Errorf("empty user.id must yield nil end_user_id, got %#v", rows[0]["end_user_id"])
	}
}

func TestStampEndUser_PresentEmptyType_IsCustom(t *testing.T) {
	// PRESENT empty user.id.type → get_user_id_type("") → "custom" (NOT the ""
	// sentinel). So it must differ from the absent-type id and equal the
	// "custom"-typed id.
	withEmptyType := buildObserveSpanWith("u1", true, "", true, "", false)
	rows, _ := Convert(withEmptyType)
	gotEmpty := rows[0]["end_user_id"]

	wantCustom := detid.EndUserID(stampProject, stampOrg, "u1", "custom").String()
	if gotEmpty != wantCustom {
		t.Errorf("present-empty type must normalize to \"custom\": got %#v want %s", gotEmpty, wantCustom)
	}
	// And must NOT equal the absent-type ("" sentinel) id.
	wantSentinel := detid.EndUserID(stampProject, stampOrg, "u1", "").String()
	if gotEmpty == wantSentinel {
		t.Error("present-empty type must NOT collapse to the absent/None sentinel id")
	}
}

func TestStampEndUser_KnownType_Passthrough(t *testing.T) {
	traces := buildObserveSpanWith("u1", true, "email", true, "", false)
	rows, _ := Convert(traces)
	want := detid.EndUserID(stampProject, stampOrg, "u1", "email").String()
	if rows[0]["end_user_id"] != want {
		t.Errorf("email type: got %#v want %s", rows[0]["end_user_id"], want)
	}
}

func TestStampSession_PresentEmptyName_Stamps(t *testing.T) {
	// Python gate is `session_name is not None` on a bare .get — present-empty
	// session.id ("") still stamps (with name "").
	traces := buildObserveSpanWith("", false, "", false, "", true) // session.id = ""
	rows, _ := Convert(traces)
	want := detid.TraceSessionID(stampProject, "").String()
	if rows[0]["trace_session_id"] != want {
		t.Errorf("present-empty session.id must stamp with name \"\": got %#v want %s", rows[0]["trace_session_id"], want)
	}
}

func TestStampEndUser_UppercaseProjectID_CanonicalizesToLowercaseKey(t *testing.T) {
	// The frozen ids were derived from str(uuid.UUID) (lowercase). If a producer
	// sends an UPPERCASE project_id/org_id, the stamp must still key on the
	// lowercase-canonical form — i.e. equal the lowercase-derived id.
	traces := buildOTLPSpan()
	rs := traces.ResourceSpans().At(0)
	rs.Resource().Attributes().PutStr("project_type", "observe")
	rs.Resource().Attributes().PutStr("fi.project_id", strings.ToUpper(stampProject))
	rs.Resource().Attributes().PutStr("fi.org_id", strings.ToUpper(stampOrg))
	rs.ScopeSpans().At(0).Spans().At(0).Attributes().PutStr("user.id", "u1")
	rows, _ := Convert(traces)

	// Must equal the id keyed on the LOWERCASE canonical project/org.
	want := detid.EndUserID(stampProject, stampOrg, "u1", "").String()
	if rows[0]["end_user_id"] != want {
		t.Errorf("uppercase project/org must canonicalize to lowercase key: got %#v want %s", rows[0]["end_user_id"], want)
	}
	// project_id COLUMN keeps its own contract (unchanged by this); we only
	// assert the deterministic-id key canonicalized.
}

func TestStampEndUser_UnparseableProjectID_Null(t *testing.T) {
	// A non-UUID project_id must NOT produce a malformed-key id — decline to
	// stamp (NULL is backfillable; a bad-key id is corruption).
	traces := buildOTLPSpan()
	rs := traces.ResourceSpans().At(0)
	rs.Resource().Attributes().PutStr("project_type", "observe")
	rs.Resource().Attributes().PutStr("fi.project_id", "not-a-uuid")
	rs.ScopeSpans().At(0).Spans().At(0).Attributes().PutStr("user.id", "u1")
	rs.ScopeSpans().At(0).Spans().At(0).Attributes().PutStr("session.id", "s1")
	rows, _ := Convert(traces)
	if rows[0]["end_user_id"] != nil {
		t.Errorf("unparseable project_id must yield nil end_user_id, got %#v", rows[0]["end_user_id"])
	}
	if rows[0]["trace_session_id"] != nil {
		t.Errorf("unparseable project_id must yield nil trace_session_id, got %#v", rows[0]["trace_session_id"])
	}
}

func TestStampEndUser_NumericUserID_StringCoerced(t *testing.T) {
	// A numeric user.id must coerce via AsString() (matching Python's f-string
	// str()), NOT route through the float64 attrs_number tier.
	traces := buildOTLPSpan()
	rs := traces.ResourceSpans().At(0)
	rs.Resource().Attributes().PutStr("project_type", "observe")
	rs.ScopeSpans().At(0).Spans().At(0).Attributes().PutInt("user.id", 12345)
	rows, _ := Convert(traces)
	want := detid.EndUserID(stampProject, stampOrg, "12345", "").String()
	if rows[0]["end_user_id"] != want {
		t.Errorf("numeric user.id must str()-coerce to \"12345\": got %#v want %s", rows[0]["end_user_id"], want)
	}
}
