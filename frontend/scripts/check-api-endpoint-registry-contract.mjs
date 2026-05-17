/* eslint-env node */
/* eslint-disable no-console */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { parse } from "@babel/parser";
import traverseModule from "@babel/traverse";

import {
  API_SURFACE_CONTRACT,
  API_SURFACE_PATHS,
} from "../src/api/contracts/api-surface.generated.js";

const traverse = traverseModule.default;

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(__dirname, "..");
const endpointRegistryPath = path.join(
  frontendRoot,
  "src",
  "utils",
  "axios.js",
);
const MAX_UNCONTRACTED_REGISTRY_PATHS = 77;
const MANAGEMENT_API_GROUPS = Object.keys(API_SURFACE_CONTRACT.groups)
  .filter((groupName) => groupName !== "root")
  .sort();
const API_PATH_RE = new RegExp(
  `^/(?:${MANAGEMENT_API_GROUPS.map((groupName) =>
    groupName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
  ).join("|")})(?:/|$)`,
);

const source = fs.readFileSync(endpointRegistryPath, "utf8");
const ast = parse(source, {
  sourceType: "module",
  plugins: ["jsx", "typescript"],
});

const apiPathTemplates = Object.keys(API_SURFACE_PATHS);
const apiPathMatchers = apiPathTemplates.map((template) => ({
  template,
  regex: new RegExp(
    `^${template
      .replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
      .replace(/\\\{[^}]+\\\}/g, "[^/]+")}$`,
  ),
}));

function rawPathValue(node) {
  if (node.type === "StringLiteral") return node.value;
  if (node.type !== "TemplateLiteral") return null;
  return node.quasis
    .map((quasi, index) => {
      const raw = quasi.value.raw || "";
      return index < node.expressions.length ? `${raw}\${}` : raw;
    })
    .join("");
}

function isInApiPathCall(nodePath) {
  const parent = nodePath.parentPath;
  return (
    parent?.isCallExpression() &&
    parent.node.callee?.type === "Identifier" &&
    parent.node.callee.name === "apiPath" &&
    parent.node.arguments[0] === nodePath.node
  );
}

function matchedContractTemplate(rawValue) {
  if (Object.prototype.hasOwnProperty.call(API_SURFACE_PATHS, rawValue)) {
    return rawValue;
  }
  const withoutQuery = rawValue.split("?")[0];
  const concretePath = withoutQuery.replace(/\$\{\}/g, "placeholder");
  return (
    apiPathMatchers.find(({ regex }) => regex.test(concretePath))?.template ||
    null
  );
}

function collectRawRegistryPaths() {
  const rawPaths = [];
  traverse(ast, {
    StringLiteral(nodePath) {
      if (isInApiPathCall(nodePath)) return;
      const value = rawPathValue(nodePath.node);
      if (value && API_PATH_RE.test(value)) {
        rawPaths.push({ value, line: nodePath.node.loc?.start?.line || 1 });
      }
    },
    TemplateLiteral(nodePath) {
      if (isInApiPathCall(nodePath)) return;
      const value = rawPathValue(nodePath.node);
      if (value && API_PATH_RE.test(value)) {
        rawPaths.push({ value, line: nodePath.node.loc?.start?.line || 1 });
      }
    },
  });
  return rawPaths;
}

const rawPathsByValue = new Map();
for (const rawPath of collectRawRegistryPaths()) {
  if (!rawPathsByValue.has(rawPath.value))
    rawPathsByValue.set(rawPath.value, rawPath);
}

const registryPaths = [...rawPathsByValue.values()].map((rawPath) => ({
  ...rawPath,
  contractTemplate: matchedContractTemplate(rawPath.value),
}));
const uncontracted = registryPaths.filter(
  (rawPath) => !rawPath.contractTemplate,
);

if (uncontracted.length > MAX_UNCONTRACTED_REGISTRY_PATHS) {
  console.error(
    [
      `Endpoint registry uncontracted paths increased from ${MAX_UNCONTRACTED_REGISTRY_PATHS} to ${uncontracted.length}.`,
      "Add the missing backend Swagger serializer/path first, then switch the frontend endpoint to apiPath().",
      ...uncontracted
        .slice(0, 80)
        .map(({ line, value }) => `  - src/utils/axios.js:${line}: ${value}`),
    ].join("\n"),
  );
  process.exit(1);
}

console.log(
  [
    "Endpoint registry contract coverage:",
    `  raw registry paths: ${registryPaths.length}`,
    `  contracted by Swagger: ${registryPaths.length - uncontracted.length}`,
    `  uncontracted legacy paths: ${uncontracted.length}/${MAX_UNCONTRACTED_REGISTRY_PATHS}`,
  ].join("\n"),
);
