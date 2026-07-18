"use client";

import { User, Bot } from "lucide-react";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

interface ChatMessageProps {
  role: "user" | "assistant";
  content: string;
  metadata?: {
    route?: string;
    sql?: string;
    chart_spec?: { data?: object[]; layout?: object };
    type?: string;
    [key: string]: unknown;
  };
}

export function ChatMessage({ role, content, metadata }: ChatMessageProps) {
  const chartSpec = metadata?.chart_spec;
  const isUpload = metadata?.type === "upload";

  return (
    <div
      className={`flex gap-3 p-4 ${
        role === "user" ? "bg-muted/50" : "bg-background"
      }`}
    >
      <div className="flex-shrink-0 mt-1">
        {role === "user" ? (
          <div className="w-7 h-7 rounded-full bg-primary flex items-center justify-center">
            <User className="w-4 h-4 text-primary-foreground" />
          </div>
        ) : (
          <div className="w-7 h-7 rounded-full bg-green-600 flex items-center justify-center">
            <Bot className="w-4 h-4 text-white" />
          </div>
        )}
      </div>

      <div className="flex-1 min-w-0 space-y-2">
        {/* Main content */}
        <div className={`whitespace-pre-wrap text-sm ${isUpload ? "text-muted-foreground italic" : ""}`}>
          {content}
        </div>

        {/* Chart */}
        {chartSpec && chartSpec.data && (
          <div className="mt-3 rounded border overflow-hidden">
            <Plot
              data={chartSpec.data as any}
              layout={{
                ...(chartSpec.layout as any || {}),
                autosize: true,
                height: 350,
                margin: { l: 50, r: 30, t: 40, b: 50 },
                paper_bgcolor: "transparent",
                plot_bgcolor: "transparent",
                font: { color: "currentColor" },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
