"use client";

import { useState, useEffect, useRef } from "react";
import { Sidebar } from "@/components/sidebar";
import { ChatMessage } from "@/components/chat-message";
import { ChatInput } from "@/components/chat-input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  askQuestion,
  listSessions,
  getSession,
  deleteSession,
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
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Load sessions on mount
  useEffect(() => {
    loadSessions();
  }, []);

  // Scroll to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  async function loadSessions() {
    try {
      const list = await listSessions();
      setSessions(list);
    } catch {
      // Silently fail — sessions just won't show
    }
  }

  async function handleSelectSession(sid: string) {
    setActiveSessionId(sid);
    try {
      const detail = await getSession(sid);
      setMessages(
        detail.messages.map((m: Message) => ({
          role: m.role,
          content: m.content,
          metadata: m.metadata || {},
        }))
      );
    } catch {
      setMessages([]);
    }
  }

  function handleNewChat() {
    setActiveSessionId(null);
    setMessages([]);
  }

  async function handleDeleteSession(sid: string) {
    await deleteSession(sid);
    if (activeSessionId === sid) {
      setActiveSessionId(null);
      setMessages([]);
    }
    await loadSessions();
  }

  async function handleSend(question: string) {
    // Add user message immediately
    const userMsg: LocalMessage = { role: "user", content: question, metadata: {} };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    try {
      const response = await askQuestion(question, activeSessionId || undefined);

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
          route_reason: response.route_reason,
          sql: response.sql,
          chart_spec: response.chart_spec,
          columns: response.columns,
          row_count: response.row_count,
          error: response.error,
        },
      };
      setMessages((prev) => [...prev, assistantMsg]);

      // Refresh session list
      await loadSessions();
    } catch (err) {
      const errorMsg: LocalMessage = {
        role: "assistant",
        content: `Error: ${err instanceof Error ? err.message : "Something went wrong"}`,
        metadata: {},
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setLoading(false);
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
    try {
      const result = await uploadFiles(sid, files);

      // Show upload result as a system message
      let content = "";
      if (result.uploaded.length > 0) {
        content += `Uploaded ${result.uploaded.length} file(s):\n`;
        result.uploaded.forEach((u) => {
          content += `  • ${u.filename} → table "${u.table_name}" (${u.rows.toLocaleString()} rows, ${u.columns.length} columns)\n`;
        });
      }
      if (result.errors.length > 0) {
        content += `\nFailed:\n`;
        result.errors.forEach((e) => {
          content += `  • ${e.filename}: ${e.error}\n`;
        });
      }
      content += "\nYou can now ask questions about your data.";

      const uploadMsg: LocalMessage = {
        role: "assistant",
        content,
        metadata: { route: "upload" },
      };
      setMessages((prev) => [...prev, uploadMsg]);
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
    }
  }

  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onNewChat={handleNewChat}
        onSelectSession={handleSelectSession}
        onDeleteSession={handleDeleteSession}
      />

      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Messages */}
        <ScrollArea className="flex-1" ref={scrollRef}>
          <div className="max-w-3xl mx-auto">
            {messages.length === 0 ? (
              <div className="flex items-center justify-center h-full min-h-[60vh] text-muted-foreground">
                <div className="text-center space-y-3">
                  <h2 className="text-2xl font-semibold text-foreground">
                    DataPilot
                  </h2>
                  <p className="text-sm max-w-md">
                    Upload a CSV or Excel file and ask questions about your data.
                    The agent picks between SQL, Python, and visualizations automatically.
                  </p>
                </div>
              </div>
            ) : (
              messages.map((msg, i) => (
                <ChatMessage
                  key={i}
                  role={msg.role}
                  content={msg.content}
                  metadata={msg.metadata}
                />
              ))
            )}
          </div>
        </ScrollArea>

        {/* Input */}
        <ChatInput
          onSend={handleSend}
          onUpload={handleUpload}
          loading={loading}
          disabled={loading}
        />
      </div>
    </div>
  );
}
