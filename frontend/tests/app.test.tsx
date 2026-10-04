import { readFileSync } from "node:fs";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import type { Demo } from "../src/types";

const demo: Demo = JSON.parse(readFileSync("public/demo.json", "utf8"));
const fetchMock = vi.fn<typeof fetch>();
beforeEach(() => {
  sessionStorage.clear();
  vi.stubGlobal("fetch", fetchMock);
  HTMLElement.prototype.scrollIntoView = vi.fn();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  fetchMock.mockImplementation(async () => new Response(JSON.stringify(demo)));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

async function openCrown() {
  const title = await screen.findByRole("heading", {
    name: "Your crown estimate",
  });
  const row = title.closest("button");
  if (!row) throw new Error("Missing conversation row");
  fireEvent.click(row);
  await screen.findByText("Your call, made clearer.");
}

describe("Abe companion", () => {
  it("opens a call, shows amounts, and searches its transcript", async () => {
    const user = userEvent.setup();
    render(<App />);
    await openCrown();
    expect(screen.getAllByText("$600.00").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: /^Transcript/ }));
    const search = screen.getByPlaceholderText("Find a word or a detail");
    await user.type(search, "crown");
    expect(document.querySelectorAll("mark").length).toBeGreaterThan(0);
    await user.clear(search);
    await user.type(search, "word-that-is-not-present");
    expect(
      screen.getByText("No matching words in this transcript."),
    ).toBeTruthy();
  });
  it("filters and searches the conversation inbox", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("heading", { name: "Your crown estimate" });
    await user.click(screen.getByRole("button", { name: "Calls" }));
    expect(screen.queryByRole("heading", { name: "Chat with Abe" })).toBeNull();
    await user.type(
      screen.getByPlaceholderText("Search your conversations"),
      "no matching call",
    );
    expect(screen.getByText("No conversations found")).toBeTruthy();
  });
  it("requires a connection to send and never fakes a reply in preview", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("heading", { name: "Your crown estimate" });
    await user.click(
      screen.getAllByRole("button", { name: "Chat with Abe" })[0],
    );
    await user.type(
      await screen.findByRole("textbox", { name: "Message Abe" }),
      "How much is left?",
    );
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(
      screen.getByRole("dialog", { name: "Make yourself at home." }),
    ).toBeTruthy();
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("/chat/messages"),
      ),
    ).toBe(false);
  });
  it("sends a connected chat to the API and displays its response", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("abe-access", "test-app-key");
    const chat = structuredClone(demo.details["chat-with-abe"]);
    fetchMock.mockImplementation(async (url, options) => {
      if (String(url) === "/demo.json")
        return new Response(JSON.stringify(demo));
      if (String(url) === "/api/conversations")
        return new Response(JSON.stringify(demo.inbox));
      if (String(url) === "/api/chat/messages") {
        expect(options?.headers).toMatchObject({
          Authorization: "Bearer test-app-key",
        });
        expect(JSON.parse(String(options?.body)).text).toBe("Show my benefits");
        chat.messages.push({
          message_id: "reply-1",
          conversation_id: "chat-with-abe",
          role: "assistant",
          source: "text",
          created_at: new Date().toISOString(),
          text: "Tool-confirmed test response.",
        });
      }
      return new Response(JSON.stringify(chat));
    });
    render(<App />);
    await screen.findByRole("heading", { name: "Your crown estimate" });
    await user.click(
      screen.getAllByRole("button", { name: "Chat with Abe" })[0],
    );
    await user.type(
      await screen.findByRole("textbox", { name: "Message Abe" }),
      "Show my benefits{Enter}",
    );
    expect(
      await screen.findByText("Tool-confirmed test response."),
    ).toBeTruthy();
    await waitFor(() =>
      expect(
        (
          screen.getByRole("textbox", {
            name: "Message Abe",
          }) as HTMLTextAreaElement
        ).disabled,
      ).toBe(false),
    );
  });
  it("preserves a failed message for retry", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("abe-access", "test-app-key");
    fetchMock.mockImplementation(async (url) => {
      if (String(url) === "/demo.json")
        return new Response(JSON.stringify(demo));
      if (String(url) === "/api/conversations")
        return new Response(JSON.stringify(demo.inbox));
      if (String(url) === "/api/chat/messages")
        return new Response("{}", { status: 503 });
      return new Response(JSON.stringify(demo.details["chat-with-abe"]));
    });
    render(<App />);
    await screen.findByRole("heading", { name: "Your crown estimate" });
    await user.click(
      screen.getAllByRole("button", { name: "Chat with Abe" })[0],
    );
    const input = await screen.findByRole("textbox", { name: "Message Abe" });
    await user.type(input, "Please help{Enter}");
    await waitFor(() =>
      expect((input as HTMLTextAreaElement).value).toBe("Please help"),
    );
    expect(screen.getByRole("alert").textContent).toMatch(
      /couldn.t|unavailable|try again/i,
    );
  });
});
