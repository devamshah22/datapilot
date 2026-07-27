"use client";

import { useState } from "react";
import { Plus, Trash2, MessageSquare, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/theme-toggle";
import type { SessionListItem } from "@/lib/api";

interface SidebarProps {
  sessions: SessionListItem[];
  activeSessionId: string | null;
  onNewChat: () => void;
  onSelectSession: (sid: string) => void;
  onDeleteSession: (sid: string) => void;
  onSignOut?: () => void;
  userEmail?: string;
}

export function Sidebar({
  sessions,
  activeSessionId,
  onNewChat,
  onSelectSession,
  onDeleteSession,
  onSignOut,
  userEmail,
}: SidebarProps) {
  const [showUserMenu, setShowUserMenu] = useState(false);
  const initial = userEmail ? userEmail[0].toUpperCase() : "?";

  return (
    <div className="w-64 h-screen border-r flex flex-col bg-muted/30 overflow-hidden">
      {/* Header */}
      <div className="p-3 border-b flex items-center justify-between">
        <h1 className="font-semibold text-sm">DataPilot</h1>
        <ThemeToggle />
      </div>

      {/* New chat button */}
      <div className="p-2">
        <Button
          variant="outline"
          className="w-full justify-start gap-2"
          onClick={onNewChat}
        >
          <Plus className="w-4 h-4" />
          New Chat
        </Button>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto">
        <div className="p-2 space-y-1">
          {sessions.map((s) => (
            <div
              key={s.session_id}
              className={`group flex items-center gap-2 px-3 py-2 rounded-md text-sm cursor-pointer transition-colors ${
                s.session_id === activeSessionId
                  ? "bg-primary/10 text-primary"
                  : "hover:bg-muted text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => onSelectSession(s.session_id)}
            >
              <MessageSquare className="w-4 h-4 flex-shrink-0" />
              <span className="flex-1 truncate">
                {s.title || "Untitled chat"}
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteSession(s.session_id);
                }}
                className="opacity-0 group-hover:opacity-100 transition-opacity"
                aria-label="Delete chat"
              >
                <Trash2 className="w-3.5 h-3.5 text-muted-foreground hover:text-destructive" />
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Footer — user avatar */}
      <div className="p-3 border-t relative">
        <div
          className="relative"
          onMouseEnter={() => setShowUserMenu(true)}
          onMouseLeave={() => setShowUserMenu(false)}
        >
          <div className="flex items-center gap-2 px-1">
            <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center flex-shrink-0">
              <span className="text-sm font-medium text-primary-foreground">{initial}</span>
            </div>
            <span className="text-xs text-muted-foreground truncate">{userEmail || "User"}</span>
          </div>

          {/* Hover dropdown */}
          {showUserMenu && (
            <div className="absolute bottom-full left-0 mb-2 rounded-md border bg-popover p-1 shadow-md min-w-[120px]">
              <button
                onClick={() => { setShowUserMenu(false); onSignOut?.(); }}
                className="flex items-center gap-2 w-full px-3 py-1.5 text-sm rounded hover:bg-muted transition-colors text-destructive"
              >
                <LogOut className="w-3.5 h-3.5" />
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
