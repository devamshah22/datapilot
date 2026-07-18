"use client";

import { useState, useRef } from "react";
import { Send, Paperclip, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

interface ChatInputProps {
  onSend: (message: string) => void;
  onUpload: (files: File[]) => void;
  disabled?: boolean;
  loading?: boolean;
}

export function ChatInput({ onSend, onUpload, disabled, loading }: ChatInputProps) {
  const [input, setInput] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const handleSubmit = () => {
    const trimmed = input.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setInput("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length > 0) {
      onUpload(files);
    }
    // Reset so same file can be re-uploaded
    if (fileRef.current) fileRef.current.value = "";
  };

  return (
    <div className="border-t p-4">
      <div className="flex items-end gap-2 max-w-3xl mx-auto">
        {/* File upload */}
        <input
          ref={fileRef}
          type="file"
          multiple
          accept=".csv,.xlsx,.xls"
          className="hidden"
          onChange={handleFileChange}
        />
        <Button
          variant="ghost"
          size="icon"
          onClick={() => fileRef.current?.click()}
          disabled={disabled}
          aria-label="Upload files"
        >
          <Paperclip className="w-4 h-4" />
        </Button>

        {/* Text input */}
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask a question about your data..."
          className="min-h-[44px] max-h-[200px] resize-none"
          disabled={disabled}
          rows={1}
        />

        {/* Send button */}
        <Button
          size="icon"
          onClick={handleSubmit}
          disabled={disabled || !input.trim()}
          aria-label="Send message"
        >
          {loading ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Send className="w-4 h-4" />
          )}
        </Button>
      </div>
    </div>
  );
}
