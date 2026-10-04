import {
  ArrowUp,
  ArrowUpRight,
  ChevronLeft,
  Download,
  FileText,
  Headphones,
  LockKeyhole,
  LogOut,
  MessageCircle,
  Phone,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  Wallet,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { getDetail, getInbox, sendMessage } from "./api";
import type { Conversation, Demo, Detail, Inbox, Recap } from "./types";

const CHAT = "chat-with-abe";
const money = (value?: string | null) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 2,
      }).format(Number(value));
const dateLabel = (date: string) =>
  new Date(date).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
const timeLabel = (date: string) =>
  new Date(date).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
  });
const humanProcedure = (value?: string | null) =>
  value
    ?.replaceAll("_", " ")
    .toLowerCase()
    .replace(/^./, (x) => x.toUpperCase()) || "Benefits conversation";
function Avatar({ small = false }: { small?: boolean }) {
  return (
    <span className={`avatar ${small ? "small" : ""}`} aria-hidden="true">
      <span>A</span>
      <i />
    </span>
  );
}
function RowIcon({ row }: { row: Conversation }) {
  return row.icon === "abe" || row.icon === "spark" ? (
    <Avatar small />
  ) : (
    <span className="row-icon">
      {row.icon === "wallet" ? (
        <Wallet size={20} />
      ) : row.channel === "voice" ? (
        <Phone size={19} />
      ) : (
        <Stethoscope size={21} />
      )}
    </span>
  );
}
function Highlight({ text, query }: { text: string; query: string }) {
  if (!query.trim()) return <>{text}</>;
  const parts = text.split(
    new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"),
  );
  let offset = 0;
  return (
    <>
      {parts.map((part) => {
        const key = offset;
        offset += part.length;
        return part.toLowerCase() === query.toLowerCase() ? (
          <mark key={key}>{part}</mark>
        ) : (
          part
        );
      })}
    </>
  );
}
function Summary({
  recap,
  sample = false,
}: {
  recap?: Recap | null;
  sample?: boolean;
}) {
  if (!recap)
    return (
      <div className="empty-summary">
        <Sparkles size={23} />
        <h3>A little clarity, coming up.</h3>
        <p>
          Benefits, costs and provider details appear here when Abe verifies
          them during your conversation.
        </p>
      </div>
    );
  return (
    <div className="summary-content">
      <div className="section-eyebrow">
        <Sparkles size={14} /> THE IMPORTANT THINGS
      </div>
      <h2>{humanProcedure(recap.procedure)}</h2>
      <p className="summary-intro">The details, without the dental jargon.</p>
      {recap.estimated_member_payment != null && (
        <div className="estimate-card">
          <span>Your estimated cost</span>
          <strong>{money(recap.estimated_member_payment)}</strong>
          <div className="estimate-rule" />
          <div>
            <span>Estimated plan payment</span>
            <b>{money(recap.plan_payment)}</b>
          </div>
          <small>Estimate only. Final coverage and costs may vary.</small>
        </div>
      )}
      <div className="fact-grid">
        {recap.annual_maximum_remaining != null && (
          <div>
            <ShieldCheck size={18} />
            <strong>{money(recap.annual_maximum_remaining)}</strong>
            <span>Benefits remaining</span>
          </div>
        )}
        {recap.fsa_balance != null && (
          <div>
            <Wallet size={18} />
            <strong>{money(recap.fsa_balance)}</strong>
            <span>Stored FSA balance</span>
          </div>
        )}
      </div>
      {recap.provider_name && (
        <div className="provider-card">
          <div className="row-icon">
            <Stethoscope size={20} />
          </div>
          <div>
            <span>PROVIDER DISCUSSED</span>
            <h4>{recap.provider_name}</h4>
            {recap.network_status && (
              <small className="network-label">
                {recap.network_status === "in_network"
                  ? "In network"
                  : "Out of network"}{" "}
                · Synthetic provider
              </small>
            )}
          </div>
        </div>
      )}
      {recap.annual_maximum_remaining_afterward != null && (
        <p className="balance-note">
          After this estimate,{" "}
          <b>{money(recap.annual_maximum_remaining_afterward)}</b> would remain
          in your annual maximum. No benefits have been spent or reserved.
        </p>
      )}
      {!!recap.constraints.length && (
        <div className="notes">
          <span className="section-eyebrow">YOU MENTIONED</span>
          {recap.constraints.map((text) => (
            <p key={text}>{text}</p>
          ))}
        </div>
      )}
      {!!recap.sources?.length && (
        <div className="source-list">
          <span className="section-eyebrow">PLAN SOURCES</span>
          {recap.sources.map((source) => (
            <p key={`${source.source_id}-${source.section}`}>
              <FileText size={14} />
              <span>
                {source.document}
                {source.section ? ` · ${source.section}` : ""}
                {source.page ? `, page ${source.page}` : ""}
              </span>
            </p>
          ))}
        </div>
      )}
      <p className="provenance">
        <ShieldCheck size={13} />
        {sample || recap.origin === "sample"
          ? "Sample summary · synthetic demo data"
          : "From Abe’s tools · synthetic demo data"}
      </p>
    </div>
  );
}
export default function App() {
  const [token, setToken] = useState(
    () => sessionStorage.getItem("abe-access") || "",
  );
  const [demo, setDemo] = useState<Demo>();
  const [inbox, setInbox] = useState<Inbox>();
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail>();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "voice" | "text">("all");
  const [tab, setTab] = useState<"overview" | "transcript">("overview");
  const [transcriptQuery, setTranscriptQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [pendingText, setPendingText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [modal, setModal] = useState<"connect" | "call" | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [context, setContext] = useState<Conversation>();
  const bottom = useRef<HTMLDivElement>(null);
  const modalRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (modal) modalRef.current?.showModal();
  }, [modal]);
  const preview = !token;
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  useEffect(() => {
    fetch("/demo.json")
      .then((r) => r.json())
      .then((data: Demo) => {
        setDemo(data);
        if (!token) setInbox(data.inbox);
      })
      .catch(() =>
        setError("The sample conversations couldn’t load. Please refresh."),
      );
  }, [token]);
  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const data = await getInbox(token);
      setInbox(data);
      if (selectedRef.current) {
        const current = await getDetail(selectedRef.current, token);
        if (selectedRef.current === current.conversation.conversation_id)
          setDetail(current);
      }
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Couldn’t refresh conversations.",
      );
    }
  }, [token]);
  useEffect(() => {
    if (!token || sending) return;
    void refresh();
    const interval = setInterval(() => {
      if (!sending && document.visibilityState === "visible") void refresh();
    }, 10000);
    return () => clearInterval(interval);
  }, [token, refresh, sending]);
  useEffect(() => {
    if (!selected) return;
    let active = true;
    setLoading(true);
    setDetail(undefined);
    setTranscriptQuery("");
    const task = token
      ? getDetail(selected, token)
      : Promise.resolve(demo?.details[selected]);
    task
      .then((value) => {
        if (active) setDetail(value);
      })
      .catch((e) => {
        if (active)
          setError(
            e instanceof Error ? e.message : "Couldn’t load conversation.",
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selected, token, demo]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: Scroll on newly arrived messages and typing status.
  useEffect(() => {
    if (selected === CHAT)
      bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [detail?.messages.length, selected, sending]);
  const open = (id: string) => {
    setSelected(id);
    setTab("overview");
    setError("");
  };
  const continueChat = (row: Conversation, prompt?: string) => {
    setContext(row);
    open(CHAT);
    setDraft(
      prompt ||
        `Can you explain the key details from my ${row.title.toLowerCase()} conversation?`,
    );
  };
  async function send() {
    if (!draft.trim() || sending) return;
    if (preview) {
      setModal("connect");
      return;
    }
    const message = draft.trim();
    setSending(true);
    setPendingText(message);
    setError("");
    setDraft("");
    try {
      const result = await sendMessage(
        message,
        token,
        context?.conversation_id,
      );
      if (selectedRef.current === CHAT) setDetail(result);
      void getInbox(token)
        .then(setInbox)
        .catch(() => {});
    } catch (e) {
      setDraft(message);
      setError(
        e instanceof Error ? e.message : "Your message couldn’t be sent.",
      );
    } finally {
      setPendingText("");
      setSending(false);
    }
  }
  async function connect() {
    setConnecting(true);
    setError("");
    try {
      const data = await getInbox(keyInput.trim());
      sessionStorage.setItem("abe-access", keyInput.trim());
      setToken(keyInput.trim());
      setInbox(data);
      setSelected(null);
      setKeyInput("");
      setModal(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn’t connect.");
    } finally {
      setConnecting(false);
    }
  }
  function exportTranscript() {
    if (!detail) return;
    const content = [
      `${detail.conversation.title} — Abe`,
      "Synthetic demo conversation",
      "",
      ...detail.messages.map(
        (m) =>
          `${new Date(m.created_at).toLocaleString()} · ${m.role === "user" ? "You" : "Abe (AI)"}\n${m.text}\n`,
      ),
    ].join("\n");
    const url = URL.createObjectURL(
      new Blob([content], { type: "text/plain;charset=utf-8" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `abe-${detail.conversation.conversation_id}.txt`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const rows = (inbox?.conversations || []).filter(
    (row) =>
      (filter === "all" || row.channel === filter) &&
      `${row.title} ${row.preview} ${row.recap?.provider_name || ""}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const active = detail?.conversation;
  const isChat = selected === CHAT;
  const stats = inbox?.conversations.find(
    (c) => c.recap?.annual_maximum_remaining,
  )?.recap;
  const sample =
    preview ||
    active?.recap?.origin === "sample" ||
    (!!detail?.messages.length &&
      detail.messages.every((m) => m.source === "seed"));
  return (
    <div className="app-shell">
      <aside className="rail">
        <button
          type="button"
          className="brand-mark"
          aria-label="Abe home"
          onClick={() => setSelected(null)}
        >
          a<span>be</span>
          <i />
        </button>
        <div className="rail-links">
          <button
            type="button"
            className="rail-active"
            aria-label="Conversations"
            onClick={() => setSelected(null)}
          >
            <MessageCircle size={21} />
          </button>
          <button
            type="button"
            aria-label="Chat with Abe"
            onClick={() => open(CHAT)}
          >
            <Sparkles size={21} />
          </button>
          <button
            type="button"
            aria-label="Call Abe"
            onClick={() => setModal("call")}
          >
            <Phone size={20} />
          </button>
        </div>
        <div className="rail-bottom">
          <ShieldCheck size={20} />
          <button
            type="button"
            className="profile-button"
            aria-label={
              preview ? "Connect to your demo" : "Disconnect from demo"
            }
            onClick={() => {
              if (preview) setModal("connect");
              else {
                sessionStorage.removeItem("abe-access");
                setToken("");
                setSelected(null);
              }
            }}
          >
            {preview ? "S" : <LogOut size={17} />}
          </button>
        </div>
      </aside>
      <main className={`workspace ${selected ? "has-selection" : ""}`}>
        <header className="workspace-header">
          <div>
            <span className="wordmark">
              Abe
              <span className="brand-dot" />
            </span>
            <span className="header-divider" />
            <span className="header-caption">YOUR BENEFITS, SIMPLIFIED.</span>
          </div>
          <button
            type="button"
            className={`connection-pill ${preview ? "preview" : ""}`}
            onClick={() => (preview ? setModal("connect") : void refresh())}
          >
            <span />
            {preview ? "Explore demo" : "Connected workspace"}
            {preview ? <ArrowUpRight size={13} /> : <RefreshCw size={12} />}
          </button>
        </header>
        <div className="workspace-body">
          <section className="inbox-panel" aria-label="Conversations">
            <div className="inbox-heading">
              <div>
                <span className="section-eyebrow">
                  A LITTLE HELP GOES A LONG WAY
                </span>
                <h1>
                  Your conversations<span>.</span>
                </h1>
              </div>
              <button
                type="button"
                className="new-chat"
                aria-label="Start chatting with Abe"
                onClick={() => open(CHAT)}
              >
                <MessageCircle size={19} />
              </button>
            </div>
            <button
              type="button"
              className="meet-card"
              onClick={() => open(CHAT)}
            >
              <Avatar />
              <div>
                <h2>Meet Abe.</h2>
                <p>
                  Your AI dental benefits guide.
                  <br />
                  Here to make things clearer.
                </p>
                <span>
                  Let’s talk <ArrowUpRight size={14} />
                </span>
              </div>
              <span className="meet-orbit" />
            </button>
            <div className="search-box">
              <Search size={17} />
              <input
                aria-label="Search conversations"
                placeholder="Search your conversations"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              {query && (
                <button
                  type="button"
                  aria-label="Clear search"
                  onClick={() => setQuery("")}
                >
                  <X size={14} />
                </button>
              )}
            </div>
            <nav className="filters" aria-label="Filter conversations">
              {(
                [
                  ["all", "All"],
                  ["voice", "Calls"],
                  ["text", "Messages"],
                ] as const
              ).map(([value, label]) => (
                <button
                  type="button"
                  key={value}
                  className={filter === value ? "selected" : ""}
                  onClick={() => setFilter(value)}
                >
                  {label}
                  {value === "all" && (
                    <span>{inbox?.conversations.length || 0}</span>
                  )}
                </button>
              ))}
            </nav>
            <div className="conversation-list">
              {rows.map((row) => (
                <button
                  type="button"
                  className={`conversation-row ${selected === row.conversation_id ? "active" : ""}`}
                  key={row.conversation_id}
                  onClick={() => open(row.conversation_id)}
                >
                  <RowIcon row={row} />
                  <div className="row-copy">
                    <div className="row-title">
                      <h3>{row.title}</h3>
                      <time>{dateLabel(row.updated_at)}</time>
                    </div>
                    <p>{row.preview}</p>
                    <div className="row-meta">
                      <span
                        className={`tag ${row.channel === "voice" ? "rose" : ""}`}
                      >
                        {row.channel === "voice" ? (
                          <Phone size={10} />
                        ) : (
                          <MessageCircle size={10} />
                        )}{" "}
                        {row.tag}
                      </span>
                      {row.status === "in_progress" ? (
                        <span className="live-label">Live call</span>
                      ) : row.pinned ? (
                        <span className="pinned-label">YOUR AI GUIDE</span>
                      ) : null}
                    </div>
                  </div>
                </button>
              ))}
              {!rows.length && (
                <div className="no-results">
                  <Search size={23} />
                  <h3>No conversations found</h3>
                  <p>Try another search or start a chat with Abe.</p>
                </div>
              )}
            </div>
            <div className="inbox-footer">
              <ShieldCheck size={14} />
              <span>Made for clarity. Built around you.</span>
            </div>
          </section>
          <section
            className="detail-panel"
            aria-label={isChat ? "Chat with Abe" : "Conversation details"}
          >
            {!selected ? (
              <div className="welcome">
                <div className="welcome-topline">
                  <span className="tiny-dot" /> YOUR DENTAL BENEFITS COMPANION
                </div>
                <div className="welcome-art">
                  <div className="art-ring one" />
                  <div className="art-ring two" />
                  <Avatar />
                  <span className="art-badge badge-chat">
                    <MessageCircle size={20} />
                  </span>
                  <span className="art-badge badge-shield">
                    <ShieldCheck size={23} />
                  </span>
                  <span className="art-dot" />
                </div>
                <span className="section-eyebrow">
                  HI, {inbox?.profile.display_name.toUpperCase() || "THERE"}.
                </span>
                <h2>
                  A little clarity.
                  <br />
                  <em>A lot less worry.</em>
                </h2>
                <p>
                  All your calls, helpful details, and next questions.
                  <br />
                  Together in one calm place.
                </p>
                <button
                  type="button"
                  className="primary"
                  onClick={() => open(CHAT)}
                >
                  Chat with Abe <ArrowUpRight size={17} />
                </button>
                <button
                  type="button"
                  className="subtle-call"
                  onClick={() => setModal("call")}
                >
                  <Phone size={14} /> More of a phone person? Call Abe
                </button>
                <div className="welcome-benefits">
                  <div>
                    <ShieldCheck size={17} />
                    <span>Annual benefit remaining</span>
                    <strong>{money(stats?.annual_maximum_remaining)}</strong>
                  </div>
                  <div>
                    <Wallet size={17} />
                    <span>Stored FSA balance</span>
                    <strong>{money(stats?.fsa_balance)}</strong>
                  </div>
                </div>
                <small>
                  Illustrative demo balances · not a coverage guarantee
                </small>
              </div>
            ) : (
              <>
                <header className="detail-header">
                  <button
                    type="button"
                    className="mobile-back icon-button"
                    aria-label="Back to conversations"
                    onClick={() => setSelected(null)}
                  >
                    <ChevronLeft />
                  </button>
                  {isChat ? (
                    <Avatar small />
                  ) : (
                    <span className="row-icon">
                      <Phone size={19} />
                    </span>
                  )}
                  <div>
                    <h2>
                      {isChat
                        ? "Chat with Abe"
                        : active?.title || "Conversation"}
                    </h2>
                    <p>
                      {isChat ? (
                        <>
                          <span className="online-dot" />
                          Your AI benefits guide
                        </>
                      ) : active ? (
                        `${dateLabel(active.updated_at)} · ${active.channel === "voice" ? "Phone conversation" : "Saved conversation"}`
                      ) : (
                        "Loading…"
                      )}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="detail-call icon-button"
                    aria-label="Call Abe"
                    onClick={() => setModal("call")}
                  >
                    <Phone size={19} />
                  </button>
                </header>
                {error && (
                  <div className="error-banner" role="alert">
                    {error}
                    <button type="button" onClick={() => void refresh()}>
                      Retry
                    </button>
                  </div>
                )}
                {sample && (
                  <div className="sample-strip">
                    <span className="tiny-dot" /> Sample conversation ·
                    synthetic data{" "}
                    {preview && (
                      <button type="button" onClick={() => setModal("connect")}>
                        Connect to Abe <ArrowUpRight size={12} />
                      </button>
                    )}
                  </div>
                )}
                {active?.status === "partial" && (
                  <div className="error-banner">
                    Some of this call’s transcript could not be saved.
                  </div>
                )}
                {!isChat && (
                  <div className="detail-tabs">
                    <button
                      type="button"
                      className={tab === "overview" ? "active" : ""}
                      onClick={() => setTab("overview")}
                    >
                      <Sparkles size={15} /> Overview
                    </button>
                    <button
                      type="button"
                      className={tab === "transcript" ? "active" : ""}
                      onClick={() => setTab("transcript")}
                    >
                      <FileText size={15} /> Transcript{" "}
                      <span>{detail?.messages.length || 0}</span>
                    </button>
                    <button
                      type="button"
                      className="export-button"
                      onClick={exportTranscript}
                      aria-label="Download transcript"
                    >
                      <Download size={16} />
                      <span>Export</span>
                    </button>
                  </div>
                )}
                {loading ? (
                  <div className="loading-state" role="status">
                    <div className="loading-dot" />
                    Gathering your conversation…
                  </div>
                ) : !detail ? (
                  <div className="loading-state">
                    Select a conversation to get started.
                  </div>
                ) : isChat ? (
                  <>
                    <div className="chat-scroll">
                      <div className="chat-date">
                        {dateLabel(
                          detail.messages[0]?.created_at ||
                            active?.updated_at ||
                            new Date().toISOString(),
                        )}
                      </div>
                      {detail.messages.map((m) => (
                        <div key={m.message_id} className={`message ${m.role}`}>
                          <div className="message-body">{m.text}</div>
                          <span>
                            {m.role === "assistant" ? "Abe · AI" : "You"} ·{" "}
                            {timeLabel(m.created_at)}
                          </span>
                        </div>
                      ))}
                      {detail.conversation.recap?.estimated_member_payment && (
                        <div className="inline-summary">
                          <Summary recap={detail.conversation.recap} />
                        </div>
                      )}
                      {!sending && detail.messages.length < 3 && (
                        <div className="starter-prompts">
                          <span>NOT SURE WHERE TO START?</span>
                          {[
                            "How much dental benefit do I have left?",
                            "Help me understand a crown estimate.",
                            "What did we discuss on my last call?",
                          ].map((prompt) => (
                            <button
                              type="button"
                              key={prompt}
                              onClick={() => setDraft(prompt)}
                            >
                              {prompt}
                              <ArrowUpRight size={14} />
                            </button>
                          ))}
                        </div>
                      )}
                      {sending && pendingText && (
                        <div className="message user">
                          <div className="message-body">{pendingText}</div>
                          <span>Sending…</span>
                        </div>
                      )}
                      {sending && (
                        <div className="message assistant">
                          <div className="thinking" role="status">
                            <i />
                            <i />
                            <i />
                            <span>Abe is looking into it</span>
                          </div>
                        </div>
                      )}
                      <div ref={bottom} />
                    </div>
                    <div className="composer-area">
                      {context && (
                        <div className="context-chip">
                          <Phone size={12} />
                          <span>Following up on {context.title}</span>
                          <button
                            type="button"
                            aria-label="Remove call context"
                            onClick={() => setContext(undefined)}
                          >
                            <X size={12} />
                          </button>
                        </div>
                      )}
                      <form
                        className="composer"
                        onSubmit={(e) => {
                          e.preventDefault();
                          void send();
                        }}
                      >
                        <textarea
                          aria-label="Message Abe"
                          placeholder="Ask Abe anything about your benefits…"
                          value={draft}
                          maxLength={2000}
                          disabled={sending}
                          onChange={(e) => setDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (
                              e.key === "Enter" &&
                              !e.shiftKey &&
                              !e.nativeEvent.isComposing
                            ) {
                              e.preventDefault();
                              void send();
                            }
                          }}
                          rows={1}
                        />
                        <button
                          aria-label="Send message"
                          type="submit"
                          disabled={!draft.trim() || sending}
                        >
                          <ArrowUp size={21} />
                        </button>
                      </form>
                      <p>
                        Abe is AI. Estimates aren’t a guarantee of coverage.{" "}
                        <ShieldCheck size={11} />
                      </p>
                    </div>
                  </>
                ) : tab === "overview" ? (
                  <div className="overview-scroll">
                    <div className="call-summary-label">
                      <span className="success-icon">
                        <Headphones size={19} />
                      </span>
                      <div>
                        <b>
                          {active?.status === "in_progress"
                            ? "Your call is in progress"
                            : "Your call, made clearer."}
                        </b>
                        <p>The key details to come back to.</p>
                      </div>
                      <span className="saved-label">
                        {active?.status === "in_progress" ? "LIVE" : "SAVED"}
                      </span>
                    </div>
                    <Summary recap={active?.recap} sample={!!sample} />
                    <div className="next-steps">
                      <span className="section-eyebrow">
                        LET’S KEEP THE CONVERSATION GOING
                      </span>
                      <h3>A question on your mind?</h3>
                      <p>You can pick up right where you left off.</p>
                      <button
                        type="button"
                        className="primary"
                        onClick={() => active && continueChat(active)}
                      >
                        Ask Abe about this call <ArrowUpRight size={17} />
                      </button>
                      {active?.actions.map((action) => (
                        <button
                          type="button"
                          className="action-option"
                          key={action.id}
                          onClick={() =>
                            active && continueChat(active, action.prompt)
                          }
                        >
                          <span>{action.id}</span>
                          {action.label}
                          <ArrowUpRight size={15} />
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="transcript-scroll">
                    <div className="transcript-heading">
                      <div>
                        <h3>The full conversation</h3>
                        <p>Automatic transcripts may contain errors.</p>
                      </div>
                      <span>{detail.messages.length} messages</span>
                    </div>
                    <div className="search-box transcript-search">
                      <Search size={16} />
                      <input
                        aria-label="Find in transcript"
                        placeholder="Find a word or a detail"
                        value={transcriptQuery}
                        onChange={(e) => setTranscriptQuery(e.target.value)}
                      />
                    </div>
                    {detail.messages
                      .filter(
                        (m) =>
                          !transcriptQuery ||
                          m.text
                            .toLowerCase()
                            .includes(transcriptQuery.toLowerCase()),
                      )
                      .map((m) => (
                        <article className="transcript-turn" key={m.message_id}>
                          {m.role === "assistant" ? (
                            <Avatar small />
                          ) : (
                            <span className="user-avatar">S</span>
                          )}
                          <div>
                            <header>
                              <b>{m.role === "assistant" ? "Abe" : "You"}</b>
                              {m.role === "assistant" && (
                                <span className="ai-label">AI</span>
                              )}
                              <time>{timeLabel(m.created_at)}</time>
                            </header>
                            <p>
                              <Highlight
                                text={m.text}
                                query={transcriptQuery}
                              />
                            </p>
                          </div>
                        </article>
                      ))}
                    {!detail.messages.length && (
                      <p className="no-results">
                        The transcript will appear as your call progresses.
                      </p>
                    )}
                    {transcriptQuery &&
                      !detail.messages.some((m) =>
                        m.text
                          .toLowerCase()
                          .includes(transcriptQuery.toLowerCase()),
                      ) && (
                        <p className="no-results">
                          No matching words in this transcript.
                        </p>
                      )}
                  </div>
                )}
              </>
            )}
          </section>
        </div>
        {!selected && error && (
          <div className="global-error" role="alert">
            {error}
          </div>
        )}
      </main>
      {modal && (
        <dialog
          ref={modalRef}
          className="modal-backdrop"
          aria-labelledby="modal-title"
          onCancel={() => setModal(null)}
        >
          <section className="modal">
            <button
              type="button"
              className="modal-close icon-button"
              aria-label="Close dialog"
              onClick={() => setModal(null)}
            >
              <X size={19} />
            </button>
            <Avatar />
            <h2 id="modal-title">
              {modal === "connect"
                ? "Make yourself at home."
                : "Let’s talk it through."}
            </h2>
            {modal === "connect" ? (
              <>
                <p>
                  Enter your demo access key to see saved calls and chat with
                  Abe. This workspace uses synthetic member data.
                </p>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void connect();
                  }}
                >
                  <label htmlFor="access-key">Demo access key</label>
                  <div className="key-field">
                    <LockKeyhole size={17} />
                    <input
                      id="access-key"
                      type="password"
                      autoComplete="off"
                      value={keyInput}
                      onChange={(e) => setKeyInput(e.target.value)}
                      placeholder="Your private access key"
                    />
                  </div>
                  {error && (
                    <p className="form-error" role="alert">
                      {error}
                    </p>
                  )}
                  <button
                    className="primary"
                    type="submit"
                    disabled={connecting || !keyInput.trim()}
                  >
                    {connecting ? "Connecting…" : "Connect to Abe"}
                    <ArrowUpRight size={16} />
                  </button>
                </form>
                <small>
                  Your key stays in this browser tab. Never enter your internal
                  service token.
                </small>
              </>
            ) : (
              <>
                <p>
                  Abe is your AI dental benefits guide. Calls in this demo are
                  saved as transcripts in your workspace.
                </p>
                {inbox?.profile.call_number ? (
                  <a
                    className="primary"
                    href={`tel:${inbox.profile.call_number}`}
                  >
                    <Phone size={16} />
                    {inbox.profile.call_number}
                  </a>
                ) : (
                  <>
                    <p className="call-unavailable">
                      Calling hasn’t been connected to this workspace yet.
                    </p>
                    <button
                      type="button"
                      className="primary"
                      onClick={() => {
                        setModal(null);
                        open(CHAT);
                      }}
                    >
                      Chat with Abe instead <ArrowUpRight size={16} />
                    </button>
                  </>
                )}
              </>
            )}
          </section>
        </dialog>
      )}
    </div>
  );
}
