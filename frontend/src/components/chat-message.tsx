"use client";

import { useState } from "react";
import { User, Bot, Copy, Check, Maximize2, X, Download } from "lucide-react";
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
  const [copied, setCopied] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [chartId] = useState(() => `chart-${Math.random().toString(36).slice(2, 10)}`);
  const chartSpec = metadata?.chart_spec;
  const isUpload = metadata?.type === "upload";

  const handleCopy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const chartLayout = {
    ...(chartSpec?.layout as any || {}),
    autosize: true,
    margin: { l: 60, r: 30, t: 50, b: 100 },
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: { color: "currentColor" },
    xaxis: {
      ...(chartSpec?.layout as any)?.xaxis,
      tickangle: -45,
      automargin: true,
    },
  };

  return (
    <>
      <div
        className={`group flex gap-3 p-4 ${
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
          {content && (
            <div className={`whitespace-pre-wrap text-sm ${isUpload ? "text-muted-foreground italic" : ""}`}>
              {content}
            </div>
          )}

          {/* Chart (inline) */}
          {chartSpec && chartSpec.data && (
            <div className="mt-3 rounded border overflow-hidden relative group/chart">
              <div className="absolute top-2 right-2 z-10 flex gap-1 opacity-0 group-hover/chart:opacity-100 transition-opacity">
                <button
                  onClick={() => {
                    const wrapper = document.getElementById(chartId);
                    const plotEl = wrapper?.querySelector(".js-plotly-plot");
                    if (plotEl) {
                      const Plotly = (window as any).Plotly;
                      if (Plotly) {
                        Plotly.downloadImage(plotEl, {
                          format: "png",
                          width: 1200,
                          height: 800,
                          filename: "datapilot-chart",
                        });
                      }
                    }
                  }}
                  className="p-1.5 rounded bg-background/80 border hover:bg-muted"
                  aria-label="Download chart"
                >
                  <Download className="w-4 h-4" />
                </button>
                <button
                  onClick={() => setFullscreen(true)}
                  className="p-1.5 rounded bg-background/80 border hover:bg-muted"
                  aria-label="View chart fullscreen"
                >
                  <Maximize2 className="w-4 h-4" />
                </button>
              </div>
              <div id={chartId}>
                <Plot
                  data={chartSpec.data as any}
                  layout={{ ...chartLayout, height: 350 }}
                  config={{ displayModeBar: false, displaylogo: false, responsive: true }}
                  style={{ width: "100%" }}
                />
              </div>
            </div>
          )}
        </div>

        {/* Copy button — appears on hover */}
        {content && !isUpload && (
          <button
            onClick={handleCopy}
            className="flex-shrink-0 mt-1 opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-muted"
            aria-label="Copy to clipboard"
          >
            {copied ? (
              <Check className="w-3.5 h-3.5 text-green-500" />
            ) : (
              <Copy className="w-3.5 h-3.5 text-muted-foreground" />
            )}
          </button>
        )}
      </div>

      {/* Fullscreen chart modal */}
      {fullscreen && chartSpec && chartSpec.data && (
        <div
          className="fixed inset-0 z-50 bg-background/95 backdrop-blur-sm flex items-center justify-center p-6"
          onClick={() => setFullscreen(false)}
        >
          <div
            className="w-full h-full max-w-6xl max-h-[85vh] relative"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="absolute top-2 right-2 z-10 flex gap-2">
              <button
                onClick={() => {
                  const wrapper = document.getElementById(`${chartId}-fs`);
                  const plotEl = wrapper?.querySelector(".js-plotly-plot");
                  if (plotEl) {
                    const Plotly = (window as any).Plotly;
                    if (Plotly) {
                      Plotly.downloadImage(plotEl, {
                        format: "png",
                        width: 1200,
                        height: 800,
                        filename: "datapilot-chart",
                      });
                    }
                  }
                }}
                className="p-2 rounded-full bg-muted hover:bg-muted/80 transition-colors"
                aria-label="Download chart"
              >
                <Download className="w-5 h-5" />
              </button>
              <button
                onClick={() => setFullscreen(false)}
                className="p-2 rounded-full bg-muted hover:bg-muted/80 transition-colors"
                aria-label="Close fullscreen"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div id={`${chartId}-fs`} style={{ width: "100%", height: "100%" }}>
              <Plot
                data={chartSpec.data as any}
                layout={{
                  ...chartLayout,
                  height: undefined,
                  margin: { l: 80, r: 40, t: 60, b: 120 },
                }}
                config={{ displayModeBar: false, displaylogo: false, responsive: true }}
                style={{ width: "100%", height: "100%" }}
                useResizeHandler
              />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
