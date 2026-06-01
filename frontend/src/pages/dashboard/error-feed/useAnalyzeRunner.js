import { useCallback, useEffect, useRef } from "react";
import { useErrorFeedStore } from "./store";

// Mock run sequence — replace once BE streams real sub-agent steps.
// Lives in this hook (not the AnalyzeTab component) so a run can be
// kicked off and continue progressing even when the user is not on
// the Analyze tab. ClusterHeadlineCard observes the same thread state
// from the store, so the two views stay in lockstep.
// Each step carries a collapsed `detail` one-liner plus an expandable
// `details` block (the agent's reasoning + the data/tools it looked at) —
// rendered like Claude Code's expandable tool/thinking blocks.
const RUN_STEPS = [
  {
    title: "Sampling representative calls",
    detail: "Picked 3 traces · centroid · outlier · p95 latency",
    chips: ["centroid", "outlier", "p95"],
    runDelayMs: 600,
    doneDelayMs: 800,
    details: [
      {
        kind: "reasoning",
        text: "To keep this cheap and representative, I sampled three traces that span the cluster's behaviour instead of reading all 32 — the most typical failure, the most divergent one, and the slowest.",
      },
      {
        kind: "tool",
        name: "sample_cluster_traces",
        input: "cluster_id, strategy=[centroid, outlier, p95_latency]",
        output: "3 traces selected",
      },
      {
        kind: "list",
        title: "Sampled traces",
        items: [
          "04ad94ec · centroid · most typical · eval 0.50",
          "c308e2c3 · outlier · most divergent embedding · eval 0.31",
          "78c2e5ff · p95 latency · slowest call · 4.4s",
        ],
      },
      {
        kind: "reasoning",
        text: "All three are failures on the same pii_task eval config, so the cluster is cohesive — not a mix of unrelated issues.",
      },
    ],
  },
  {
    title: "Reading conversation transcripts",
    detail: "Average prompt drift in turn 2; ~700-token system prompt steady.",
    chips: [],
    runDelayMs: 500,
    doneDelayMs: 700,
    details: [
      {
        kind: "reasoning",
        text: "I read each sampled transcript turn-by-turn, watching for where the agent's behaviour diverged from the user's intent.",
      },
      {
        kind: "list",
        title: "Turn-by-turn observations",
        items: [
          "Turn 1 — user supplies the user_id and the field they want fetched.",
          "Turn 2 — agent re-requests the user_id it was already given (context dropped).",
          "Turn 3 — agent proceeds on a guessed value, returning the wrong record.",
        ],
      },
      {
        kind: "reasoning",
        text: "The system prompt is steady at ~700 tokens across all three traces, so the drift is behavioural — not a token-budget truncation.",
      },
    ],
  },
  {
    title: "Comparing against nearest passing call",
    detail:
      "Passing trace re-states user inputs before tool dispatch; failing traces skip.",
    chips: ["KNN match · cos 0.12"],
    runDelayMs: 700,
    doneDelayMs: 900,
    details: [
      {
        kind: "reasoning",
        text: "I pulled the nearest passing trace by embedding distance and diffed the two execution paths to isolate exactly what differs.",
      },
      {
        kind: "tool",
        name: "knn_passing_match",
        input: "cluster_id, root_input_embedding",
        output: "trace 9f3a · cosine 0.12 · eval 1.00",
      },
      {
        kind: "list",
        title: "Execution-path diff (failing → passing)",
        items: [
          "Passing re-states the user's inputs in a system turn before the tool call.",
          "Failing skips that restate → the tool runs without the prior-turn context.",
          "Tools available, model, and temperature are identical between the two.",
        ],
      },
    ],
  },
  {
    title: "Checking deploy timeline",
    detail: "First seen 4d after v2.4.1 (prompt rev). No matching infra event.",
    chips: ["v2.4.1"],
    runDelayMs: 600,
    doneDelayMs: 700,
    details: [
      {
        kind: "reasoning",
        text: "I correlated the cluster's first-seen timestamp against the deploy and prompt-revision history.",
      },
      {
        kind: "list",
        title: "Timeline",
        items: [
          "May 18 — v2.4.0 shipped (no change to this flow).",
          "May 22 14:02 — v2.4.1 prompt revision, system prompt shortened ~120 tokens.",
          "May 22 — cluster first seen; failures sustained for ~4 days after.",
        ],
      },
      {
        kind: "reasoning",
        text: "No infra/deploy event lines up — the regression tracks the prompt revision, not an infrastructure change.",
      },
    ],
  },
  {
    title: "Synthesizing",
    detail: "Drafting the cluster-level summary.",
    chips: [],
    runDelayMs: 500,
    doneDelayMs: 1200,
    details: [
      {
        kind: "reasoning",
        text: "Combining the findings: v2.4.1 dropped the line that restated user-supplied inputs, so the agent loses context across turns and re-asks for data it already has.",
      },
      {
        kind: "reasoning",
        text: "The fix is prompt-side and low-risk — restore the restate guard before tool dispatch. Confidence is high because the passing/failing diff isolates this single difference.",
      },
    ],
  },
];

function makeStepMessage(idx) {
  const step = RUN_STEPS[idx];
  return {
    id: `step-${Date.now()}-${idx}`,
    type: "step",
    status: "queued",
    title: step.title,
    detail: step.detail,
    chips: step.chips,
    details: step.details,
  };
}

function buildSynthesis(error) {
  const name = error?.error?.name ?? "this cluster";
  const count = error?.traceCount?.toLocaleString() ?? "—";
  return {
    id: `synth-${Date.now()}`,
    type: "synthesis",
    headline:
      `${name} occurs when the agent drops critical user context across turns. ` +
      `The model re-asks for already-provided inputs in ~31% of the ${count} affected traces.`,
    fix: "Add a one-line guard in the system prompt restating already-supplied user inputs before each tool dispatch.",
    confidence: "H",
    category: "fix in prompt",
  };
}

export function useAnalyzeRunner(clusterId, error) {
  const setAnalyzeThread = useErrorFeedStore((s) => s.setAnalyzeThread);
  const clearAnalyzePendingStart = useErrorFeedStore(
    (s) => s.clearAnalyzePendingStart,
  );
  const pendingStart = useErrorFeedStore(
    (s) => !!s.analyzePendingStartByCluster[clusterId],
  );

  const timersRef = useRef([]);
  const clearTimers = useCallback(() => {
    timersRef.current.forEach((t) => clearTimeout(t));
    timersRef.current = [];
  }, []);

  // Cancel pending timers when the user leaves this cluster.
  useEffect(() => () => clearTimers(), [clusterId, clearTimers]);

  const patch = useCallback(
    (mutator) => {
      const current =
        useErrorFeedStore.getState().analyzeThreadsByCluster[clusterId];
      const seed = current ?? { messages: [], runState: "idle", startedAt: null };
      setAnalyzeThread(clusterId, mutator(seed));
    },
    [clusterId, setAnalyzeThread],
  );

  const startRun = useCallback(() => {
    if (!clusterId) return;
    clearTimers();

    const now = new Date();
    const timeLabel = now.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });

    patch((t) => ({
      messages:
        t.messages.length > 0
          ? [
              ...t.messages,
              {
                id: `hdr-${Date.now()}`,
                type: "run_header",
                label: "Re-run",
                timestamp: timeLabel,
              },
            ]
          : [],
      runState: "streaming",
      startedAt: Date.now(),
    }));

    const enqueueNext = (i) => {
      const step = RUN_STEPS[i];
      if (!step) {
        const synth = buildSynthesis(error);
        const t1 = setTimeout(() => {
          patch((t) => ({
            ...t,
            messages: [...t.messages, synth],
            runState: "done",
          }));
        }, 250);
        timersRef.current.push(t1);
        return;
      }
      const msg = makeStepMessage(i);
      patch((t) => ({ ...t, messages: [...t.messages, msg] }));
      const tRun = setTimeout(() => {
        patch((t) => ({
          ...t,
          messages: t.messages.map((m) =>
            m.id === msg.id ? { ...m, status: "running" } : m,
          ),
        }));
        const tDone = setTimeout(() => {
          patch((t) => ({
            ...t,
            messages: t.messages.map((m) =>
              m.id === msg.id ? { ...m, status: "done" } : m,
            ),
          }));
          enqueueNext(i + 1);
        }, step.doneDelayMs);
        timersRef.current.push(tDone);
      }, step.runDelayMs);
      timersRef.current.push(tRun);
    };

    enqueueNext(0);
  }, [clusterId, error, clearTimers, patch]);

  // Auto-fire whenever the pending-start flag flips on for this cluster.
  // Single source of truth: any analyze button anywhere just sets the flag.
  useEffect(() => {
    if (!clusterId || !pendingStart) return;
    clearAnalyzePendingStart(clusterId);
    startRun();
  }, [clusterId, pendingStart, clearAnalyzePendingStart, startRun]);

  return { startRun, clearTimers };
}
