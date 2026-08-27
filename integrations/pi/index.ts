import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { join } from "node:path";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import {
  getDefaultEnvironment,
  StdioClientTransport,
} from "@modelcontextprotocol/sdk/client/stdio.js";
import {
  DEFAULT_MAX_BYTES,
  DEFAULT_MAX_LINES,
  truncateHead,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { Type, type TSchema } from "typebox";

const EXPECTED_TOOLS = new Set([
  "cancel_run",
  "doctor",
  "get_run_status",
  "get_run_summary",
  "inspect_model",
  "list_run_artifacts",
  "plan_run",
  "resolve_regions",
  "start_run",
  "validate_run",
]);

const recipePath = Type.Object({
  recipe_path: Type.String({ description: "Recipe path relative to the Pi project root" }),
});
const recipePathWithRunId = Type.Object({
  recipe_path: Type.String({ description: "Recipe path relative to the Pi project root" }),
  run_id: Type.Optional(Type.String({ description: "Optional durable run identifier" })),
});
const runId = Type.Object({
  run_id: Type.String({ description: "Durable run identifier" }),
});

function runtimeRoot(cwd: string): string {
  const configRoot =
    process.env.PI_CODING_AGENT_DIR ?? join(homedir(), ".pi", "agent");
  const projectKey = createHash("sha256").update(cwd.toLowerCase()).digest("hex").slice(0, 16);
  return join(configRoot, "runtime", "ansys-research-runner", projectKey);
}

type McpToolResult = {
  content?: Array<{ type: string; text?: string }>;
  isError?: boolean;
  structuredContent?: unknown;
};

function textFromResult(result: McpToolResult): string {
  if (result.structuredContent !== undefined) {
    return JSON.stringify(result.structuredContent, null, 2);
  }
  const text = (result.content ?? [])
    .filter((item) => item.type === "text" && typeof item.text === "string")
    .map((item) => item.text)
    .join("\n");
  return text || JSON.stringify(result, null, 2);
}

export default function ansysResearchMcpExtension(pi: ExtensionAPI) {
  let client: Client | undefined;
  let transport: StdioClientTransport | undefined;
  let connection: Promise<Client> | undefined;
  let connectedTools: string[] = [];
  let stderrTail = "";

  const close = async (): Promise<void> => {
    const activeClient = client;
    client = undefined;
    transport = undefined;
    connection = undefined;
    connectedTools = [];
    if (activeClient) {
      await activeClient.close().catch(() => undefined);
    }
  };

  const connect = async (cwd: string): Promise<Client> => {
    if (client) return client;
    if (connection) return connection;

    connection = (async () => {
      const nextTransport = new StdioClientTransport({
        command: "ansys-research-mcp",
        args: ["--transport", "stdio"],
        cwd,
        env: {
          ...getDefaultEnvironment(),
          ANSYS_RESEARCH_ROOT: cwd,
          ANSYS_RESEARCH_RUNTIME: runtimeRoot(cwd),
        },
        stderr: "pipe",
      });
      nextTransport.stderr?.on("data", (chunk) => {
        stderrTail = `${stderrTail}${String(chunk)}`.slice(-4000);
      });
      const nextClient = new Client(
        { name: "pi-ansys-research-runner", version: "0.13.0" },
        { capabilities: {} },
      );
      try {
        await nextClient.connect(nextTransport);
        const listed = await nextClient.listTools(undefined, { timeout: 30_000 });
        const names = listed.tools.map((tool) => tool.name).sort();
        const unexpected = names.filter((name) => !EXPECTED_TOOLS.has(name));
        const missing = [...EXPECTED_TOOLS].filter((name) => !names.includes(name));
        if (unexpected.length > 0 || missing.length > 0) {
          throw new Error(
            `MCP tool boundary mismatch; missing=${missing.join(",") || "none"}; ` +
              `unexpected=${unexpected.join(",") || "none"}`,
          );
        }
        transport = nextTransport;
        client = nextClient;
        connectedTools = names;
        return nextClient;
      } catch (error) {
        await nextClient.close().catch(() => undefined);
        connection = undefined;
        const detail = stderrTail.trim();
        throw new Error(
          `${error instanceof Error ? error.message : String(error)}${
            detail ? `; server stderr: ${detail}` : ""
          }`,
        );
      }
    })();

    return connection;
  };

  const callMcp = async (
    name: string,
    parameters: Record<string, unknown>,
    cwd: string,
    signal?: AbortSignal,
  ) => {
    const activeClient = await connect(cwd);
    const result = await activeClient.callTool(
      { name, arguments: parameters },
      undefined,
      {
        signal,
        timeout: 300_000,
        maxTotalTimeout: 600_000,
      },
    );
    const typedResult = result as McpToolResult;
    const rawText = textFromResult(typedResult);
    const truncated = truncateHead(rawText, {
      maxBytes: DEFAULT_MAX_BYTES,
      maxLines: DEFAULT_MAX_LINES,
    });
    const text = truncated.truncated
      ? `${truncated.content}\n\n[Output truncated to Pi's tool-output boundary.]`
      : truncated.content;
    if (typedResult.isError) throw new Error(text);
    return {
      content: [{ type: "text" as const, text }],
      details: {
        mcpTool: name,
        structuredContent: typedResult.structuredContent,
        truncated: truncated.truncated,
      },
    };
  };

  const register = <Schema extends TSchema>(definition: {
    name: string;
    label: string;
    description: string;
    parameters: Schema;
  }): void => {
    pi.registerTool({
      ...definition,
      async execute(_toolCallId, parameters, signal, _onUpdate, ctx) {
        return callMcp(
          definition.name,
          parameters as Record<string, unknown>,
          ctx.cwd,
          signal,
        );
      },
    });
  };

  register({
    name: "doctor",
    label: "Ansys Doctor",
    description: "Inspect bounded host and installed Ansys capabilities without running a model",
    parameters: Type.Object({
      live: Type.Optional(Type.Boolean({ description: "Run bounded live capability probes" })),
      timeout_seconds: Type.Optional(
        Type.Number({ minimum: 1, description: "Timeout for each live probe" }),
      ),
    }),
  });
  register({
    name: "inspect_model",
    label: "Inspect Ansys Model",
    description: "Inspect one confined supported CAD model into a solver-neutral Geometry Graph",
    parameters: Type.Object({
      model_path: Type.String({ description: "Model path relative to the Pi project root" }),
    }),
  });
  register({
    name: "resolve_regions",
    label: "Resolve Ansys Regions",
    description: "Resolve semantic regions referenced by one confined thermal recipe",
    parameters: recipePath,
  });
  register({
    name: "validate_run",
    label: "Validate Ansys Run",
    description: "Validate one confined thermal recipe and its closed contracts",
    parameters: recipePath,
  });
  register({
    name: "plan_run",
    label: "Plan Ansys Run",
    description: "Compile a validated thermal recipe into immutable reviewed CAE-IR",
    parameters: recipePathWithRunId,
  });
  register({
    name: "start_run",
    label: "Start Ansys Run",
    description: "Enqueue a validated thermal run and start the detached registry worker",
    parameters: recipePathWithRunId,
  });
  register({
    name: "get_run_status",
    label: "Get Ansys Run Status",
    description: "Read a durable run snapshot and append-only event history",
    parameters: runId,
  });
  register({
    name: "cancel_run",
    label: "Cancel Ansys Run",
    description: "Request safe cancellation through the durable Job Registry",
    parameters: runId,
  });
  register({
    name: "get_run_summary",
    label: "Get Ansys Run Summary",
    description: "Return bounded scalar results without field arrays",
    parameters: runId,
  });
  register({
    name: "list_run_artifacts",
    label: "List Ansys Run Artifacts",
    description: "List run-owned artifact paths, hashes, media types, and sizes",
    parameters: runId,
  });

  pi.on("session_start", async (_event, ctx) => {
    try {
      await connect(ctx.cwd);
      ctx.ui.setStatus("ansys-mcp", `Ansys MCP: ${connectedTools.length} tools`);
    } catch (error) {
      ctx.ui.setStatus("ansys-mcp", "Ansys MCP: unavailable");
      ctx.ui.notify(
        `Ansys MCP unavailable: ${error instanceof Error ? error.message : String(error)}`,
        "warning",
      );
    }
  });

  pi.on("session_shutdown", async (_event, ctx) => {
    ctx.ui.setStatus("ansys-mcp", undefined);
    await close();
  });

  pi.registerCommand("ansys-mcp-status", {
    description: "Show the local ansys-research-runner MCP connection status",
    handler: async (_args, ctx) => {
      try {
        await connect(ctx.cwd);
        ctx.ui.notify(`Ansys MCP connected: ${connectedTools.join(", ")}`, "info");
      } catch (error) {
        ctx.ui.notify(
          `Ansys MCP unavailable: ${error instanceof Error ? error.message : String(error)}`,
          "error",
        );
      }
    },
  });
}
