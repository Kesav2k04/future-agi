import fs from "node:fs/promises";
import process from "node:process";
import {
  CleanupStack,
  SkipJourney,
  createAuthenticatedContext,
} from "./api-client.mjs";

export async function runJourneys(journeys, argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  if (args.list) {
    for (const journey of journeys) {
      console.log(`${journey.id}\t${journey.title}`);
    }
    return { status: "listed", results: [] };
  }

  const selected = journeys.filter((journey) => {
    if (args.only.size && !args.only.has(journey.id)) return false;
    if (args.grep) {
      const haystack = `${journey.id} ${journey.title} ${journey.tags?.join(" ")}`;
      return haystack.toLowerCase().includes(args.grep.toLowerCase());
    }
    return true;
  });

  if (!selected.length) {
    const summary = {
      status: "passed",
      api_base: normalizeBaseUrl(
        process.env.API_BASE || "http://localhost:8003",
      ),
      organization_id: process.env.FUTURE_AGI_ORGANIZATION_ID || null,
      workspace_id: process.env.FUTURE_AGI_WORKSPACE_ID || null,
      total: 0,
      passed: 0,
      skipped: 0,
      failed: 0,
      selected_total: 0,
      results: [],
    };
    await writeSummary(summary, args);
    return summary;
  }

  let baseContext;
  try {
    baseContext = await createAuthenticatedContext();
  } catch (error) {
    const summary = {
      status: "failed",
      api_base: normalizeBaseUrl(
        process.env.API_BASE || "http://localhost:8003",
      ),
      organization_id: process.env.FUTURE_AGI_ORGANIZATION_ID || null,
      workspace_id: process.env.FUTURE_AGI_WORKSPACE_ID || null,
      total: 1,
      passed: 0,
      skipped: 0,
      failed: 1,
      selected_total: selected.length,
      results: [
        {
          id: "context_setup",
          title: "Authenticated API journey context",
          status: "failed",
          error: error.message,
          stack: error.stack,
        },
      ],
    };
    await writeSummary(summary, args);
    process.exitCode = 1;
    return summary;
  }
  const results = [];

  for (const journey of selected) {
    const cleanup = new CleanupStack();
    const evidence = [];
    const startedAt = Date.now();
    let cleanupRan = false;
    process.stdout.write(`RUN ${journey.id} ${journey.title}\n`);

    try {
      const result = await journey.run({ ...baseContext, cleanup, evidence });
      const cleanupFailures = await cleanup.run(evidence);
      cleanupRan = true;
      if (cleanupFailures.length) {
        throw new Error(
          `Cleanup failed: ${cleanupFailures
            .map((item) => `${item.label}: ${item.error}`)
            .join("; ")}`,
        );
      }
      results.push({
        id: journey.id,
        title: journey.title,
        status: "passed",
        elapsed_ms: Date.now() - startedAt,
        evidence,
        result,
      });
      process.stdout.write(`PASS ${journey.id}\n`);
    } catch (error) {
      if (error instanceof SkipJourney) {
        if (!cleanupRan) await cleanup.run(evidence);
        results.push({
          id: journey.id,
          title: journey.title,
          status: "skipped",
          reason: error.reason,
          elapsed_ms: Date.now() - startedAt,
          evidence,
        });
        process.stdout.write(`SKIP ${journey.id} ${error.reason}\n`);
      } else {
        if (!cleanupRan) await cleanup.run(evidence);
        results.push({
          id: journey.id,
          title: journey.title,
          status: "failed",
          error: error.message,
          stack: error.stack,
          elapsed_ms: Date.now() - startedAt,
          evidence,
        });
        process.stdout.write(`FAIL ${journey.id} ${error.message}\n`);
        if (args.failFast) break;
      }
    }
  }

  const summary = {
    status: results.some((item) => item.status === "failed")
      ? "failed"
      : "passed",
    api_base: baseContext.apiBase,
    organization_id: baseContext.organizationId || null,
    workspace_id: baseContext.workspaceId || null,
    total: results.length,
    passed: results.filter((item) => item.status === "passed").length,
    skipped: results.filter((item) => item.status === "skipped").length,
    failed: results.filter((item) => item.status === "failed").length,
    results,
  };

  await writeSummary(summary, args);
  if (summary.failed > 0) process.exitCode = 1;
  return summary;
}

async function writeSummary(summary, args) {
  if (args.jsonPath) {
    await fs.writeFile(args.jsonPath, `${JSON.stringify(summary, null, 2)}\n`);
  }
  console.log(JSON.stringify(summary, null, 2));
}

function normalizeBaseUrl(value) {
  return String(value || "").replace(/\/+$/, "");
}

function parseArgs(argv) {
  const args = {
    failFast: false,
    grep: "",
    jsonPath: "",
    list: false,
    only: new Set(),
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--fail-fast") {
      args.failFast = true;
    } else if (arg === "--list") {
      args.list = true;
    } else if (arg === "--grep") {
      args.grep = argv[++index] || "";
    } else if (arg === "--only") {
      for (const id of String(argv[++index] || "").split(",")) {
        if (id.trim()) args.only.add(id.trim());
      }
    } else if (arg === "--json") {
      args.jsonPath = argv[++index] || "";
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return args;
}
