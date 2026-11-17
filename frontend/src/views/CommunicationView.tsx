import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import "./CommunicationView.css";
import { Icon } from "../v3/ui/Icon";
import { confirmAction } from "../v3/ui/confirm";
import { GoogleMeetDialog } from "../components/GoogleMeetDialog";

import {
  createCommunicationConversation,
  createCommunicationMessage,
  createCommunicationProject,
  createCommunicationReminder,
  createCommunicationTask,
  updateCommunicationTask,
  updateCommunicationMessage,
  deleteCommunicationMessage,
  summarizeCommunicationThread,
  setCommunicationReaction,
  listSavedCommunicationMessages,
  setSavedCommunicationMessage,
  getCommunicationPreferences,
  setCommunicationSound,
  setCommunicationMute,
  forwardCommunicationMessage,
  updateCommunicationChecklist,
  recapCommunicationConversation,
  apiUrl,
  getCommunicationBootstrap,
  getCommunicationDraft,
  saveCommunicationDraft,
  getCommunicationLinkPreview,
  listCommunicationMessages,
  listCommunicationNotifications,
  markCommunicationRead,
  refreshCommunicationPresence,
  listLeads,
  pinCommunicationMessage,
  readCommunicationNotification,
  searchCommunicationMessages,
  unpinCommunicationMessage,
  uploadCommunicationAttachment,
} from "../api";
import type {
  CommunicationBootstrap,
  CommunicationConversation,
  CommunicationMessage,
  CommunicationNotification,
  CommunicationLinkPreview,
  CommunicationAttachment,
  CommunicationProject,
  Lead,
} from "../types";
import { activateCommunicationSound, communicationSoundActive } from "../communicationSound";

type CommunicationViewProps = { permissions: ReadonlySet<string>; onOpenLead: (id: string) => void };
type PendingAttachment = { file: File; kind: "file" | "voice"; duration_ms?: number; upload_id: string };
type Draft = { text: string; mention_ids: string[]; lead_ids: string[]; attachment_names: string[] };

const can = (permissions: ReadonlySet<string>, permission: string) => permissions.has(permission);

function conversationLabel(conversation: CommunicationConversation, members: CommunicationBootstrap["members"]) {
  if (conversation.name) return conversation.name;
  return conversation.member_ids
    .map((id) => members.find((member) => member.id === id)?.display_name)
    .filter(Boolean)
    .join(", ") || "Direct message";
}

function time(value: string) {
  return new Date(value).toLocaleString([], { hour: "numeric", minute: "2-digit", month: "short", day: "numeric" });
}

export function CommunicationView({ permissions, onOpenLead }: CommunicationViewProps) {
  const [data, setData] = useState<CommunicationBootstrap | null>(null);
  const [selected, setSelected] = useState<CommunicationConversation | null>(null);
  const [messages, setMessages] = useState<CommunicationMessage[]>([]);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [leadSearch, setLeadSearch] = useState("");
  const [leadSearchResults, setLeadSearchResults] = useState<Lead[]>([]);
  const [leadSearchError, setLeadSearchError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [mentionIds, setMentionIds] = useState<string[]>([]);
  const [mentionQuery, setMentionQuery] = useState<{ start: number; end: number; query: string } | null>(null);
  const [leadIds, setLeadIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const [loadedDraftId, setLoadedDraftId] = useState<string | null>(null);
  const [missingDraftAttachments, setMissingDraftAttachments] = useState<string[]>([]);
  const [showCreate, setShowCreate] = useState<"project" | "channel" | "dm" | "group" | null>(null);
  const [newMemberIds, setNewMemberIds] = useState<string[]>([]);
  const [openingMemberId, setOpeningMemberId] = useState<string | null>(null);
  const [onlineMemberIds, setOnlineMemberIds] = useState<string[] | null>(null);
  const [quickActionsOpen, setQuickActionsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [mobileConversationsOpen, setMobileConversationsOpen] = useState(false);
  const [searchResults, setSearchResults] = useState<CommunicationMessage[]>([]);
  const [notifications, setNotifications] = useState<CommunicationNotification[]>([]);
  const [showNotifications, setShowNotifications] = useState(false);
  const [notificationTarget, setNotificationTarget] = useState<string | null>(null);
  const [threadRoot, setThreadRoot] = useState<CommunicationMessage | null>(null);
  const [taskMessage, setTaskMessage] = useState<CommunicationMessage | null>(null);
  const [forwardMessage, setForwardMessage] = useState<CommunicationMessage | null>(null);
  const [forwardBusy, setForwardBusy] = useState(false);
  const [forwardAttempted, setForwardAttempted] = useState(false);
  const [forwardDestination, setForwardDestination] = useState("");
  const forwardRequestRef = useRef("");
  const forwardBusyRef = useRef(false);
  const [editingMessage, setEditingMessage] = useState<CommunicationMessage | null>(null);
  const [editingText, setEditingText] = useState("");
  const [editBusy, setEditBusy] = useState(false);
  const [taskSaving, setTaskSaving] = useState(false);
  const [threadReplies, setThreadReplies] = useState<CommunicationMessage[]>([]);
  const [threadText, setThreadText] = useState("");
  const [threadSending, setThreadSending] = useState(false);
  const threadSendingRef = useRef(false);
  const [threadSummary, setThreadSummary] = useState<{ summary: string; message_count: number; truncated: boolean } | null>(null);
  const [summaryBusy, setSummaryBusy] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [recap, setRecap] = useState<{ summary: string; message_count: number; truncated: boolean } | null>(null);
  const [recapError, setRecapError] = useState<string | null>(null);
  const [recapBusy, setRecapBusy] = useState(false);
  const [showRecap, setShowRecap] = useState(false);
  const [showSaved, setShowSaved] = useState(false);
  const [showMeeting, setShowMeeting] = useState(false);
  const [savedMessages, setSavedMessages] = useState<CommunicationMessage[]>([]);
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  const [failedUploads, setFailedUploads] = useState<Record<string, { messageId: string; items: PendingAttachment[] }>>({});
  const [recording, setRecording] = useState(false);
  const [recordingPaused, setRecordingPaused] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const recordingDurationRef = useRef(0);
  const [browserAlertsEnabled, setBrowserAlertsEnabled] = useState(
    typeof Notification !== "undefined" && Notification.permission === "granted",
  );
  const [notificationSound, setNotificationSound] = useState(false);
  const [soundReady, setSoundReady] = useState(communicationSoundActive());
  const [mutedConversations, setMutedConversations] = useState<string[]>([]);
  const knownNotificationIds = useRef(new Set<string>());
  const notificationsReady = useRef(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recordingStartedAt = useRef(0);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const searchRequestRef = useRef(0);
  const countRequestRef = useRef(0);
  const draftRef = useRef<Draft>({ text: "", mention_ids: [], lead_ids: [], attachment_names: [] });
  const draftWrites = useRef(new Map<string, Promise<void>>());
  const draftAttachments = useRef(new Map<string, PendingAttachment[]>());
  const dialogBusyRef = useRef(false);

  useEffect(() => { setDialogError(null); }, [showCreate, taskMessage, editingMessage, threadRoot, forwardMessage]);
  useEffect(() => { dialogBusyRef.current = taskSaving || editBusy || forwardBusy; }, [taskSaving, editBusy, forwardBusy]);

  useEffect(() => {
    if (!showCreate && !taskMessage && !editingMessage && !threadRoot && !forwardMessage) return;
    const dialog = document.querySelector<HTMLElement>(".communication-view [role='dialog']");
    if (!dialog) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const controls = () => Array.from(dialog.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex]:not([tabindex='-1'])"));
    controls()[0]?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (!dialog!.contains(document.activeElement)) return;
      if (event.key === "Escape" && !dialogBusyRef.current) {
        event.preventDefault();
        setShowCreate(null); setNewMemberIds([]); setTaskMessage(null); setEditingMessage(null); setThreadRoot(null); setForwardMessage(null);
      } else if (event.key === "Tab") {
        const items = controls();
        if (!items.length) { event.preventDefault(); return; }
        if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items[items.length - 1].focus(); }
        else if (!event.shiftKey && document.activeElement === items[items.length - 1]) { event.preventDefault(); items[0].focus(); }
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => { document.removeEventListener("keydown", onKeyDown); previousFocus?.focus(); };
  }, [showCreate, taskMessage, editingMessage, threadRoot, forwardMessage]);
  const activeThreadRef = useRef<string | null>(null);
  const activeConversationRef = useRef<string | null>(null);

  useEffect(() => {
    activeConversationRef.current = selected?.id ?? null;
    setRecap(null); setRecapError(null); setShowRecap(false);
  }, [selected?.id]);

  async function generateRecap() {
    if (!selected || recapBusy) return;
    const id = selected.id;
    setRecapBusy(true); setRecapError(null);
    try {
      const result = await recapCommunicationConversation(id);
      if (activeConversationRef.current === id) setRecap(result);
    } catch (reason) {
      if (activeConversationRef.current === id) setRecapError(reason instanceof Error ? reason.message : "Could not recap this conversation.");
    } finally { setRecapBusy(false); }
  }

  const refresh = async () => {
    setLoading(true);
    try {
      const [next, leadRows, nextNotifications, preferences] = await Promise.all([getCommunicationBootstrap(), listLeads({ limit: 50 }), listCommunicationNotifications(), getCommunicationPreferences()]);
      setData(next);
      setLeads(leadRows.items);
      knownNotificationIds.current = new Set(nextNotifications.map((item) => item.id));
      notificationsReady.current = true;
      setNotifications(nextNotifications);
      setNotificationSound(preferences.sound_enabled);
      setMutedConversations(preferences.muted_conversation_ids);
      setSelected((current) => current && next.conversations.find((item) => item.id === current.id) || next.conversations[0] || null);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Communication is unavailable.");
    } finally { setLoading(false); }
  };

  useEffect(() => { void refresh(); }, []);

  if (selected && loadedDraftId === selected.id) {
    draftRef.current = { text, mention_ids: mentionIds, lead_ids: leadIds, attachment_names: [...missingDraftAttachments, ...pendingAttachments.map((item) => item.file.name)] };
    draftAttachments.current.set(selected.id, pendingAttachments);
  }

  function queueDraft(id: string, draft: Draft) {
    const previous = draftWrites.current.get(id) || Promise.resolve();
    const next = previous.catch(() => undefined).then(async () => {
      await saveCommunicationDraft(id, draft);
    });
    draftWrites.current.set(id, next);
    void next.catch(() => setError("Draft could not be saved. Check your connection."));
    return next;
  }

  useEffect(() => {
    if (!selected) return;
    const id = selected.id;
    let active = true;
    let loaded = false;
    setLoadedDraftId(null);
    setText(""); setMentionIds([]); setLeadIds([]); setMentionQuery(null);
    setMissingDraftAttachments([]);
    setPendingAttachments(draftAttachments.current.get(id) || []);
    void (async () => {
      try {
        await draftWrites.current.get(id)?.catch(() => undefined);
        const draft = await getCommunicationDraft(id);
        if (!active) return;
        draftRef.current = draft;
        if (!(draftAttachments.current.get(id) || []).length) setMissingDraftAttachments(draft.attachment_names || []);
        setText(draft.text); setMentionIds(draft.mention_ids); setLeadIds(draft.lead_ids);
        loaded = true; setLoadedDraftId(id);
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "Could not load draft.");
      }
    })();
    return () => { active = false; if (loaded) void queueDraft(id, draftRef.current); };
  }, [selected?.id]);

  useEffect(() => {
    if (!selected || loadedDraftId !== selected.id) return;
    const id = selected.id;
    const timer = window.setTimeout(() => void queueDraft(id, { text, mention_ids: mentionIds, lead_ids: leadIds, attachment_names: [...missingDraftAttachments, ...pendingAttachments.map((item) => item.file.name)] }), 600);
    return () => window.clearTimeout(timer);
  }, [selected?.id, loadedDraftId, text, mentionIds, leadIds, pendingAttachments, missingDraftAttachments]);

  useEffect(() => () => {
    const recorder = recorderRef.current;
    if (!recorder) return;
    recorder.onstop = null;
    if (recorder.state !== "inactive") recorder.stop();
    recorder.stream.getTracks().forEach((track) => track.stop());
    recorderRef.current = null;
  }, []);

  useEffect(() => {
    if (!recording || recordingPaused) return;
    const interval = window.setInterval(() => {
      const elapsed = recordingDurationRef.current + Date.now() - recordingStartedAt.current;
      setRecordingSeconds(Math.floor(elapsed / 1000));
      if (elapsed >= 10 * 60 * 1000) stopVoiceRecording();
    }, 250);
    return () => window.clearInterval(interval);
  }, [recording, recordingPaused]);

  useEffect(() => {
    const query = leadSearch.trim();
    if (!query) { setLeadSearchResults([]); setLeadSearchError(null); return; }
    setLeadSearchResults([]);
    setLeadSearchError(null);
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void listLeads({ search: query, limit: 50 }, controller.signal)
        .then((result) => { setLeadSearchResults(result.items); setLeadSearchError(null); })
        .catch(() => { if (!controller.signal.aborted) setLeadSearchError("Could not search leads."); });
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [leadSearch]);

  function refreshConversationCounts() {
    const requestId = ++countRequestRef.current;
    void getCommunicationBootstrap().then((next) => {
      if (requestId !== countRequestRef.current) return;
      setData((current) => current ? { ...current, conversations: next.conversations } : current);
    }).catch(() => undefined);
  }

  useEffect(() => {
    const update = () => { if (!document.hidden) refreshConversationCounts(); };
    const interval = window.setInterval(update, 30_000);
    document.addEventListener("visibilitychange", update);
    return () => { countRequestRef.current++; window.clearInterval(interval); document.removeEventListener("visibilitychange", update); };
  }, []);

  useEffect(() => {
    const update = () => {
      if (!document.hidden) void refreshCommunicationPresence().then(setOnlineMemberIds).catch(() => setOnlineMemberIds(null));
    };
    update();
    const interval = window.setInterval(update, 30_000);
    document.addEventListener("visibilitychange", update);
    return () => { window.clearInterval(interval); document.removeEventListener("visibilitychange", update); };
  }, []);

  useEffect(() => {
    const refreshNotifications = () => void listCommunicationNotifications().then((next) => {
      const newUnread = notificationsReady.current ? next.filter((item) => !item.read_at && !knownNotificationIds.current.has(item.id)) : [];
      knownNotificationIds.current = new Set(next.map((item) => item.id));
      notificationsReady.current = true;
      if (browserAlertsEnabled && document.hidden) {
        newUnread.forEach((notification) => new Notification("Workspace communication", { body: `New ${notification.kind.replace("_", " ")} notification` }));
      }
      setNotifications(next);
    }).catch(() => undefined);
    const interval = window.setInterval(refreshNotifications, 30_000);
    return () => window.clearInterval(interval);
  }, [browserAlertsEnabled]);

  useEffect(() => {
    if (!selected) { setMessages([]); return; }
    let active = true;
    const loadMessages = () => void listCommunicationMessages(selected.id)
      .then((next) => {
        if (!active) return;
        setMessages(next);
      })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Messages could not be loaded."); });
    loadMessages();
    const interval = window.setInterval(loadMessages, 20_000);
    return () => { active = false; window.clearInterval(interval); };
  }, [selected?.id]);

  useEffect(() => {
    const last = messages.at(-1);
    if (!selected || !last || document.hidden) return;
    const element = document.getElementById(`communication-message-${last.id}`);
    if (!element) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !document.hidden) {
        void markCommunicationRead(selected.id, last.id).then(refreshConversationCounts).catch(() => undefined);
        observer.disconnect();
      }
    }, { threshold: 0.5 });
    observer.observe(element);
    return () => observer.disconnect();
  }, [selected?.id, messages]);

  useEffect(() => {
    if (!notificationTarget) return;
    const target = document.getElementById(`communication-message-${notificationTarget}`);
    if (target) { target.scrollIntoView({ block: "center" }); setNotificationTarget(null); }
  }, [messages, notificationTarget]);

  useEffect(() => {
    activeThreadRef.current = threadRoot?.id ?? null;
    setThreadSummary(null);
    setSummaryError(null);
    if (!threadRoot) { setThreadReplies([]); return; }
    void listCommunicationMessages(threadRoot.conversation_id, threadRoot.id)
      .then(setThreadReplies)
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Thread replies could not be loaded."));
  }, [threadRoot?.id]);

  async function summarizeThread() {
    if (!threadRoot || summaryBusy) return;
    const messageId = threadRoot.id;
    setSummaryBusy(true);
    setSummaryError(null);
    try {
      const summary = await summarizeCommunicationThread(messageId);
      if (activeThreadRef.current === messageId) setThreadSummary(summary);
    } catch (reason) { if (activeThreadRef.current === messageId) setSummaryError(reason instanceof Error ? reason.message : "Could not summarize this thread."); }
    finally { setSummaryBusy(false); }
  }

  const grouped = useMemo(() => (data?.projects ?? []).map((project) => ({ project, conversations: data?.conversations.filter((item) => item.project_id === project.id) ?? [] })), [data]);
  const ungrouped = useMemo(() => data?.conversations.filter((item) => !item.project_id) ?? [], [data]);
  const selectedMentions = data?.members.filter((member) => mentionIds.includes(member.id)) ?? [];
  const mentionSuggestions = mentionQuery && data && selected
    ? data.members.filter((member) => selected.member_ids.includes(member.id) && member.display_name.toLowerCase().includes(mentionQuery.query.toLowerCase()))
    : [];

  function updateComposer(value: string, cursor: number) {
    setText(value);
    const match = value.slice(0, cursor).match(/(?:^|\s)@([^\s@]*)$/);
    setMentionQuery(match ? { start: cursor - match[1].length - 1, end: cursor, query: match[1] } : null);
  }

  function insertMention(member: CommunicationBootstrap["members"][number]) {
    if (!mentionQuery) return;
    const insertion = `@${member.display_name} `;
    const cursor = mentionQuery.start + insertion.length;
    setText((current) => current.slice(0, mentionQuery.start) + insertion + current.slice(mentionQuery.end));
    setMentionIds((current) => current.includes(member.id) ? current : [...current, member.id]);
    setMentionQuery(null);
    requestAnimationFrame(() => { composerRef.current?.focus(); composerRef.current?.setSelectionRange(cursor, cursor); });
  }
  function restoreAttachmentNames(files: File[]) {
    setMissingDraftAttachments((current) => {
      const remaining = [...current];
      for (const file of files) {
        const index = remaining.indexOf(file.name);
        if (index >= 0) remaining.splice(index, 1);
      }
      return remaining;
    });
  }
  const selectedLeads = leads.filter((lead) => leadIds.includes(lead.id));

  async function openDirectMessage(memberId: string) {
    if (!data || openingMemberId) return;
    const existing = data.conversations.find((item) => item.kind === "dm" && !item.is_archived && item.member_ids.includes(memberId) && item.member_ids.includes(data.active_user_id));
    if (existing) {
      setSelected(existing);
      setMobileConversationsOpen(false);
      return;
    }
    setOpeningMemberId(memberId);
    try {
      const conversation = await createCommunicationConversation({ kind: "dm", member_ids: [memberId] });
      setData((current) => current ? { ...current, conversations: [conversation, ...current.conversations] } : current);
      setSelected(conversation);
      setMobileConversationsOpen(false);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not open the direct message.");
    } finally { setOpeningMemberId(null); }
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    if (sendingRef.current || !selected || failedUploads[selected.id] || missingDraftAttachments.length || loadedDraftId !== selected.id || recording || (!text.trim() && !pendingAttachments.length)) return;
    const conversationId = selected.id;
    const submittedAttachments = pendingAttachments;
    sendingRef.current = true; setSending(true);
    try {
      const messageText = text.trim() || (pendingAttachments.some((item) => item.kind === "voice") ? "Voice message" : "Attachment");
      const created = await createCommunicationMessage(conversationId, {
        plaintext: messageText,
        body: { version: 1, type: "markdown", text: messageText },
        mentioned_user_ids: mentionIds,
        lead_ids: leadIds,
      });
      const attachments: CommunicationAttachment[] = [];
      try {
        for (const attachment of submittedAttachments) {
          attachments.push(await uploadCommunicationAttachment(created.id, attachment));
        }
      } catch (reason) {
        const remaining = submittedAttachments.slice(attachments.length);
        draftAttachments.current.set(conversationId, []);
        setFailedUploads((current) => ({ ...current, [conversationId]: { messageId: created.id, items: remaining } }));
        void queueDraft(conversationId, { text: "", mention_ids: [], lead_ids: [], attachment_names: [] });
        if (activeConversationRef.current === conversationId) {
          draftRef.current = { text: "", mention_ids: [], lead_ids: [], attachment_names: [] };
          setMessages((current) => [...current, { ...created, attachments }]);
          setText(""); setMentionIds([]); setMentionQuery(null); setLeadIds([]);
          setPendingAttachments([]);
        }
        setError(reason instanceof Error ? `${reason.message} The message was sent. Retry the remaining attachments below.` : "The message was sent. Retry the remaining attachments below.");
        return;
      }
      draftAttachments.current.set(conversationId, []);
      void queueDraft(conversationId, { text: "", mention_ids: [], lead_ids: [], attachment_names: [] });
      if (activeConversationRef.current === conversationId) {
        draftRef.current = { text: "", mention_ids: [], lead_ids: [], attachment_names: [] };
        setMessages((current) => [...current, { ...created, attachments }]);
        setText(""); setMentionIds([]); setMentionQuery(null); setLeadIds([]); setPendingAttachments([]);
      }
      setError(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Message could not be sent."); }
    finally { sendingRef.current = false; setSending(false); }
  }

  async function retryAttachments(conversationId: string) {
    const failed = failedUploads[conversationId];
    if (!failed || sendingRef.current) return;
    sendingRef.current = true; setSending(true);
    let completed = 0;
    try {
      for (const item of failed.items) {
        const uploaded = await uploadCommunicationAttachment(failed.messageId, item);
        completed += 1;
        if (activeConversationRef.current === conversationId) {
          setMessages((current) => current.map((message) => message.id === failed.messageId
            ? { ...message, attachments: [...message.attachments, uploaded] } : message));
        }
      }
      setFailedUploads((current) => { const next = { ...current }; delete next[conversationId]; return next; });
      setError(null);
    } catch (reason) {
      setFailedUploads((current) => ({ ...current, [conversationId]: { ...failed, items: failed.items.slice(completed) } }));
      setError(reason instanceof Error ? `${reason.message} Retry the remaining attachments.` : "Could not upload the remaining attachments.");
    } finally { sendingRef.current = false; setSending(false); }
  }

  async function startVoiceRecording() {
    if (!selected) return;
    const recordingConversationId = selected.id;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("Voice recording is not supported by this browser.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: BlobPart[] = [];
      const recorder = new MediaRecorder(stream);
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        const extension = blob.type.includes("ogg") ? "ogg" : blob.type.includes("mpeg") ? "mp3" : blob.type.includes("mp4") ? "m4a" : "webm";
        const file = new File([blob], `voice-message.${extension}`, { type: blob.type || "audio/webm" });
        if (activeConversationRef.current === recordingConversationId) restoreAttachmentNames([file]);
        const attachment: PendingAttachment = { file, kind: "voice", upload_id: crypto.randomUUID(), duration_ms: Math.min(600_000, Math.max(1, recordingDurationRef.current)) };
        const next = [...(draftAttachments.current.get(recordingConversationId) || []), attachment];
        draftAttachments.current.set(recordingConversationId, next);
        if (activeConversationRef.current === recordingConversationId) setPendingAttachments(next);
        setRecording(false);
        recorderRef.current = null;
      };
      recordingStartedAt.current = Date.now();
      recordingDurationRef.current = 0;
      setRecordingSeconds(0);
      setRecordingPaused(false);
      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
      setError(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Microphone access is required to record a voice message."); }
  }

  function pauseVoiceRecording() {
    const recorder = recorderRef.current;
    if (!recorder) return;
    if (recorder.state === "recording") {
      recordingDurationRef.current += Date.now() - recordingStartedAt.current;
      recorder.pause(); setRecordingPaused(true);
    } else if (recorder.state === "paused") {
      recordingStartedAt.current = Date.now();
      recorder.resume(); setRecordingPaused(false);
    }
  }

  function stopVoiceRecording() {
    const recorder = recorderRef.current;
    if (!recorder || recorder.state === "inactive") return;
    if (recorder.state === "recording") recordingDurationRef.current += Date.now() - recordingStartedAt.current;
    recorder.stop();
  }

  async function enableBrowserAlerts() {
    if (typeof Notification === "undefined") { setError("Browser alerts are not supported by this browser."); return; }
    const permission = await Notification.requestPermission();
    setBrowserAlertsEnabled(permission === "granted");
    if (permission !== "granted") setError("Browser alerts were not enabled.");
  }

  async function toggleNotificationSound() {
    try {
      if (notificationSound && soundReady) {
        const preferences = await setCommunicationSound(false);
        setNotificationSound(preferences.sound_enabled);
      } else {
        await activateCommunicationSound();
        setSoundReady(true);
        const preferences = await setCommunicationSound(true);
        setNotificationSound(preferences.sound_enabled);
      }
      window.dispatchEvent(new Event("communication-preferences-changed"));
      setError(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not enable notification sound."); }
  }

  async function toggleMute() {
    if (!selected) return;
    try {
      const result = await setCommunicationMute(selected.id, !mutedConversations.includes(selected.id));
      setMutedConversations(result.muted_conversation_ids);
      window.dispatchEvent(new Event("communication-preferences-changed"));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not update notification settings."); }
  }

  function formatSelection(prefix: string, suffix = prefix) {
    const field = composerRef.current;
    if (!field) return;
    const selectionStart = field.selectionStart;
    const selectionEnd = field.selectionEnd;
    const selectedText = text.slice(selectionStart, selectionEnd) || "text";
    const next = `${text.slice(0, selectionStart)}${prefix}${selectedText}${suffix}${text.slice(selectionEnd)}`;
    setText(next);
    requestAnimationFrame(() => {
      field.focus();
      const start = selectionStart + prefix.length;
      field.setSelectionRange(start, start + selectedText.length);
    });
  }

  function formatLines(prefix: string) {
    const field = composerRef.current;
    if (!field) return;
    const start = text.lastIndexOf("\n", field.selectionStart - 1) + 1;
    const endIndex = text.indexOf("\n", field.selectionEnd);
    const end = endIndex === -1 ? text.length : endIndex;
    const lines = text.slice(start, end).split("\n");
    const replacement = lines.map((line, index) => `${prefix === "number" ? `${index + 1}. ` : prefix}${line}`).join("\n");
    setText(text.slice(0, start) + replacement + text.slice(end));
    requestAnimationFrame(() => { field.focus(); field.setSelectionRange(start, start + replacement.length); });
  }

  function formatLink() {
    const url = window.prompt("Link URL (https://…)");
    if (!url?.trim()) return;
    formatSelection("[", `](${url.trim()})`);
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    setDialogError(null);
    const form = new FormData(event.currentTarget as HTMLFormElement);
    const name = String(form.get("name") || "").trim();
    const description = String(form.get("description") || "").trim();
    const memberId = String(form.get("member_id") || "");
    const projectId = String(form.get("project_id") || "") || undefined;
    if (showCreate === "dm") {
      setShowCreate(null);
      await openDirectMessage(memberId);
      return;
    }
    try {
      if (showCreate === "project") await createCommunicationProject({ name, description });
      else {
        await createCommunicationConversation(showCreate === "group"
            ? { kind: "group_dm", member_ids: newMemberIds }
            : { kind: "channel", name, description, project_id: projectId, member_ids: newMemberIds });
      }
      setShowCreate(null); setNewMemberIds([]); await refresh();
    } catch (reason) { setDialogError(reason instanceof Error ? reason.message : "Could not create the conversation."); }
  }

  async function pin(messageId: string) {
    try {
      const message = messages.find((item) => item.id === messageId);
      if (message?.is_pinned) {
        await unpinCommunicationMessage(messageId);
        setMessages((current) => current.map((item) => item.id === messageId ? { ...item, is_pinned: false } : item));
      } else {
        const pinned = await pinCommunicationMessage(messageId);
        setMessages((current) => current.map((item) => item.id === pinned.id ? pinned : item));
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not pin the message."); }
  }

  async function react(message: CommunicationMessage, reaction: string) {
    try {
      const selected = !message.reactions?.some((item) => item.reaction === reaction && item.mine);
      const updated = await setCommunicationReaction(message.id, reaction, selected);
      setMessages((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not update reaction."); }
  }

  async function toggleSaved(message: CommunicationMessage) {
    try {
      const updated = await setSavedCommunicationMessage(message.id, !message.is_saved);
      setMessages((current) => current.map((item) => item.id === updated.id ? updated : item));
      if (showSaved) setSavedMessages(await listSavedCommunicationMessages());
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not update saved messages."); }
  }

  async function forward(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!forwardMessage || forwardBusyRef.current) return;
    const targetId = forwardDestination;
    if (!targetId) return;
    forwardBusyRef.current = true; setForwardBusy(true); setForwardAttempted(true); setDialogError(null);
    try {
      const result = await forwardCommunicationMessage(forwardMessage.id, targetId, forwardRequestRef.current);
      if (activeConversationRef.current === targetId) setMessages((current) => current.some((item) => item.id === result.id) ? current : [...current, result]);
      setForwardMessage(null); setNotice("Message forwarded.");
    } catch (reason) { setDialogError(reason instanceof Error ? reason.message : "Could not forward message."); }
    finally { forwardBusyRef.current = false; setForwardBusy(false); }
  }

  async function openSaved() {
    if (showSaved) { setShowSaved(false); return; }
    try { setSavedMessages(await listSavedCommunicationMessages()); setShowSaved(true); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load saved messages."); }
  }

  async function updateChecklist(message: CommunicationMessage, line: number, completed: boolean) {
    try {
      const updated = await updateCommunicationChecklist(message.id, line, completed, message.plaintext);
      setMessages((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not update checklist."); }
  }

  async function remind(messageId: string) {
    const dueAt = new Date(Date.now() + 60 * 60 * 1000).toISOString();
    try { await createCommunicationReminder(messageId, dueAt); setNotice("Reminder set for one hour from now."); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not set the reminder."); }
  }

  async function createTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!taskMessage || taskSaving) return;
    setDialogError(null);
    const form = new FormData(event.currentTarget);
    const due = String(form.get("due_at") || "");
    setTaskSaving(true);
    try {
      const task = await createCommunicationTask(taskMessage.id, {
        title: String(form.get("title") || "").trim(), assignee_id: String(form.get("assignee_id") || ""),
        due_at: due ? new Date(due).toISOString() : null,
      });
      setMessages((current) => current.map((message) => message.id === taskMessage.id ? { ...message, tasks: [...message.tasks, task] } : message));
      setTaskMessage(null);
      setError(null);
    } catch (reason) { setDialogError(reason instanceof Error ? reason.message : "Could not create the task."); }
    finally { setTaskSaving(false); }
  }

  async function toggleTask(messageId: string, taskId: string, completed: boolean) {
    try {
      const task = await updateCommunicationTask(taskId, completed);
      setMessages((current) => current.map((message) => message.id === messageId ? { ...message, tasks: message.tasks.map((item) => item.id === task.id ? task : item) } : message));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not update the task."); }
  }

  async function saveMessageEdit(event: FormEvent) {
    event.preventDefault();
    if (!editingMessage || !editingText.trim() || editBusy) return;
    setDialogError(null);
    setEditBusy(true);
    try {
      const text = editingText.trim();
      const updated = await updateCommunicationMessage(editingMessage.id, {
        plaintext: text, body: { version: 1, type: "markdown", text },
      });
      setMessages((current) => current.map((message) => message.id === updated.id ? updated : message));
      setEditingMessage(null);
      setError(null);
    } catch (reason) { setDialogError(reason instanceof Error ? reason.message : "Could not edit the message."); }
    finally { setEditBusy(false); }
  }

  async function deleteMessage(message: CommunicationMessage) {
    if (!(await confirmAction({ title: "Delete this message?", message: "The message and its attachments and tasks will be removed.", confirmLabel: "Delete message", tone: "danger" }))) return;
    try {
      await deleteCommunicationMessage(message.id);
      setMessages((current) => current.map((item) => item.id === message.id ? { ...item, plaintext: "This message was deleted.", body: { version: 1, type: "deleted" }, deleted_at: new Date().toISOString(), attachments: [], tasks: [], mentioned_user_ids: [], leads: [] } : item));
      setError(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not delete the message."); }
  }

  async function readNotifications() {
    const unread = notifications.filter((item) => !item.read_at);
    try {
      await Promise.all(unread.map((item) => readCommunicationNotification(item.id)));
      setNotifications((current) => current.map((item) => item.read_at ? item : { ...item, read_at: new Date().toISOString() }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Notifications could not be updated."); }
  }

  async function openNotification(item: CommunicationNotification) {
    const conversation = data?.conversations.find((candidate) => candidate.id === item.conversation_id);
    if (!conversation) { setError("This conversation is no longer available."); return; }
    setSelected(conversation);
    setNotificationTarget(item.message_id);
    setShowNotifications(false);
    setMobileConversationsOpen(false);
    if (!item.read_at) {
      try {
        await readCommunicationNotification(item.id);
        setNotifications((current) => current.map((notice) => notice.id === item.id ? { ...notice, read_at: new Date().toISOString() } : notice));
      } catch { setError("The alert could not be marked as read."); }
    }
  }

  async function runSearch(value: string) {
    setSearch(value);
    const requestId = ++searchRequestRef.current;
    if (value.trim().length < 2) { setSearchResults([]); return; }
    try {
      const results = await searchCommunicationMessages(value);
      if (requestId === searchRequestRef.current) setSearchResults(results);
    } catch (reason) {
      if (requestId === searchRequestRef.current) setError(reason instanceof Error ? reason.message : "Search is unavailable.");
    }
  }

  async function replyInThread(event: FormEvent) {
    event.preventDefault();
    if (threadSendingRef.current || !threadRoot || !threadText.trim()) return;
    const rootId = threadRoot.id;
    threadSendingRef.current = true; setThreadSending(true);
    try {
      const created = await createCommunicationMessage(threadRoot.conversation_id, {

        plaintext: threadText,
        body: { version: 1, type: "plain_text", text: threadText },
        parent_message_id: threadRoot.id,
      });
      if (activeThreadRef.current === rootId) {
        setThreadReplies((current) => [...current, created]);
        setThreadText("");
      }
    } catch (reason) { setDialogError(reason instanceof Error ? reason.message : "Thread reply could not be sent."); }
    finally { threadSendingRef.current = false; setThreadSending(false); }
  }

  if (loading) return <div className="communication-loading" aria-busy="true">Loading your workspace conversations…</div>;
  if (!data) return <div className="communication-loading">{error || "Communication is unavailable."}</div>;

  return <section className="communication-view page-view" aria-label="Workspace communication">
    <header className="communication-header">
      <div><span className="eyebrow">Workspace collaboration</span><h1>Communication</h1><p>Share lead context, coordinate work, and keep decisions with the team.</p></div>
      <div className="communication-header-actions">
        <button type="button" className="button-quiet" aria-expanded={showSaved} onClick={() => void openSaved()}>Saved messages</button><button className="button-quiet communication-notifications" onClick={() => setShowNotifications((open) => !open)} aria-expanded={showNotifications} aria-controls="communication-alerts">Alerts{notifications.filter((item) => !item.read_at).length ? ` · ${notifications.filter((item) => !item.read_at).length}` : ""}</button>
        {!browserAlertsEnabled && <button className="button-quiet" onClick={() => void enableBrowserAlerts()}>Enable alerts</button>}<button type="button" className="button-quiet" aria-pressed={notificationSound} onClick={() => void toggleNotificationSound()}>{notificationSound && soundReady ? "Sound on" : notificationSound ? "Activate sound" : "Enable sound"}</button>
        {(can(permissions, "communication.create") || can(permissions, "communication.manage")) && <div className="communication-quick-actions"><button type="button" className="primary-button" aria-expanded={quickActionsOpen} onClick={() => setQuickActionsOpen((open) => !open)}>Quick actions</button>{quickActionsOpen && <div className="communication-quick-menu" role="group" aria-label="Quick actions">{can(permissions, "communication.create") && <><button type="button" onClick={() => { setShowCreate("dm"); setQuickActionsOpen(false); }}>New message</button><button type="button" onClick={() => { setShowCreate("group"); setQuickActionsOpen(false); }}>New group</button><button type="button" onClick={() => { setShowCreate("channel"); setQuickActionsOpen(false); }}>New channel</button></>}{can(permissions, "communication.manage") && <button type="button" onClick={() => { setShowCreate("project"); setQuickActionsOpen(false); }}>New project</button>}</div>}</div>}
      </div>
    </header>
    {showNotifications && <section id="communication-alerts" className="communication-alerts" aria-label="Communication alerts"><div className="communication-alerts-head"><div><strong>Alerts</strong><p>Mentions, replies, messages and reminders</p></div><button type="button" className="button-quiet" disabled={!notifications.some((item) => !item.read_at)} onClick={() => void readNotifications()}>Mark all read</button></div>{notifications.length ? <ul>{notifications.map((item) => <li key={item.id}><span className={item.read_at ? "communication-alert-dot is-read" : "communication-alert-dot"} aria-hidden="true" /><div><strong>{item.kind === "mention" ? "You were mentioned" : item.kind === "thread_reply" ? "New thread reply" : item.kind === "dm" ? "New direct message" : item.kind === "message" ? "New channel message" : item.kind === "task" ? "Task assigned to you" : item.kind === "reminder" ? "Message reminder" : "Communication update"}</strong><time>{time(item.created_at)}</time></div>{!item.read_at && <span className="communication-alert-unread">Unread</span>}<button type="button" className="button-quiet" onClick={() => void openNotification(item)}>Open</button></li>)}</ul> : <p className="communication-alerts-empty">No alerts yet.</p>}</section>}
    {showSaved && <section className="communication-alerts" aria-label="Saved messages"><div className="communication-alerts-head"><strong>Saved messages</strong><button type="button" className="button-quiet" onClick={() => setShowSaved(false)}>Close</button></div>{savedMessages.length ? <ul>{savedMessages.map((message) => <li key={message.id}><div><strong>{message.author.display_name}</strong><span>{message.plaintext}</span></div><button type="button" className="button-quiet" onClick={() => { const conversation = data.conversations.find((item) => item.id === message.conversation_id); if (conversation) { setSelected(conversation); setNotificationTarget(message.id); setShowSaved(false); } }}>Open</button></li>)}</ul> : <p className="communication-alerts-empty">No saved messages yet.</p>}</section>}
    {error && <div className="api-error" role="alert">{error}</div>}
    {editingMessage && <div className="team-modal-backdrop"><form className="team-modal panel" role="dialog" aria-modal="true" aria-label="Edit message" onSubmit={(event) => void saveMessageEdit(event)}><div className="team-modal-head"><div><span className="eyebrow">Communication</span><h2>Edit message</h2><p>Existing mentions and linked leads stay attached.</p></div><button type="button" className="modal-close" onClick={() => setEditingMessage(null)} aria-label="Close">×</button></div>{dialogError && <div className="api-error" role="alert">{dialogError}</div>}<label className="team-field"><span>Message</span><textarea value={editingText} onChange={(event) => setEditingText(event.target.value)} maxLength={20000} required /></label><div className="team-modal-actions"><button type="button" className="button-quiet" disabled={editBusy} onClick={() => setEditingMessage(null)}>Cancel</button><button className="primary-button" disabled={editBusy || !editingText.trim()}>{editBusy ? "Saving…" : "Save changes"}</button></div></form></div>}
    {notice && <div className="communication-notice" role="status">{notice}<button type="button" onClick={() => setNotice(null)} aria-label="Dismiss notification"><Icon name="close" size={16} /></button></div>}
    <button type="button" className="communication-mobile-toggle" aria-expanded={mobileConversationsOpen} aria-controls="communication-conversations" onClick={() => setMobileConversationsOpen((open) => !open)}><Icon name="sidebar" size={17} /> {mobileConversationsOpen ? "Hide conversations" : "Show conversations"}<span>{data.conversations.length}</span></button>
    {showMeeting && <GoogleMeetDialog defaultTitle={selected ? `Meeting · ${conversationLabel(selected, data.members)}` : ""} onClose={() => setShowMeeting(false)} onCreated={(result) => { setShowMeeting(false); const link = result.meet_url || result.event_url; if (link) setText((current) => `${current}${current ? "\n" : ""}${link}`); setNotice(result.meet_url ? "Meeting created. The Google Meet link was added to your draft." : "Event created. Google is still preparing the Meet link; open the event link in Google Calendar."); }} />}
    <div className="communication-layout">
      <aside id="communication-conversations" className={`communication-sidebar${mobileConversationsOpen ? " is-mobile-open" : ""}`}>
        <div className="communication-sidebar-title"><div><strong>Conversations</strong><small>Your workspace, together</small></div><span className="communication-count">{data.conversations.length}</span></div>
        <label className="communication-search"><Icon name="search" size={17} /><span className="sr-only">Search communication</span><input value={search} onChange={(event) => void runSearch(event.target.value)} placeholder="Search messages" /></label>
        {searchResults.length > 0 && <div className="communication-search-results">{searchResults.map((message) => <button key={message.id} onClick={() => { const conversation = data.conversations.find((item) => item.id === message.conversation_id); if (conversation) setSelected(conversation); void runSearch(""); }}><b>{message.author.display_name}</b><span>{message.plaintext}</span></button>)}</div>}
        <div className="communication-side-head"><span>Projects</span>{can(permissions, "communication.manage") && <button onClick={() => setShowCreate("project")} aria-label="Create project">+</button>}</div>
        {grouped.map(({ project, conversations }) => <div className="communication-project" key={project.id}><b title={project.description}>{project.name}</b>{project.description && <small>{project.description}</small>}{conversations.map((conversation) => <ConversationButton key={conversation.id} conversation={conversation} active={selected?.id === conversation.id} members={data.members} onClick={() => { setSelected(conversation); setMobileConversationsOpen(false); }} />)}</div>)}
        <div className="communication-side-head"><span>Channels & group messages</span>{can(permissions, "communication.create") && <button onClick={() => setShowCreate("channel")} aria-label="Create channel">+</button>}</div>
        {ungrouped.filter((conversation) => conversation.kind !== "dm").map((conversation) => <ConversationButton key={conversation.id} conversation={conversation} active={selected?.id === conversation.id} members={data.members} onClick={() => { setSelected(conversation); setMobileConversationsOpen(false); }} />)}
        <div className="communication-side-head"><span>Direct messages</span></div>
        {data.members.filter((member) => member.id !== data.active_user_id).map((member) => {
          const conversation = data.conversations.find((item) => item.kind === "dm" && !item.is_archived && item.member_ids.includes(member.id) && item.member_ids.includes(data.active_user_id));
          return <button key={member.id} type="button" className={`communication-member${conversation && conversation.id === selected?.id ? " active" : ""}`} onClick={() => void openDirectMessage(member.id)} disabled={!!openingMemberId || (!conversation && !can(permissions, "communication.create"))} aria-label={`Message ${member.display_name}, ${onlineMemberIds === null ? "status unavailable" : onlineMemberIds.includes(member.id) ? "online" : "offline"}${conversation?.unread_count ? `, ${conversation.unread_count} unread ${conversation.unread_count === 1 ? "message" : "messages"}` : ""}`}><span className="communication-member-avatar" aria-hidden="true">{member.display_name.slice(0, 1).toUpperCase()}</span><span className="communication-member-name">{member.display_name}</span>{!!conversation?.unread_count && <span className="communication-unread" aria-hidden="true">{conversation.unread_count > 99 ? "99+" : conversation.unread_count}</span>}<span className={`communication-presence${onlineMemberIds?.includes(member.id) ? " is-online" : ""}`} title={onlineMemberIds === null ? "Status unavailable" : onlineMemberIds.includes(member.id) ? "Recently active" : "Offline"} aria-hidden="true" />{openingMemberId === member.id && <small>Opening…</small>}</button>;
        })}
        {data.members.length <= 1 && <p className="communication-members-empty">Workspace members will appear here.</p>}
      </aside>
      <main className="communication-main">
        {selected ? <>
          <header className="conversation-header"><div className="conversation-heading"><div className="conversation-heading-icon" aria-hidden="true">{selected.kind === "channel" ? "#" : <Icon name="team" size={22} />}</div><div><span>{selected.kind === "channel" ? "CHANNEL" : "DIRECT MESSAGE"}</span><h2>{conversationLabel(selected, data.members)}</h2><p>{selected.description || "Private workspace conversation"}</p></div></div><div className="conversation-meta"><button type="button" className="button-quiet" aria-pressed={mutedConversations.includes(selected.id)} onClick={() => void toggleMute()}>{mutedConversations.includes(selected.id) ? "Unmute" : "Mute"}</button><span><Icon name="team" size={15} /> {selected.member_ids.length} {selected.member_ids.length === 1 ? "member" : "members"}</span>{selected.primary_lead && <LeadChip lead={selected.primary_lead} onOpen={() => onOpenLead(selected.primary_lead!.id)} />}</div></header>
          {can(permissions, "agents.execute") && <div className="communication-recap"><button type="button" className="button-quiet" aria-expanded={showRecap} onClick={() => setShowRecap((open) => !open)}>Conversation recap</button>{showRecap && <div className="communication-summary"><small>Generating a recap shares recent conversation messages with your workspace AI provider.</small><button type="button" className="secondary-button" disabled={recapBusy} onClick={() => void generateRecap()}>{recapBusy ? "Generating…" : recap ? "Refresh recap" : "Generate recap"}</button>{recapError && <p role="alert">{recapError}</p>}{recap && <div role="status"><strong>Recap · {recap.message_count} messages</strong><p>{recap.summary}</p>{recap.truncated && <small>Some earlier or lengthy content was omitted.</small>}</div>}</div>}</div>}
          <div className="message-list" aria-live="polite">{messages.length ? messages.map((message) => <article className="message" id={`communication-message-${message.id}`} key={message.id}><div className="message-avatar" aria-hidden="true">{message.author.display_name.slice(0, 1).toUpperCase()}</div><div><header><b>{message.author.display_name}</b><time>{time(message.created_at)}</time>{message.is_pinned && <span className="message-pin">Pinned</span>}<button className="message-pin-button" onClick={() => void pin(message.id)} aria-label={`${message.is_pinned ? "Unpin" : "Pin"} message from ${message.author.display_name}`}><Icon name="pin" size={16} /></button><button className="message-pin-button" onClick={() => void remind(message.id)} aria-label={`Remind me about message from ${message.author.display_name}`}><Icon name="clock" size={16} /></button></header><FormattedMessage text={message.plaintext} mentions={data.members.filter((member) => message.mentioned_user_ids.includes(member.id))} onToggle={can(permissions, "communication.create") && !message.deleted_at ? (line, completed) => void updateChecklist(message, line, completed) : undefined} /><MessageLinkPreview text={message.plaintext} /><MessageAttachments message={message} />{message.mentioned_user_ids.length > 0 && <div className="message-tags">{message.mentioned_user_ids.map((id) => <span key={id}>@{data.members.find((member) => member.id === id)?.display_name || "member"}</span>)}</div>}{message.leads.map((lead) => <LeadChip key={lead.id} lead={lead} onOpen={() => onOpenLead(lead.id)} />)}{selected.kind === "dm" && message.author.id === data.active_user_id && <span className="message-read-status">{message.seen_by?.length ? "Seen" : "Not seen yet"}</span>}{message.tasks?.map((task) => <div className="communication-task" key={task.id}><label><input type="checkbox" checked={!!task.completed_at} disabled={task.assignee_id !== data.active_user_id && task.created_by !== data.active_user_id && !can(permissions, "communication.moderate")} onChange={(event) => void toggleTask(message.id, task.id, event.target.checked)} /> <span>{task.title}</span></label><small>{data.members.find((member) => member.id === task.assignee_id)?.display_name || "Member"}{task.due_at ? ` · Due ${new Date(task.due_at).toLocaleString()}` : ""}</small></div>)}{!message.deleted_at && <div className="communication-reactions" aria-label="Message reactions">{([ ["thumbs_up", "👍", "Thumbs up"], ["heart", "❤️", "Heart"], ["celebrate", "🙌", "Celebrate"], ["eyes", "👀", "Eyes"], ["check", "✅", "Done"] ] as const).map(([key, icon, label]) => { const reaction = message.reactions?.find((item) => item.reaction === key); return <button key={key} type="button" disabled={!can(permissions, "communication.create")} aria-pressed={!!reaction?.mine} aria-label={`${label}, ${reaction?.count || 0} reactions`} onClick={() => void react(message, key)}><span aria-hidden="true">{icon}</span>{!!reaction?.count && <span>{reaction.count}</span>}</button>; })}</div>}{!message.deleted_at && can(permissions, "communication.create") && <button className="message-thread-button" onClick={() => setTaskMessage(message)}>Create task</button>}<button className="message-thread-button" aria-label={`Quick reply to ${message.author.display_name} in a thread`} onClick={() => setThreadRoot(message)}>Quick reply</button>{!message.deleted_at && can(permissions, "communication.create") && <button className="message-thread-button" onClick={() => { forwardRequestRef.current = crypto.randomUUID(); setForwardAttempted(false); setForwardDestination(""); setForwardMessage(message); }}>Forward</button>}{!message.deleted_at && <button className="message-thread-button" aria-pressed={!!message.is_saved} onClick={() => void toggleSaved(message)}>{message.is_saved ? "Remove saved" : "Save"}</button>}{!message.deleted_at && can(permissions, "communication.create") && (message.author.id === data.active_user_id || can(permissions, "communication.moderate")) && <><button className="message-thread-button" onClick={() => { setEditingMessage(message); setEditingText(message.plaintext); }}>Edit</button><button className="message-thread-button" onClick={() => void deleteMessage(message)}>Delete</button></>}</div></article>) : <div className="conversation-empty"><div className="communication-empty-content"><span className="communication-empty-icon"><Icon name="team" size={25} /></span><strong>Start the conversation</strong><p>Bring your team together here. Share an update, a lead, or an idea to get things moving.</p></div></div>}</div>
          {!!missingDraftAttachments.length && <div className="communication-notice" role="status"><span>Reattach these draft files: {missingDraftAttachments.join(", ")}</span><button type="button" onClick={() => setMissingDraftAttachments([])}>Send without files</button></div>}
          {selected && failedUploads[selected.id] && <div className="communication-notice" role="status"><span>{failedUploads[selected.id].items.length} attachment(s) still need uploading.</span><button type="button" className="button-quiet" disabled={sending} onClick={() => void retryAttachments(selected.id)}>Retry upload</button><button type="button" className="button-quiet" disabled={sending} onClick={() => setFailedUploads((current) => { const next = { ...current }; delete next[selected.id]; return next; })}>Discard</button></div>}
          {can(permissions, "communication.create") && <form className="message-composer" onSubmit={(event) => void send(event)} onDragOver={(event) => { if (can(permissions, "communication.attach") && event.dataTransfer.types.includes("Files")) event.preventDefault(); }} onDrop={(event) => { if (!can(permissions, "communication.attach") || !event.dataTransfer.files.length) return; event.preventDefault(); const files = Array.from(event.dataTransfer.files); restoreAttachmentNames(files); setPendingAttachments((current) => [...current, ...files.map((file) => ({ file, kind: "file" as const, upload_id: crypto.randomUUID() }))]); }}>
            <div className="composer-input-wrap"><textarea ref={composerRef} disabled={sending || loadedDraftId !== selected.id} value={text} onChange={(event) => updateComposer(event.target.value, event.target.selectionStart)} onKeyDown={(event) => { if (event.key === "Escape" && mentionQuery) { event.preventDefault(); setMentionQuery(null); } else if (event.key === "ArrowDown" && mentionSuggestions.length) { event.preventDefault(); document.querySelector<HTMLButtonElement>("#communication-mention-suggestions button")?.focus(); } else if (event.key === "Enter" && !event.shiftKey && !event.altKey && !event.nativeEvent.isComposing && !mentionSuggestions.length) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={`Message ${conversationLabel(selected, data.members)}`} aria-label="Message text" aria-autocomplete="list" aria-controls={mentionSuggestions.length ? "communication-mention-suggestions" : undefined} />{mentionSuggestions.length > 0 && <div id="communication-mention-suggestions" className="composer-mention-suggestions" role="listbox" aria-label="Mention a conversation member">{mentionSuggestions.map((member) => <button key={member.id} type="button" role="option" aria-selected="false" onMouseDown={(event) => event.preventDefault()} onClick={() => insertMention(member)}><span>{member.display_name.slice(0, 1).toUpperCase()}</span><div><strong>{member.display_name}</strong><small>{member.email}</small></div></button>)}</div>}</div>
            {text.trim() && /\*\*|__|~~|_[^_]+_|(?:^|\n)\s*(?:- |\d+\. )|\[[^\]]+\]\(https?:/i.test(text) && <div className="composer-format-preview"><strong>Formatting preview</strong><FormattedMessage text={text} mentions={selectedMentions} /></div>}
            <div className="composer-tools">
              <div className="formatting-tools" aria-label="Text formatting"><button type="button" aria-label="Bold" title="Bold" onClick={() => formatSelection("**")}><Icon name="bold" size={16} /></button><button type="button" aria-label="Italic" title="Italic" onClick={() => formatSelection("_")}><Icon name="italic" size={16} /></button><button type="button" aria-label="Underline" title="Underline" onClick={() => formatSelection("__")}><Icon name="underline" size={16} /></button><button type="button" aria-label="Strikethrough" title="Strikethrough" onClick={() => formatSelection("~~")}><s>S</s></button><button type="button" aria-label="Bulleted list" title="Bulleted list" onClick={() => formatLines("- ")}><Icon name="listBullets" size={16} /></button><button type="button" aria-label="Numbered list" title="Numbered list" onClick={() => formatLines("number")}>1.</button><button type="button" aria-label="Indent" title="Indent" onClick={() => formatLines("  ")}>→</button><button type="button" aria-label="To-do list" title="To-do list" onClick={() => formatLines("- [ ] ")}>☐</button><button type="button" aria-label="Insert link" title="Insert link" onClick={formatLink}><Icon name="link" size={16} /></button></div>
              <details name="communication-composer-picker"><summary>@ Mention</summary><div className="composer-popover">{data.members.filter((member) => selected.member_ids.includes(member.id)).map((member) => <label key={member.id}><input type="checkbox" checked={mentionIds.includes(member.id)} onChange={() => setMentionIds((current) => current.includes(member.id) ? current.filter((id) => id !== member.id) : [...current, member.id])} /> {member.display_name}</label>)}</div></details>
              <details name="communication-composer-picker"><summary>Link lead</summary><div className="composer-popover"><label className="sr-only" htmlFor="communication-lead-search">Search leads</label><input id="communication-lead-search" type="search" placeholder="Search leads" value={leadSearch} onChange={(event) => setLeadSearch(event.target.value)} />{leadSearchError && <p role="alert">{leadSearchError}</p>}{(leadSearch.trim() ? leadSearchResults : leads).length ? (leadSearch.trim() ? leadSearchResults : leads).map((lead) => <label key={lead.id}><input type="checkbox" checked={leadIds.includes(lead.id)} onChange={() => { setLeads((current) => current.some((item) => item.id === lead.id) ? current : [...current, lead]); setLeadIds((current) => current.includes(lead.id) ? current.filter((id) => id !== lead.id) : [...current, lead.id]); }} /> {lead.full_name} · {lead.company_name}</label>) : <p>No leads found</p>}</div></details>
              {can(permissions, "leads.edit") && <button type="button" className="button-quiet" onClick={() => setShowMeeting(true)}>Create meeting</button>}{can(permissions, "communication.attach") && <label className="attachment-picker"><Icon name="paperclip" size={16} /> Attach<input type="file" multiple disabled={sending} onChange={(event) => { const files = Array.from(event.target.files || []); restoreAttachmentNames(files); setPendingAttachments((current) => [...current, ...files.map((file) => ({ file, kind: "file" as const, upload_id: crypto.randomUUID() }))]); event.target.value = ""; }} /></label>}
              {can(permissions, "communication.attach") && <button type="button" className="button-quiet communication-voice" disabled={sending} onClick={() => recording ? stopVoiceRecording() : void startVoiceRecording()}><Icon name="mic" size={16} /> {recording ? `Finish recording · ${Math.floor(recordingSeconds / 60)}:${String(recordingSeconds % 60).padStart(2, "0")}` : "Voice message"}</button>}{recording && <button type="button" className="button-quiet" onClick={pauseVoiceRecording}>{recordingPaused ? "Resume" : "Pause"}</button>}
              {(selectedMentions.length > 0 || selectedLeads.length > 0) && <span className="composer-selected">{selectedMentions.map((member) => `@${member.display_name}`).join(" ")} {selectedLeads.map((lead) => lead.full_name).join(" ")}</span>}{pendingAttachments.map((attachment, index) => <PendingAttachmentPreview key={`${attachment.file.name}-${index}`} attachment={attachment} onRemove={() => setPendingAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))} />)}
              <button className="primary-button communication-send" disabled={sending || !!failedUploads[selected.id] || !!missingDraftAttachments.length || loadedDraftId !== selected.id || (!text.trim() && !pendingAttachments.length) || recording}>{sending ? "Sending…" : "Send"} <Icon name="send" size={15} /></button>
            </div>
          </form>}
        </> : <div className="conversation-empty">Create a channel to start collaborating.</div>}
      </main>
    </div>
    {showCreate && <div className="team-modal-backdrop"><form className="team-modal panel" role="dialog" aria-modal="true" aria-label="Create conversation" onSubmit={(event) => void create(event)}><div className="team-modal-head"><div><span className="eyebrow">Communication</span><h2>{showCreate === "project" ? "Create project" : showCreate === "dm" ? "New direct message" : showCreate === "group" ? "New group message" : "Create channel"}</h2><p>{showCreate === "dm" ? "Start a private conversation with one workspace member." : showCreate === "group" ? "Start a private group with at least two teammates." : "Keep work organized with clear names, membership, and descriptions."}</p></div><button type="button" className="modal-close" onClick={() => { setShowCreate(null); setNewMemberIds([]); }} aria-label="Close">×</button></div>{dialogError && <div className="api-error" role="alert">{dialogError}</div>}{showCreate === "dm" ? <label className="team-field"><span>Member</span><select name="member_id" required defaultValue=""><option value="" disabled>Select a member</option>{data.members.filter((member) => member.id !== data.active_user_id).map((member) => <option key={member.id} value={member.id}>{member.display_name} · {member.email}</option>)}</select></label> : showCreate === "group" ? <MemberPicker members={data.members} activeUserId={data.active_user_id} selectedIds={newMemberIds} onChange={setNewMemberIds} /> : <><label className="team-field"><span>Name</span><input name="name" required /></label><label className="team-field"><span>Description</span><textarea name="description" /></label>{showCreate === "channel" && <><label className="team-field"><span>Project</span><select name="project_id" defaultValue=""><option value="">No project</option>{data.projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label><MemberPicker members={data.members} activeUserId={data.active_user_id} selectedIds={newMemberIds} onChange={setNewMemberIds} /></>}</>}<div className="team-modal-actions"><button type="button" className="button-quiet" onClick={() => { setShowCreate(null); setNewMemberIds([]); }}>Cancel</button><button className="primary-button" disabled={showCreate === "group" && newMemberIds.length < 2}>Create</button></div></form></div>}
    {forwardMessage && <div className="team-modal-backdrop"><form className="team-modal panel" role="dialog" aria-modal="true" aria-label="Forward message" onSubmit={(event) => void forward(event)}><div className="team-modal-head"><div><span className="eyebrow">Communication</span><h2>Forward message</h2><p>The message and its attachments will be shared with members of the selected conversation.</p></div><button type="button" className="modal-close" disabled={forwardBusy} onClick={() => setForwardMessage(null)} aria-label="Close">×</button></div>{dialogError && <div className="api-error" role="alert">{dialogError}</div>}{forwardAttempted && dialogError && <p>Retry uses the same request. Check the destination before starting a new forward.</p>}<label className="team-field"><span>Conversation</span><select value={forwardDestination} disabled={forwardAttempted} onChange={(event) => setForwardDestination(event.target.value)} required><option value="">Select a conversation</option>{data.conversations.filter((item) => !item.is_archived).map((item) => <option key={item.id} value={item.id}>{conversationLabel(item, data.members)}</option>)}</select></label><div className="communication-summary"><strong>{forwardMessage.author.display_name}</strong><p>{forwardMessage.plaintext}</p><small>{forwardMessage.attachments.length} attachment(s)</small></div><div className="team-modal-actions"><button type="button" className="button-quiet" disabled={forwardBusy} onClick={() => setForwardMessage(null)}>Cancel</button><button className="primary-button" disabled={forwardBusy || !forwardDestination}>{forwardBusy ? "Forwarding…" : forwardAttempted ? "Retry forward" : "Forward"}</button></div></form></div>}
    {taskMessage && selected && <div className="team-modal-backdrop"><form className="team-modal panel" role="dialog" aria-modal="true" aria-label="Create task" onSubmit={(event) => void createTask(event)}><div className="team-modal-head"><div><span className="eyebrow">From this message</span><h2>Create task</h2><p>Assign a follow-up and keep the original message as context.</p></div><button type="button" className="modal-close" onClick={() => setTaskMessage(null)} aria-label="Close">×</button></div>{dialogError && <div className="api-error" role="alert">{dialogError}</div>}<label className="team-field"><span>Task</span><input name="title" maxLength={255} required /></label><label className="team-field"><span>Assign to</span><select name="assignee_id" required defaultValue=""><option value="" disabled>Select a conversation member</option>{data.members.filter((member) => selected.member_ids.includes(member.id)).map((member) => <option key={member.id} value={member.id}>{member.display_name}</option>)}</select></label><label className="team-field"><span>Due date (optional)</span><input type="datetime-local" name="due_at" /></label><div className="team-modal-actions"><button type="button" className="button-quiet" onClick={() => setTaskMessage(null)}>Cancel</button><button className="primary-button" disabled={taskSaving}>{taskSaving ? "Creating…" : "Create task"}</button></div></form></div>}
    {threadRoot && <div className="team-modal-backdrop"><section className="team-modal panel communication-thread" role="dialog" aria-modal="true" aria-label="Message thread"><div className="team-modal-head"><div><span className="eyebrow">Thread</span><h2>{threadRoot.author.display_name}</h2><p>{threadRoot.plaintext}</p></div><button type="button" className="modal-close" onClick={() => setThreadRoot(null)} aria-label="Close thread">×</button></div>{dialogError && <div className="api-error" role="alert">{dialogError}</div>}{can(permissions, "agents.execute") && <div className="communication-summary"><button type="button" className="secondary-button" disabled={summaryBusy} onClick={() => void summarizeThread()}>{summaryBusy ? "Summarizing…" : "Summarize with Ask Corral"}</button><small>Shares this thread with your workspace AI provider.</small>{summaryError && <p role="alert">{summaryError}</p>}{threadSummary && <div role="status"><strong>AI summary · {threadSummary.message_count} messages</strong><p>{threadSummary.summary}</p>{threadSummary.truncated && <small>Some thread content was omitted due to size limits.</small>}</div>}</div>}<div className="thread-replies">{threadReplies.length ? threadReplies.map((reply) => <article key={reply.id}><b>{reply.author.display_name}</b><time>{time(reply.created_at)}</time><p>{reply.plaintext}</p></article>) : <p>No replies yet.</p>}</div>{can(permissions, "communication.create") && <form className="message-composer" onSubmit={(event) => void replyInThread(event)}><textarea disabled={threadSending} value={threadText} onChange={(event) => setThreadText(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.altKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder="Reply in thread" aria-label="Thread reply" /><div className="composer-tools"><button className="primary-button" disabled={threadSending || !threadText.trim()}>{threadSending ? "Sending…" : "Reply"}</button></div></form>}</section></div>}
  </section>;
}

function ConversationButton({ conversation, active, members, onClick }: { conversation: CommunicationConversation; active: boolean; members: CommunicationBootstrap["members"]; onClick: () => void }) {
  const label = conversationLabel(conversation, members);
  const unread = conversation.unread_count || 0;
  return <button className={`conversation-button ${active ? "active" : ""}`} onClick={onClick} aria-label={`${label}${unread ? `, ${unread} unread ${unread === 1 ? "message" : "messages"}` : ""}`}><span>{conversation.kind === "channel" ? "#" : "•"}</span><span className="conversation-name">{label}</span>{unread > 0 && <span className="communication-unread" aria-hidden="true">{unread > 99 ? "99+" : unread}</span>}</button>;
}

function LeadChip({ lead, onOpen }: { lead: { full_name: string; company_name: string; qualification: string | null; source: string; owner_name: string | null; pipeline_stage: string | null }; onOpen: () => void }) {
  return <button className="communication-lead-chip" onClick={onOpen}><span>Lead</span><b>{lead.full_name}</b><small>{lead.company_name} · {lead.qualification || lead.source}</small><small>{lead.owner_name ? `Owner: ${lead.owner_name}` : "Unassigned"}{lead.pipeline_stage ? ` · ${lead.pipeline_stage.replaceAll("_", " ")}` : ""}</small></button>;
}

function MemberPicker({ members, activeUserId, selectedIds, onChange }: { members: CommunicationBootstrap["members"]; activeUserId: string; selectedIds: string[]; onChange: (ids: string[]) => void }) {
  return <fieldset className="communication-member-picker"><legend>Include team members</legend>{members.filter((member) => member.id !== activeUserId).map((member) => <label key={member.id}><input type="checkbox" checked={selectedIds.includes(member.id)} onChange={() => onChange(selectedIds.includes(member.id) ? selectedIds.filter((id) => id !== member.id) : [...selectedIds, member.id])} /> {member.display_name} <small>{member.email}</small></label>)}</fieldset>;
}

function MessageLinkPreview({ text }: { text: string }) {
  const url = text.match(/https?:\/\/[^\s<]+/i)?.[0];
  const [preview, setPreview] = useState<CommunicationLinkPreview | null>(null);
  useEffect(() => { if (!url) return; let active = true; void getCommunicationLinkPreview(url).then((value) => { if (active) setPreview(value); }).catch(() => { if (active) setPreview(null); }); return () => { active = false; }; }, [url]);
  if (!preview) return null;
  return <a className="communication-link-preview" href={preview.url} target="_blank" rel="noreferrer noopener"><span>{preview.domain}</span><b>{preview.title || preview.domain}</b>{preview.description && <small>{preview.description}</small>}</a>;
}

function PendingAttachmentPreview({ attachment, onRemove }: { attachment: PendingAttachment; onRemove: () => void }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (attachment.kind !== "voice") return;
    const objectUrl = URL.createObjectURL(attachment.file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [attachment.file, attachment.kind]);
  const seconds = Math.floor((attachment.duration_ms || 0) / 1000);
  return <div className="communication-pending-attachment">
    <span>{attachment.kind === "voice" ? `Voice message · ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}` : attachment.file.name}</span>
    {url && <audio controls preload="metadata" src={url}>Voice recording preview</audio>}
    <button type="button" className="button-quiet" onClick={onRemove} aria-label={`Remove ${attachment.file.name}`}>Remove</button>
  </div>;
}

function MessageAttachments({ message }: { message: CommunicationMessage }) {
  const [copied, setCopied] = useState<string | null>(null);
  const [copyError, setCopyError] = useState(false);
  async function copyLink(attachment: CommunicationAttachment) {
    try {
      await navigator.clipboard.writeText(new URL(apiUrl(attachment.download_url), window.location.href).href);
      setCopied(attachment.id); setCopyError(false);
    } catch { setCopyError(true); }
  }
  if (!message.attachments.length) return null;
  return <div className="message-attachments">{message.attachments.map((attachment) => <div key={attachment.id}>
    {attachment.kind === "voice" ? <audio controls preload="metadata" src={apiUrl(attachment.download_url)}>Voice message: {attachment.filename}</audio>
      : <a href={apiUrl(attachment.download_url)} target="_blank" rel="noreferrer">Attachment · {attachment.filename}</a>}
    <a href={apiUrl(`${attachment.download_url}?download=true`)} download>Download</a>
    <button type="button" className="button-quiet" onClick={() => void copyLink(attachment)}>{copied === attachment.id ? "Link copied" : "Copy link"}</button>
  </div>)}{copyError && <p role="alert">Could not copy the link. Check browser clipboard permissions.</p>}</div>;
}

function FormattedMessage({ text, onToggle, mentions = [] }: { text: string; onToggle?: (line: number, completed: boolean) => void; mentions?: CommunicationBootstrap["members"] }) {
  const inline = (value: string) => formatInline(value, mentions);
  return <div className="formatted-message">{text.split("\n").map((line, index) => {
    const todo = line.match(/^(\s*)- \[([ xX])\] (.*)$/);
    if (todo) return <label className={`formatted-todo${todo[2].toLowerCase() === "x" ? " completed" : ""}`} key={index} style={{ paddingLeft: `${Math.min(todo[1].length, 12) * 0.5}em` }}><input type="checkbox" checked={todo[2].toLowerCase() === "x"} disabled={!onToggle} onChange={(event) => onToggle?.(index, event.target.checked)} /> <span>{inline(todo[3])}</span></label>;
    const numbered = line.match(/^(\s*)(\d+)\.\s+(.*)$/);
    if (numbered) return <div className="formatted-bullet" key={index} style={{ paddingLeft: `${Math.min(numbered[1].length, 12) * 0.5}em` }}><span aria-hidden="true">{numbered[2]}.</span>{inline(numbered[3])}</div>;
    const indentation = line.match(/^(\s+)(- (?:\[[ x]\] )?.*)$/i);
    if (indentation) return <div key={index} style={{ paddingLeft: `${Math.min(indentation[1].length, 12) * 0.5}em` }}><FormattedMessage text={indentation[2]} mentions={mentions} /></div>;
    if (line.startsWith("- ")) return <div className="formatted-bullet" key={index}><span aria-hidden="true">•</span>{inline(line.slice(2))}</div>;
    return <p key={index}>{inline(line) || " "}</p>;
  })}</div>;
}

function formatInline(value: string, mentions: CommunicationBootstrap["members"] = []) {
  return value.split(/(\[[^\]]+\]\(https?:\/\/[^)\s]+\)|https?:\/\/[^\s<>]+|\*\*[^*]+\*\*|~~[^~]+~~|__[^_]+__|_[^_]+_)/gi).map((token, index) => {
    if (token.startsWith("**") && token.endsWith("**")) return <strong key={index}>{token.slice(2, -2)}</strong>;
    if (token.startsWith("~~") && token.endsWith("~~")) return <s key={index}>{token.slice(2, -2)}</s>;
    if (token.startsWith("__") && token.endsWith("__")) return <u key={index}>{token.slice(2, -2)}</u>;
    if (token.startsWith("_") && token.endsWith("_")) return <em key={index}>{token.slice(1, -1)}</em>;
    const link = token.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/);
    if (link) return <a key={index} href={link[2]} target="_blank" rel="noreferrer noopener">{link[1]}</a>;
    if (/^https?:\/\//i.test(token)) {
      const url = token.replace(/[.,!?;:]+$/, "");
      return <span key={index}><a href={url} target="_blank" rel="noreferrer noopener">{url}</a>{token.slice(url.length)}</span>;
    }
    const names = mentions.map((member) => member.display_name).filter(Boolean).sort((a, b) => b.length - a.length);
    if (!names.length) return token;
    const escaped = names.map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    const pattern = new RegExp(`(@(?:${escaped.join("|")}))(?=$|[\\s.,!?;:])`, "g");
    return <span key={index}>{token.split(pattern).map((part, partIndex) => names.some((name) => part === `@${name}`) ? <span className="communication-inline-mention" key={partIndex}>{part}</span> : part)}</span>;
  });
}
