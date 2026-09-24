"use client";

import { useState, useEffect, useRef } from "react";
import { Menu } from "lucide-react";
import { useAuth } from "@/components/auth-provider";
import { LoginPage } from "@/components/login-page";
import { Sidebar } from "@/components/sidebar";
import { ChatMessage } from "@/components/chat-message";
import { ChatInput } from "@/components/chat-input";
import {
  askQuestion,
  listSessions,
  getSession,
  deleteSession,
  renameSession,
  uploadFiles,
  type SessionListItem,
  type Message,
} from "@/lib/api";

interface LocalMessage {
  role: "user" | "assistant";
  content: string;
  metadata: Record<string, unknown>;
}

export default function ChatPage() {
  const { user, loading: authLoading, signOut } = useAuth();
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [pendingAction, setPendingAction] = useState<"asking" | "uploading" | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Load sessions when user is authenticated
  useEffect(() => {
    if (user) {
      loadSessions();
    }
  }, [user]);

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pendingAction]);

  async function loadSessions() {
    try {
      const list = await listSessions();
      setSessions(list);
    } catch {
      // Silently fail
    }
  }

  async function handleSelectSession(sid: string) {
    setActiveSessionId(sid);
    setSidebarOpen(false);
    try {
      const detail = await getSession(sid);
      setMessages(
        detail.messages.map((m: Message) => ({
          role: m.role,
          content: m.content,
          metadata: m.metadata || {},
        }))
      );
    } catch (err) {
      setMessages([
        {
          role: "assistant",
          content: `Couldn't load chat history: ${
            err instanceof Error ? err.message : "backend unreachable"
          }. Is the backend running?`,
          metadata: {},
        },
      ]);
    }
  }

  function handleNewChat() {
    setActiveSessionId(null);
    setMessages([]);
    setSidebarOpen(false);
  }

  async function handleDeleteSession(sid: string) {
    await deleteSession(sid);
    if (activeSessionId === sid) {
      setActiveSessionId(null);
      setMessages([]);
    }
    await loadSessions();
  }

  async function handleRenameSession(sid: string, title: string) {
    await renameSession(sid, title);
    await loadSessions();
  }

  async function handleSend(question: string) {
    // Add user message immediately
    const userMsg: LocalMessage = { role: "user", content: question, metadata: {} };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);
    setPendingAction("asking");

    // Create abort controller for this request
    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const response = await askQuestion(question, activeSessionId || undefined, controller.signal);

      // Set session if new
      if (!activeSessionId) {
        setActiveSessionId(response.session_id);
      }

      // Add assistant message
      const assistantMsg: LocalMessage = {
        role: "assistant",
        content: response.answer,
        metadata: {
          route: response.route,
          chart_spec: response.chart_spec,
          error: response.error,
        },
      };
      setMessages((prev) => [...prev, assistantMsg]);

      // Refresh session list
      await loadSessions();
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") {
        // User cancelled — add a note
        const cancelMsg: LocalMessage = {
          role: "assistant",
          content: "Generation stopped.",
          metadata: {},
        };
        setMessages((prev) => [...prev, cancelMsg]);
      } else {
        const errorMsg: LocalMessage = {
          role: "assistant",
          content: err instanceof Error && err.message.includes("401")
            ? "Your session expired. Please sign in again."
            : "Something went wrong. Please try again or rephrase your question.",
          metadata: {},
        };
        setMessages((prev) => [...prev, errorMsg]);
      }
    } finally {
      setLoading(false);
      setPendingAction(null);
      abortControllerRef.current = null;
    }
  }

  function handleStop() {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
  }

  async function handleUpload(files: File[]) {
    // Create session if none exists
    let sid = activeSessionId;
    if (!sid) {
      sid = crypto.randomUUID().replace(/-/g, "");
      setActiveSessionId(sid);
    }

    setLoading(true);
    setPendingAction("uploading");
    try {
      const result = await uploadFiles(sid, files);

      if (result.uploaded.length > 0) {
        const names = result.uploaded.map((u) => u.filename).join(", ");
        const uploadMsg: LocalMessage = {
          role: "user",
          content: `📎 Uploaded: ${names}`,
          metadata: { type: "upload", files: result.uploaded },
        };
        setMessages((prev) => [...prev, uploadMsg]);
      }
      if (result.errors.length > 0) {
        const errorMsg: LocalMessage = {
          role: "assistant",
          content: `Some files couldn't be uploaded:\n${result.errors.map((e) => `• ${e.filename}: ${e.error}`).join("\n")}`,
          metadata: {},
        };
        setMessages((prev) => [...prev, errorMsg]);
      }
      await loadSessions();
    } catch (err) {
      const errorMsg: LocalMessage = {
        role: "assistant",
        content: `Upload failed: ${err instanceof Error ? err.message : "Unknown error"}`,
        metadata: {},
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setLoading(false);
      setPendingAction(null);
    }
  }

  return (
    <>
      {authLoading ? (
        <div className="flex h-screen items-center justify-center">
          <p className="text-muted-foreground">Loading...</p>
        </div>
      ) : !user ? (
        <LoginPage />
      ) : (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar — fixed, independent scroll */}
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onNewChat={handleNewChat}
        onSelectSession={handleSelectSession}
        onDeleteSession={handleDeleteSession}
        onRenameSession={handleRenameSession}
        onSignOut={signOut}
        userEmail={user?.email}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      {/* Main chat area — flex column, own scroll */}
      <div className="flex-1 flex flex-col min-w-0 h-screen overflow-hidden relative">
        {/* Mobile header — opens the sidebar drawer */}
        <div className="md:hidden flex items-center gap-2 border-b px-3 py-2">
          <button
            onClick={() => setSidebarOpen(true)}
            className="p-2 rounded hover:bg-muted"
            aria-label="Open menu"
          >
            <Menu className="w-5 h-5" />
          </button>
          <span className="text-sm font-semibold">DataPilot</span>
        </div>
        {/* Messages — scrollable independently */}
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-3xl mx-auto">
            {messages.length === 0 && !pendingAction ? (
              <div className="flex items-center justify-center min-h-[60vh] text-muted-foreground">
                <div className="text-center space-y-3">
                  <h2 className="text-2xl font-semibold text-foreground">
                    DataPilot
                  </h2>
                  <p className="text-sm max-w-md">
                    Upload a CSV or Excel file and ask questions about your data.
                  </p>
                </div>
              </div>
            ) : (
              <>
                {messages.map((msg, i) => (
                  <ChatMessage
                    key={i}
                    role={msg.role}
                    content={msg.content}
                    metadata={msg.metadata}
                  />
                ))}
                {pendingAction && (
                  <div className="flex gap-3 p-4" aria-live="polite">
                    <div className="w-7 h-7 rounded-full bg-green-600 flex items-center justify-center flex-shrink-0 mt-1">
                      <span className="sr-only">Working</span>
                    </div>
                    <div className="flex items-center gap-1.5 text-sm text-muted-foreground py-2">
                      <span className="sr-only">
                        {pendingAction === "uploading" ? "Uploading files…" : "Thinking…"}
                      </span>
                      <span className="w-2 h-2 rounded-full bg-muted-foreground/60 animate-bounce [animation-delay:-0.3s]" aria-hidden="true" />
                      <span className="w-2 h-2 rounded-full bg-muted-foreground/60 animate-bounce [animation-delay:-0.15s]" aria-hidden="true" />
                      <span className="w-2 h-2 rounded-full bg-muted-foreground/60 animate-bounce" aria-hidden="true" />
                      <span className="ml-1">
                        {pendingAction === "uploading" ? "Uploading…" : "Thinking…"}
                      </span>
                    </div>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </>
            )}
          </div>
        </div>

        {/* Input — pinned to bottom, centered when no messages */}
        <div className={messages.length === 0 && !pendingAction ? "absolute bottom-1/2 left-1/2 -translate-x-1/2 translate-y-1/2 w-full max-w-3xl px-4" : ""}>
          <ChatInput
            onSend={handleSend}
            onUpload={handleUpload}
            onStop={handleStop}
            loading={loading}
            uploading={pendingAction === "uploading"}
          />
        </div>
      </div>
    </div>
      )}
    </>
  );
}
