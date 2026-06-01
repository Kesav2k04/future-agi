// Frontend-only synthetic "voice calls" cluster. The backend doesn't yet
// tag clusters with a modality (text vs voice), so this demo cluster lets
// us show the voice-call detail experience as a separate row in the Error
// Feed table. Remove this module once BE returns real voice clusters with
// a `modality` field.

export const VOICE_DEMO_ID = "VOICE-DEMO-1";

export const isVoiceDemoCluster = (id) => id === VOICE_DEMO_ID;

// Row shape consumed by ErrorFeedTable.
export const voiceDemoRow = {
  clusterId: VOICE_DEMO_ID,
  source: "eval",
  modality: "voice",
  error: {
    name: "Agent reads back full card number on voice calls",
    type: "PIILeakError",
  },
  evalScore: 0.42,
  severity: "critical",
  status: "escalating",
  traceCount: 20,
  usersAffected: 0,
  fixLayer: "prompt",
  trends: Array.from({ length: 14 }).map((_, i) => ({
    timestamp: Date.now() - (13 - i) * 86400000,
    value: i < 9 ? 0 : [2, 4, 5, 3, 6][i - 9] ?? 1,
  })),
  lastSeen: new Date(Date.now() - 2 * 3600000).toISOString(),
  lastSeenHuman: "2h ago",
};

// Detail shape consumed by ErrorFeedDetailView (mirrors useErrorFeedDetail).
export const voiceDemoDetail = {
  row: voiceDemoRow,
  description:
    "Voice agent repeats the caller's full card number and reads back the email in clear during card-verification calls.",
  successTrace: null,
  representativeTrace: null,
};

// Overview shape consumed by OverviewTab (mirrors useErrorFeedOverview).
const VOICE_INPUTS = [
  "Confirm the charge on my card ending 4242",
  "I want to dispute a $14.99 charge",
  "Can you check my recent transactions?",
  "Update the email on my account",
  "Verify my identity for a refund",
];

export const voiceDemoOverview = {
  eventsOverTime: Array.from({ length: 14 }).map((_, i) => ({
    date: new Date(Date.now() - (13 - i) * 86400000).toISOString(),
    errors: i < 9 ? 0 : [2, 4, 5, 3, 6][i - 9] ?? 1,
    users: 0,
  })),
  patternSummary: null, // falls back to the Pattern Summary stub
  representativeTraces: Array.from({ length: 20 }).map((_, i) => ({
    id: `call_${(i + 1).toString().padStart(2, "0")}_${Math.random()
      .toString(16)
      .slice(2, 8)}`,
    status: i % 5 === 4 ? "pass" : "fail",
    timestamp: new Date(Date.now() - (20 - i) * 7 * 60000).toISOString(),
    summary: {
      latencyMs: 160000 + ((i * 7919) % 40000),
      inputTokens: 0,
      outputTokens: 0,
      cost: 0.08 + ((i * 13) % 5) * 0.01,
    },
    evidence: { input: VOICE_INPUTS[i % VOICE_INPUTS.length] },
  })),
};
