/**
 * Chat handler definitions for mock API responses.
 *
 * These are pure functions with no MSW dependency. Each handler matches a
 * route pattern from the real API and returns the same shape the backend
 * would. When MSW is added to package.json, these can be wired up as:
 *
 *   http.get('/api/v1/chat/threads', ({ request }) => {
 *     const url = new URL(request.url);
 *     const result = chatHandlerDefinitions['GET /api/v1/chat/threads']({
 *       scopeKind: url.searchParams.get('scope_kind') ?? undefined,
 *       channelId: url.searchParams.get('channel_id') ?? undefined,
 *       ...
 *     });
 *     return HttpResponse.json(result);
 *   });
 */

import {
  mockChannels,
  mockThreads,
  getHydratedThread,
  actors,
} from '../data/mock-chat';
import type {
  ChatMessage,
  ChatThread,
  ChatChannel,
  ChatScopeKind,
} from '../../types/chat';
import type { PaginatedResponse } from '../../types/common';

// ---------------------------------------------------------------------------
// Simulated agent responses by scope kind
// ---------------------------------------------------------------------------

const agentResponses: Record<string, string[]> = {
  approval: [
    "I've reviewed the proposed changes. The modifications look correct and meet all validation criteria. Recommending approval.",
    'Running validation checks on the updated design. All constraints pass. Ready for final sign-off.',
    'Found a minor issue during review: the net names on sheet 2 have been updated but the BOM still references the old designators. Please regenerate the BOM before approving.',
  ],
  'bom-entry': [
    'Checking supplier availability for this component. DigiKey shows 2,400 units in stock with a 4-week lead time for reel quantities.',
    'Found 3 alternative parts with compatible specifications. The most cost-effective option saves $0.12/unit at 1k volume.',
    'Cross-referencing this component against the ITAR and REACH compliance databases. No restrictions found for export to the target markets.',
  ],
  'digital-twin-node': [
    'Analyzing the impact of this change on downstream assemblies. 3 dependent nodes will need re-validation.',
    'Updated the Digital Twin graph. The constraint engine flagged 1 warning: the thermal budget for this node exceeds 90% of the design margin.',
    'Version diff generated. The change affects 2 geometry parameters and 1 material property. No interface changes detected.',
  ],
  session: [
    'Analysis session results are ready. Processing took 42 seconds. Full report attached to the work_product node.',
    'Mesh quality check passed. All elements have aspect ratio < 5:1. Proceeding with solver run.',
    'Simulation converged after 847 iterations. Maximum residual: 2.3e-6. Results are within the acceptance tolerance.',
  ],
  project: [
    'Project status summary: 12 of 18 tasks complete, 4 in progress, 2 blocked. Estimated completion: March 15.',
    'Detected a dependency conflict between the chassis redesign (MET-52) and the PCB layout update (MET-48). Recommend resolving MET-52 first.',
    'Weekly metrics: 3 DRC passes, 1 FEA run, 2 BOM updates. No open violations.',
  ],
};

/** Map from scope kind to the agent actor that typically responds. */
const scopeAgentMap = {
  approval: actors.agentEE,
  'bom-entry': actors.agentEE,
  'digital-twin-node': actors.agentME,
  session: actors.agentSIM,
  project: actors.agentSE,
};

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

export interface MockHandlerConfig {
  /** Simulated network latency in milliseconds (default: 150). */
  latencyMs?: number;
  /** Delay before an agent "types" back in milliseconds (default: 1200). */
  agentResponseDelayMs?: number;
}

export const defaultHandlerConfig: MockHandlerConfig = {
  latencyMs: 150,
  agentResponseDelayMs: 1_200,
};

// ---------------------------------------------------------------------------
// Handler definitions (route -> pure function)
// ---------------------------------------------------------------------------

export interface GetThreadsParams {
  scopeKind?: ChatScopeKind;
  channelId?: string;
  entityId?: string;
  includeArchived?: boolean;
  page?: number;
  pageSize?: number;
}

export interface CreateThreadBody {
  channelId: string;
  title: string;
  scope: {
    kind: ChatScopeKind;
    entityId: string;
    label?: string;
  };
}

export interface SendMessageBody {
  content: string;
  graphRef?: {
    nodeId: string;
    nodeType: string;
    label: string;
  };
}

export const chatHandlerDefinitions = {
  /**
   * GET /api/v1/chat/threads
   *
   * Returns a paginated, optionally filtered list of threads.
   */
  'GET /api/v1/chat/threads': (
    params?: GetThreadsParams,
  ): PaginatedResponse<ChatThread> => {
    let filtered = [...mockThreads];

    if (params?.scopeKind) {
      filtered = filtered.filter((t) => t.scope.kind === params.scopeKind);
    }
    if (params?.channelId) {
      filtered = filtered.filter((t) => t.channelId === params.channelId);
    }
    if (params?.entityId) {
      filtered = filtered.filter(
        (t) => t.scope.entityId === params.entityId,
      );
    }
    if (!params?.includeArchived) {
      filtered = filtered.filter((t) => !t.archived);
    }

    const page = params?.page ?? 1;
    const pageSize = params?.pageSize ?? 20;
    const start = (page - 1) * pageSize;
    const paged = filtered.slice(start, start + pageSize);

    return {
      data: paged,
      total: filtered.length,
      page,
      pageSize,
      totalPages: Math.ceil(filtered.length / pageSize),
    };
  },

  /**
   * GET /api/v1/chat/threads/:id
   *
   * Returns a single thread with its messages populated.
   */
  'GET /api/v1/chat/threads/:id': (
    id: string,
  ): ChatThread | { error: string; status: number } => {
    const thread = getHydratedThread(id);
    if (!thread) {
      return { error: 'Thread not found', status: 404 };
    }
    return thread;
  },

  /**
   * POST /api/v1/chat/threads
   *
   * Creates a new thread and returns it.
   */
  'POST /api/v1/chat/threads': (body: CreateThreadBody): ChatThread => {
    const now = new Date().toISOString();
    const newThread: ChatThread = {
      id: `th-new-${Date.now()}`,
      channelId: body.channelId,
      scope: body.scope,
      title: body.title,
      messages: [],
      participants: [actors.userAlex], // creator is first participant
      createdAt: now,
      lastMessageAt: now,
      archived: false,
    };
    return newThread;
  },

  /**
   * POST /api/v1/chat/threads/:id/messages
   *
   * Sends a user message in a thread and returns it.
   */
  'POST /api/v1/chat/threads/:id/messages': (
    threadId: string,
    body: SendMessageBody,
  ): ChatMessage => {
    const now = new Date().toISOString();
    const userMessage: ChatMessage = {
      id: `msg-${Date.now()}`,
      threadId,
      actor: actors.userAlex, // default sender for mock
      content: body.content,
      status: 'sent',
      createdAt: now,
      updatedAt: now,
      graphRef: body.graphRef
        ? {
            nodeId: body.graphRef.nodeId,
            nodeType: body.graphRef.nodeType,
            label: body.graphRef.label,
          }
        : undefined,
    };
    return userMessage;
  },

  /**
   * GET /api/v1/chat/channels
   *
   * Returns all channels.
   */
  'GET /api/v1/chat/channels': (): ChatChannel[] => {
    return [...mockChannels];
  },
} as const;

// ---------------------------------------------------------------------------
// Agent response generator
// ---------------------------------------------------------------------------

/**
 * Generates a realistic mock agent response for the given scope kind.
 * Picks a random canned response and wraps it in a ChatMessage.
 */
export function generateAgentResponse(
  threadId: string,
  scopeKind: string,
): ChatMessage {
  const responses =
    agentResponses[scopeKind] ?? ['Processing your request...'];
  const content = responses[Math.floor(Math.random() * responses.length)];
  const agent = scopeAgentMap[scopeKind as keyof typeof scopeAgentMap] ?? actors.agentSE;

  return {
    id: `msg-agent-${Date.now()}`,
    threadId,
    actor: agent,
    content: content ?? 'Processing your request...',
    status: 'sent',
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
}

/**
 * Generates a system notification message (e.g., status change, gate event).
 */
export function generateSystemMessage(
  threadId: string,
  content: string,
): ChatMessage {
  return {
    id: `msg-sys-${Date.now()}`,
    threadId,
    actor: actors.system,
    content,
    status: 'sent',
    createdAt: new Date().toISOString(),
  };
}
