export type Source = {
  source_id: string;
  document: string;
  section?: string | null;
  page?: number | null;
};
export type Recap = {
  origin: "tool_result" | "sample";
  synthetic: true;
  provider_id?: string | null;
  provider_name?: string | null;
  procedure?: string | null;
  constraints: string[];
  plan_payment?: string | null;
  estimated_member_payment?: string | null;
  annual_maximum_remaining?: string | null;
  annual_maximum_remaining_afterward?: string | null;
  fsa_balance?: string | null;
  network_status?: string | null;
  sources?: Source[];
};
export type Conversation = {
  conversation_id: string;
  member_id: string;
  channel: "voice" | "text";
  pinned: boolean;
  title: string;
  tag: string;
  icon: string;
  preview: string;
  updated_at: string;
  status: "in_progress" | "completed" | "partial";
  recap?: Recap | null;
  actions: { id: string; label: string; prompt: string }[];
  synthetic: true;
};
export type Message = {
  message_id: string;
  conversation_id: string;
  role: "user" | "assistant";
  text: string;
  source: "voice" | "text" | "seed";
  created_at: string;
};
export type Detail = { conversation: Conversation; messages: Message[] };
export type Inbox = {
  profile: {
    member_id: string;
    display_name: string;
    call_number: string;
    synthetic: true;
  };
  conversations: Conversation[];
};
export type Demo = { inbox: Inbox; details: Record<string, Detail> };
