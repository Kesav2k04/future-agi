/**
 * buildGraphDiff — diff two AgentGraphs (from buildTraceGraph) and produce
 * a richer failing-side graph that surfaces, in-place:
 *
 *   1. What the failing trace actually did            (its real nodes/edges)
 *   2. What it *should* have done but skipped         (ghost nodes + dashed
 *                                                      "SKIPPED PATH" edges)
 *   3. Where things actually went wrong               (`_isFailurePoint` on
 *                                                      nodes whose spans had
 *                                                      errors)
 *
 * Each node is matched by its `(type, name)` pair — both come from the span
 * attributes and are stable identifiers.
 *
 * Annotations on `node.data` (all optional, ignored when absent):
 *
 *   _diffStatus:
 *     "fail-only"          → extra step in failing trace
 *     "pass-only-ghost"    → ghost step injected from working (was skipped)
 *     "matched-regressed"  → exists in both, but failing has errors and/or
 *                            is ≥1.5× slower
 *     "matched"            → exists in both with comparable metrics
 *
 *   _isFailurePoint: true  → this node is where the failing trace errored
 *                            (takes visual priority over diff status; a
 *                            failed node is the headline, not a diff cue)
 *
 * Annotations on edges:
 *
 *   _skipped: true         → synthetic dashed edge representing a path the
 *                            failing trace did not take. Rendered red.
 *
 * The original `failGraph` / `passGraph` objects are NOT mutated.
 *
 * The `passAnnotated` graph on the right is kept simpler — its nodes get the
 * standard diff annotations, but no ghosts/skipped edges. The failing-side
 * graph is the one carrying the diagnostic story.
 */

const SLOWER_RATIO = 1.5;
const SENTINEL_TYPES = new Set(["start", "end"]);
const GHOST_PREFIX = "ghost-";

function keyOf(node) {
  const type = String(node?.data?.type ?? "")
    .toLowerCase()
    .trim();
  const name = String(node?.data?.name ?? "")
    .toLowerCase()
    .trim();
  return `${type}|${name}`;
}

function isRegressed(failNode, passNode) {
  const failErr = failNode?.data?.error_count ?? 0;
  const passErr = passNode?.data?.error_count ?? 0;
  if (failErr > 0 && passErr === 0) return true;

  const failLat = failNode?.data?.avg_latency_ms ?? 0;
  const passLat = passNode?.data?.avg_latency_ms ?? 0;
  if (passLat > 0 && failLat / passLat >= SLOWER_RATIO) return true;

  return false;
}

function annotate(node, patch) {
  // Shallow clone — fresh data object so React Flow + memoised consumers pick
  // up the change without mutating the upstream graph.
  return {
    ...node,
    data: {
      ...node.data,
      ...patch,
    },
  };
}

export function buildGraphDiff(failGraph, passGraph) {
  if (!failGraph || !passGraph) {
    return {
      failAnnotated: failGraph ?? null,
      passAnnotated: passGraph ?? null,
      summary: { added: 0, missing: 0, regressed: 0, shared: 0, failed: 0 },
    };
  }

  const failNodes = failGraph.nodes ?? [];
  const passNodes = passGraph.nodes ?? [];
  const failEdges = failGraph.edges ?? [];
  const passEdges = passGraph.edges ?? [];

  const passByKey = new Map();
  for (const n of passNodes) passByKey.set(keyOf(n), n);

  let added = 0;
  let missing = 0;
  let regressed = 0;
  let shared = 0;
  let failed = 0;

  // 1. Annotate the failing-side nodes in-place. A node that errored takes
  //    visual priority via `_isFailurePoint` regardless of its diff status.
  const annotatedFailNodes = failNodes.map((node) => {
    const isSentinel = SENTINEL_TYPES.has(node?.data?.type);
    const errorCount = node?.data?.error_count ?? 0;
    const isFailurePoint = !isSentinel && errorCount > 0;
    if (isFailurePoint) failed += 1;

    if (isSentinel) {
      return annotate(node, { _diffStatus: "matched" });
    }
    const match = passByKey.get(keyOf(node));
    let status;
    if (!match) {
      added += 1;
      status = "fail-only";
    } else if (isRegressed(node, match)) {
      regressed += 1;
      status = "matched-regressed";
    } else {
      shared += 1;
      status = "matched";
    }
    return annotate(node, {
      _diffStatus: status,
      _isFailurePoint: isFailurePoint || undefined,
    });
  });

  // 2. Identify pass-only (missing) nodes. Each becomes a ghost in the
  //    failing graph carrying the working-side metadata.
  const failByKey = new Map();
  for (const n of annotatedFailNodes) failByKey.set(keyOf(n), n);

  const passIdToKey = new Map();
  for (const n of passNodes) passIdToKey.set(n.id, keyOf(n));

  const ghostNodes = [];
  const ghostIds = new Set(); // working-trace ids whose ghost we created
  for (const passNode of passNodes) {
    if (SENTINEL_TYPES.has(passNode?.data?.type)) continue;
    if (failByKey.has(keyOf(passNode))) continue; // exists in failing → not missing
    missing += 1;
    ghostNodes.push({
      ...passNode,
      id: `${GHOST_PREFIX}${passNode.id}`,
      data: { ...passNode.data, _diffStatus: "pass-only-ghost" },
    });
    ghostIds.add(passNode.id);
  }

  // 3. Build synthetic "skipped path" edges. For each working-graph edge
  //    whose TARGET is a missing/ghost node, mirror it into the failing
  //    graph: source is the failing-side equivalent if shared (entry into
  //    the ghost branch), or another ghost if both endpoints are missing
  //    (interior of a multi-hop missing chain). We do NOT mirror edges
  //    whose target is shared — those would rejoin the real flow and
  //    create confusing duplicate connections.
  const skippedEdges = [];
  for (const edge of passEdges) {
    if (!ghostIds.has(edge.target)) continue;

    let sourceId;
    if (ghostIds.has(edge.source)) {
      // Ghost-to-ghost (interior of missing sub-chain).
      sourceId = `${GHOST_PREFIX}${edge.source}`;
    } else {
      // Anchor the ghost branch back to the equivalent failing-side parent.
      const sourcePassNode = passNodes.find((n) => n.id === edge.source);
      if (!sourcePassNode) continue;
      const failParent = failByKey.get(keyOf(sourcePassNode));
      if (!failParent) continue;
      sourceId = failParent.id;
    }

    skippedEdges.push({
      source: sourceId,
      target: `${GHOST_PREFIX}${edge.target}`,
      transition_count: 1,
      _skipped: true,
    });
  }

  const failAnnotated = {
    ...failGraph,
    nodes: [...annotatedFailNodes, ...ghostNodes],
    edges: [...failEdges, ...skippedEdges],
  };

  // 4. Working-side nodes get standard diff annotations; the working graph
  //    is the reference view, not the storytelling view.
  const passAnnotated = {
    ...passGraph,
    nodes: passNodes.map((node) => {
      if (SENTINEL_TYPES.has(node?.data?.type)) {
        return annotate(node, { _diffStatus: "matched" });
      }
      const match = failByKey.get(keyOf(node));
      if (!match) return annotate(node, { _diffStatus: "pass-only" });
      return annotate(node, { _diffStatus: "matched" });
    }),
  };

  return {
    failAnnotated,
    passAnnotated,
    summary: { added, missing, regressed, shared, failed },
  };
}
