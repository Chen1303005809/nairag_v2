import type {
  AvailableParent,
  ChildContentInput,
  KnowledgeBase,
  LoginResponse,
  ManagedKnowledgeBase,
  ParentContentInput,
  ReviewerAssignment,
  ReviewDecision,
  ReviewDecisionKind,
  ReviewQueueItem,
  ReviewSubmission,
  SearchResponse,
  HelpfulFeedbackResponse,
  TemporaryPasswordResponse,
  User,
  UserRole
} from "./types";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "/api/v1").replace(/\/$/, "");
const csrfCookieName = import.meta.env.VITE_CSRF_COOKIE_NAME ?? "nairag_csrf";
const preAuthCsrfCookieName =
  import.meta.env.VITE_PRE_AUTH_CSRF_COOKIE_NAME ?? "nairag_pre_auth_csrf";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string
  ) {
    super(detail);
  }
}

function apiUrl(path: string): string {
  return `${apiBaseUrl}${path}`;
}

function readCookie(name: string): string | null {
  const cookie = document.cookie
    .split("; ")
    .find((value) => value.startsWith(`${encodeURIComponent(name)}=`));
  return cookie ? decodeURIComponent(cookie.split("=").slice(1).join("=")) : null;
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.status === 204) {
    return undefined as T;
  }

  const payload: unknown = await response.json().catch(() => undefined);
  if (!response.ok) {
    const detail =
      typeof payload === "object" && payload !== null && "detail" in payload
        ? String(payload.detail)
        : "请求失败，请稍后重试";
    throw new ApiError(response.status, detail);
  }
  return payload as T;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(apiUrl(path), {
    credentials: "include",
    ...init
  });
  return parseResponse<T>(response);
}

function jsonRequest(method: string, body: object, csrfToken?: string): RequestInit {
  return {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {})
    },
    body: JSON.stringify(body)
  };
}

async function issuePreAuthCsrfToken(): Promise<string> {
  await request<void>("/auth/csrf");
  const token = readCookie(preAuthCsrfCookieName);
  if (!token) {
    throw new ApiError(403, "浏览器未接受登录 CSRF Cookie")
  }
  return token;
}

function sessionCsrfToken(): string {
  const token = readCookie(csrfCookieName);
  if (!token) {
    throw new ApiError(403, "登录 CSRF Cookie 已失效，请重新登录")
  }
  return token;
}

async function sessionMutation<T>(method: string, path: string, body?: object): Promise<T> {
  const csrfToken = sessionCsrfToken();
  return request<T>(
    path,
    body === undefined
      ? { method, headers: { "X-CSRF-Token": csrfToken } }
      : jsonRequest(method, body, csrfToken)
  );
}

export const api = {
  me: (): Promise<User> => request<User>("/auth/me"),

  login: async (username: string, password: string): Promise<LoginResponse> => {
    const csrfToken = await issuePreAuthCsrfToken();
    return request<LoginResponse>("/auth/login", jsonRequest("POST", { username, password }, csrfToken));
  },

  logout: (): Promise<void> => sessionMutation<void>("POST", "/auth/logout"),

  changePassword: (currentPassword: string, newPassword: string): Promise<LoginResponse> =>
    sessionMutation<LoginResponse>("POST", "/auth/change-password", {
      current_password: currentPassword,
      new_password: newPassword
    }),

  listUsers: (includeDisabled = true): Promise<User[]> =>
    request<User[]>(`/users?include_disabled=${includeDisabled}`),

  createUser: (
    username: string,
    displayName: string,
    role: UserRole
  ): Promise<TemporaryPasswordResponse> =>
    sessionMutation<TemporaryPasswordResponse>("POST", "/users", {
      username,
      display_name: displayName,
      role
    }),

  updateUser: (
    id: string,
    update: Partial<Pick<User, "display_name" | "role" | "is_active">>
  ): Promise<User> => sessionMutation<User>("PATCH", `/users/${id}`, update),

  resetUserPassword: (id: string): Promise<TemporaryPasswordResponse> =>
    sessionMutation<TemporaryPasswordResponse>("POST", `/users/${id}/reset-password`),

  listKnowledgeBases: (): Promise<KnowledgeBase[]> => request<KnowledgeBase[]>("/knowledge-bases"),

  listManagedKnowledgeBases: (): Promise<ManagedKnowledgeBase[]> =>
    request<ManagedKnowledgeBase[]>("/knowledge-bases/admin"),

  listAssignedReviewKnowledgeBases: (): Promise<KnowledgeBase[]> =>
    request<KnowledgeBase[]>("/knowledge-bases/assigned-to-me"),

  createKnowledgeBase: (
    logicalKey: string,
    name: string,
    description: string | null,
    isActive: boolean
  ): Promise<ManagedKnowledgeBase> =>
    sessionMutation<ManagedKnowledgeBase>("POST", "/knowledge-bases", {
      logical_key: logicalKey,
      name,
      description,
      is_active: isActive
    }),

  updateKnowledgeBase: (
    id: string,
    update: Partial<Pick<KnowledgeBase, "name" | "description" | "is_active">>
  ): Promise<ManagedKnowledgeBase> =>
    sessionMutation<ManagedKnowledgeBase>("PATCH", `/knowledge-bases/${id}`, update),

  listKnowledgeBaseReviewers: (id: string): Promise<ReviewerAssignment[]> =>
    request<ReviewerAssignment[]>(`/knowledge-bases/admin/${id}/reviewers`),

  assignKnowledgeBaseReviewer: (knowledgeBaseId: string, reviewerUserId: string): Promise<ReviewerAssignment> =>
    sessionMutation<ReviewerAssignment>(
      "PUT",
      `/knowledge-bases/${knowledgeBaseId}/reviewers/${reviewerUserId}`
    ),

  unassignKnowledgeBaseReviewer: (knowledgeBaseId: string, reviewerUserId: string): Promise<void> =>
    sessionMutation<void>(
      "DELETE",
      `/knowledge-bases/${knowledgeBaseId}/reviewers/${reviewerUserId}`
    ),

  listAvailableParents: (): Promise<AvailableParent[]> =>
    request<AvailableParent[]>("/knowledge-content/parents/available"),

  listMyContentSubmissions: (): Promise<ReviewSubmission[]> =>
    request<ReviewSubmission[]>("/knowledge-content/submissions/mine"),

  listReviewQueue: (knowledgeBaseId?: string): Promise<ReviewQueueItem[]> =>
    request<ReviewQueueItem[]>(
      `/knowledge-content/review-queue${knowledgeBaseId ? `?knowledge_base_id=${knowledgeBaseId}` : ""}`
    ),

  decideReviewTarget: (
    submissionId: string,
    knowledgeBaseId: string,
    decision: ReviewDecisionKind,
    comment?: string
  ): Promise<ReviewDecision> =>
    sessionMutation<ReviewDecision>(
      "POST",
      `/knowledge-content/review-submissions/${submissionId}/targets/${knowledgeBaseId}/decision`,
      { decision, comment: comment || null }
    ),

  search: (query: string, knowledgeBaseId?: string): Promise<SearchResponse> =>
    sessionMutation<SearchResponse>("POST", "/search", {
      query,
      knowledge_base_id: knowledgeBaseId || null,
      limit: 10
    }),

  submitHelpfulFeedback: (
    searchEventId: string,
    resultItemId: string
  ): Promise<HelpfulFeedbackResponse> =>
    sessionMutation<HelpfulFeedbackResponse>(
      "POST",
      `/search/events/${searchEventId}/feedback`,
      { result_item_id: resultItemId }
    ),

  createParentSubmission: (
    parent: ParentContentInput,
    primaryChild: ChildContentInput,
    knowledgeBaseIds: string[]
  ): Promise<ReviewSubmission> =>
    sessionMutation<ReviewSubmission>("POST", "/knowledge-content/parent-submissions", {
      parent,
      primary_child: primaryChild,
      knowledge_base_ids: knowledgeBaseIds
    }),

  createChildSubmission: (
    parentId: string,
    child: ChildContentInput,
    knowledgeBaseIds: string[]
  ): Promise<ReviewSubmission> =>
    sessionMutation<ReviewSubmission>("POST", "/knowledge-content/child-submissions", {
      parent_id: parentId,
      child,
      knowledge_base_ids: knowledgeBaseIds
    }),

  resubmitRejectedParent: (
    reviewSubmissionId: string,
    parent: ParentContentInput,
    primaryChild: ChildContentInput
  ): Promise<ReviewSubmission> =>
    sessionMutation<ReviewSubmission>(
      "POST",
      `/knowledge-content/review-submissions/${reviewSubmissionId}/resubmit-parent`,
      { parent, primary_child: primaryChild }
    ),

  resubmitRejectedChild: (
    reviewSubmissionId: string,
    child: ChildContentInput,
    knowledgeBaseIds: string[]
  ): Promise<ReviewSubmission> =>
    sessionMutation<ReviewSubmission>(
      "POST",
      `/knowledge-content/review-submissions/${reviewSubmissionId}/resubmit-child`,
      { child, knowledge_base_ids: knowledgeBaseIds }
    )
};
