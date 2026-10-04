# Voice protocol implementation

The current adapter targets **Amazon Nova 2 Sonic**, not the original Nova Sonic protocol examples. External SDK types and the official references below inform the implementation; Milestone 2 live phone acceptance must be performed by the user.

## Audio boundary

| Side | Wire payload | Decoded format |
| --- | --- | --- |
| Twilio inbound/outbound | Base64 `media.payload` | G.711 μ-law, mono, 8,000 samples/second |
| Nova input/output | Base64 audio event content | Signed PCM16 little-endian, mono, 8,000 samples/second |

`voice-gateway/src/audio.ts` decodes and encodes one sample at a time. Both sides use 8 kHz, so there is **no resampling**. Payloads contain raw samples, with no WAV/file header. Twilio specifies μ-law/8 kHz in its [Media Streams message contract](https://www.twilio.com/docs/voice/media-streams/websocket-messages); Nova 2 explicitly accepts 8 kHz PCM in its [input event contract](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-input-events.html).

## Twilio and call lifecycle

`POST /twilio/incoming` accepts bounded form data and validates its signature and account before returning `<Connect><Stream>`. `GET /twilio/media` upgrades to a WebSocket only after signature validation. Validation uses the configured public origin, never an incoming proxy `Host` value. WebSocket validation permits the equivalent configured HTTPS/WSS forms and their trailing-slash forms; paths remain fixed.

The telephony adapter validates JSON events and stream/call identifiers. It requires inbound mono μ-law at 8 kHz, forwards audio, recognizes stop, and accepts but ignores DTMF and mark events. Nova interruptions send Twilio `clear` once per interrupted response and suppress that response's remaining audio. Both `contentEnd: INTERRUPTED` and the assistant-only JSON text interruption marker are handled; a cancelled partial tool block does not close the call. Final transcripts continue through the existing persistence hook; they are not logged.

Each call owns its backend session, Nova connection, buffers, timers, and WebSocket. Hangup, malformed input, upstream errors, startup timeout, shutdown, or buffer overflow closes that call and attempts session deletion. Startup audio and outbound transport buffers are bounded. The gateway admits at most four concurrent calls and closes each after seven minutes; it does not roll over to another Nova session. Bedrock documents an eight-minute bidirectional-stream duration in the [API reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_InvokeModelWithBidirectionalStream.html).

Twilio references: [bidirectional `<Stream>`](https://www.twilio.com/docs/voice/twiml/stream), [Media Streams security](https://www.twilio.com/docs/voice/media-streams#communicate-with-twilios-media-servers), and [request-signature validation](https://www.twilio.com/docs/usage/security#validating-requests-are-coming-from-twilio).

## Nova stream

`src/nova/bedrock.ts` uses the installed AWS SDK's `InvokeModelWithBidirectionalStreamCommand` with an HTTP/2 handler. The default model is `amazon.nova-2-sonic-v1:0`; credentials resolve through the SDK's standard chain. The [Nova 2 getting-started guide](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-getting-started.html) documents this model identifier.

`src/nova/session.ts` sends, in order:

1. `sessionStart` with inference and turn-detection settings.
2. `promptStart` with PCM output configuration and backend-provided tool schemas.
3. A SYSTEM text content block containing the backend prompt.
4. An open USER audio content block, followed by continuous `audioInput` events.
5. An interactive USER text block requesting the opening greeting.

The greeting uses Nova 2's documented [cross-modal model-start-first support](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-cross-modal.html). Normal input continues to stream while the conversation is active. On clean close, the adapter sends audio `contentEnd`, `promptEnd`, then `sessionEnd`, with a bounded wait before aborting and destroying the client.

Tool output events are correlated by `contentId`. The adapter gathers the argument JSON, invokes only advertised tools after `contentEnd` with `TOOL_USE`, then sends a TOOL content block whose `toolResultInputConfiguration.toolUseId` matches the request. Tool results arrive as JSON text. Unknown tools, malformed arguments, timeouts, and excessive or duplicate calls produce safe errors or close the stream. See Nova's [input events](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-input-events.html) and [output events](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-output-events.html).

## Interruption regression and live acceptance

The previous implementation permanently suppressed a `completionId` after interruption. A protocol reproduction demonstrated A playing, interruption clearing playback, and B being silently discarded when B had a new `contentId` but the same `completionId`; microphone input and the connection remained open. Prior tests always assigned B a different completion ID. AWS's [Nova evaluation harness](https://github.com/aws-samples/sample-amazon-nova-sonic-eval-harness#event-driven-streaming-rxpy) also documents `completion_start` once per interactive session. A completion ID is therefore not a reliable assistant-turn cancellation key.

The gateway now associates output content blocks with bounded response state. An interruption marks only its owning response, clears Twilio playback, and leaves the input audio container and queues open. Fresh response content reopens output even with the same completion ID. Late audio and interruption events from old blocks cannot clear or play over a newer response. Nova handles interruption detection and stops generation itself; the documented input protocol has no separate response-cancel command, so the gateway does not send session-ending events to cancel speech.

Regression tests cover A → interruption → B, a normal C turn afterward, two interruptions within one completion, interruption before/at the first frame and after the final frame, stale events, and the complete protocol-adapter-to-Twilio bridge. They verify uninterrupted microphone forwarding and exactly-once hangup cleanup. No real phone-call success is implied by those tests.

For one short live acceptance call, interrupt with “Actually, I meant a root canal,” interrupt again during the next answer, then ask “What plan am I on?” without hanging up. Expect each old answer to stop and each new turn to receive a response. Safe lifecycle events `barge_in_playback_cleared`, `barge_in_input_continued`, and `barge_in_response_resumed` identify the three bridge steps using only an opaque connection ID; they contain no audio, transcripts, member IDs, or credentials. These events prove gateway activity, while a human listener must still verify timing, speech quality, and conversation continuity.

## Backend contract

Only `GET /health` is public. All session/tool requests require `Authorization: Bearer <ABE_INTERNAL_TOKEN>` over loopback HTTP or private HTTPS.

| Operation | Request | Response |
| --- | --- | --- |
| `POST /sessions` | `{call_id}` with a Twilio `CA` SID | `{session_id, system_prompt, tools}` |
| `POST /sessions/{session_id}/tools` | `{tool_name, tool_call_id, arguments}` | `{tool_name, tool_call_id, result}` |
| `DELETE /sessions/{session_id}` | No body | `204`; idempotent |

Tool definitions have `{name, description, input_schema}`. The member tool, `get_member`, accepts `{member_id}` and returns either `{status:"success", member:{...}}` or `{status:"not_found", member_id}`. IDs are case-insensitive and normalize to uppercase. Unknown or extra arguments are rejected. Stored balances are validated integers in USD; the backend returns `annual_maximum_remaining: 800` for `DEMO001` without asking Nova to derive it.

Authentication failures return 401, invalid payloads 422, unknown tools 400, missing/expired sessions 404, capacity exhaustion 503, and unexpected internal failures a sanitized 500. The gateway validates result schemas before returning them to Nova. Backend and gateway logs omit credentials and member facts.

The backend uses one worker and an in-memory session repository; bounded structured context, latest member result and latest source metadata are retained. This establishes the channel/tool boundary for future text support. Member lookup is behind a Python repository protocol so storage can change later. No identity verification or real member access is implemented. See [Milestone 2](milestone-2.md) for the additional allowlisted tools, repository contracts and deterministic calculator limitations.
