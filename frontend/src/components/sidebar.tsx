"use client";

import { useState, useRef, useEffect } from "react";
import { Plus, Trash2, MessageSquare, LogOut, MoreHorizontal, Pencil, Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/theme-toggle";
import type { SessionListItem } from "@/lib/api";

interface SidebarProps {
  sessions: SessionListItem[];
  activeSessionId: string | null;
  onNewChat: () => void;
  onSelectSession: (sid: string) => void;
  onDeleteSession: (sid: string) => void;
  onRenameSession?: (sid: string, title: string) => void;
  onSignOut?: () => void;
  userEmail?: string;
}

export function Sidebar({
  sessions,
  activeSessionId,
  onNewChat,
  onSelectSession,
  onDeleteSession,
  onRenameSession,
  onSignOut,
  userEmail,
}: SidebarProps) {
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const initial = userEmail ? userEmail[0].toUpperCase() : "?";

  // Close menu when clicking outside
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpenId(null);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const startRename = (sid: string, currentTitle: string) => {
    setEditingId(sid);
    setEditTitle(currentTitle || "");
  };

  const confirmRename = () => {
    if (editingId && editTitle.trim()) {
      onRenameSession?.(editingId, editTitle.trim());
    }
    setEditingId(null);
  };

  return (
    <div className="w-64 h-screen border-r flex flex-col bg-muted/30 overflow-hidden">
      {/* Header */}
      <div className="p-3 border-b flex items-center justify-between">
        <h1 className="font-bold text-base">DataPilot</h1>
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
              onClick={() => editingId !== s.session_id && onSelectSession(s.session_id)}
            >
              <MessageSquare className="w-4 h-4 flex-shrink-0" />

              {editingId === s.session_id ? (
                <div className="flex-1 flex items-center gap-1">
                  <input
                    type="text"
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") confirmRename();
                      if (e.key === "Escape") setEditingId(null);
                    }}
                    className="flex-1 bg-background border rounded px-1 py-0.5 text-xs outline-none"
                    autoFocus
                  />
                  <button onClick={confirmRename}>
                    <Check className="w-3 h-3 text-green-500" />
                  </button>
                  <button onClick={() => setEditingId(null)}>
                    <X className="w-3 h-3 text-muted-foreground" />
                  </button>
                </div>
              ) : (
                <>
                  <span className="flex-1 truncate">
                    {s.title || "Untitled chat"}
                  </span>
                  <div className="relative">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setMenuOpenId(menuOpenId === s.session_id ? null : s.session_id);
                      }}
                      className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded hover:bg-muted"
                      aria-label="Session options"
                    >
                      <MoreHorizontal className="w-4 h-4 text-muted-foreground" />
                    </button>

                    {menuOpenId === s.session_id && (
                      <div ref={menuRef} className="absolute right-0 top-full mt-1 z-50 rounded-md border bg-popover p-1 shadow-md min-w-[120px]">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setMenuOpenId(null);
                            startRename(s.session_id, s.title || "");
                          }}
                          className="flex items-center gap-2 w-full px-2 py-1.5 text-xs rounded hover:bg-muted transition-colors"
                        >
                          <Pencil className="w-3 h-3" />
                          Rename
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setMenuOpenId(null);
                            onDeleteSession(s.session_id);
                          }}
                          className="flex items-center gap-2 w-full px-2 py-1.5 text-xs rounded hover:bg-muted transition-colors text-destructive"
                        >
                          <Trash2 className="w-3 h-3" />
                          Delete
                        </button>
                      </div>
                    )}
                  </div>
                </>
              )}
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
