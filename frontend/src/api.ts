import type { Detail, Inbox } from "./types";
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function request<T>(
  path: string,
  token: string,
  body?: unknown,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body ? "POST" : "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
    signal: AbortSignal.timeout(body ? 65000 : 10000),
    cache: "no-store",
  });
  if (!response.ok)
    throw new ApiError(
      response.status,
      response.status === 401
        ? "That access key didn’t work. Please try again."
        : response.status === 409
          ? "Abe is still responding. Give it a moment."
          : "We couldn’t connect to Abe. Please try again.",
    );
  return response.json() as Promise<T>;
}
export const getInbox = (token: string) =>
  request<Inbox>("/conversations", token);
export const getDetail = (id: string, token: string) =>
  request<Detail>(`/conversations/${encodeURIComponent(id)}`, token);
export const sendMessage = (text: string, token: string, context?: string) =>
  request<Detail>("/chat/messages", token, {
    text,
    ...(context ? { context_conversation_id: context } : {}),
  });
