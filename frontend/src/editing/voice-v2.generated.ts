// Generated from app.editing.contracts; do not edit by hand.
export type Begin = { "type": "utterance.begin"; "utteranceId": string; "binding": Binding; };
export type Binding = { "expectedRevision": number; "contextRevision": number; "interactionEpoch": number; "catalogRevision": string; "scopeDigest": string; };
export type End = { "type": "utterance.end"; "manifest": Array<string>; "reason": "silence" | "local_stop"; "silenceMs": number; };
export type SegmentBegin = { "type": "segment.begin"; "segmentId": string; "sequence": number; };
export type SegmentEnd = { "type": "segment.end"; "segmentId": string; };
export type Simple = { "type": "speech.stopped" | "utterance.cancel" | "capture.stop"; };
export type VoiceV2Control = Begin | SegmentBegin | SegmentEnd | End | Simple;
