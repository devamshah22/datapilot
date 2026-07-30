"use client";

import { useState, useRef } from "react";
import { Send, Plus, Square } from "lucide-react";

interface ChatInputProps {
  onSend: (message: string) => void;
  onUpload: (files: File[]) => void;
  onStop?: () => void;
  loading?: boolean;
}

export function ChatInput({ onSend, onUpload, onStop, loading }: ChatInputProps) {
  const [input, setInput] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSubmit = () => {
    const trimmed = input.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setInput("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
    // Shift+Enter: default behavior (newline) — trigger resize after
    if (e.key === "Enter" && e.shiftKey) {
      setTimeout(() => {
        if (textareaRef.current) {
          textareaRef.current.style.height = "auto";
          textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 200) + "px";
        }
      }, 0);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length > 0) {
      onUpload(files);
    }
    if (fileRef.current) fileRef.current.value = "";
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const el = e.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  };

  return (
    <div className="border-t p-4 bg-background">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-end gap-0 rounded-2xl border bg-muted/30 px-3 py-2 focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-1">
          {/* Upload button */}
          <input
            ref={fileRef}
            type="file"
            multiple
            accept=".csv,.xlsx,.xls"
            className="hidden"
            onChange={handleFileChange}
          />
          <button
            onClick={() => fileRef.current?.click()}
            className="flex-shrink-0 p-1.5 rounded-full hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            aria-label="Upload files"
          >
            <Plus className="w-5 h-5" />
          </button>

          {/* Text input — never disabled during generation */}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            placeholder="Ask anything"
            className="flex-1 bg-transparent border-0 outline-none resize-none text-sm px-3 py-1.5 min-h-[36px] max-h-[200px] placeholder:text-muted-foreground"
            rows={1}
          />

          {/* Send or Stop button */}
          {loading ? (
            <button
              onClick={onStop}
              className="flex-shrink-0 p-2 rounded-full bg-foreground text-background hover:opacity-80 transition-opacity"
              aria-label="Stop generation"
            >
              <Square className="w-4 h-4 fill-current" />
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={!input.trim()}
              className="flex-shrink-0 p-2 rounded-full bg-foreground text-background disabled:opacity-30 hover:opacity-80 transition-opacity"
              aria-label="Send message"
            >
              <Send className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
