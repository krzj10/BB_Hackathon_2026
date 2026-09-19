import { Badge } from "../../components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import type { ProposedAction, ToolResult, ApprovalReceipt } from "../../api/types.generated";
import { formatTimeInWarsaw } from "../../lib/format";

type ActionStatus = ProposedAction["status"];

function formatStatus(status: ActionStatus): { label: string; className: string } {
  switch (status) {
    case "pending":
      return { label: "Pending approval", className: "bg-primary/10 text-primary border-primary/30" };
    case "approved":
      return { label: "Approved", className: "bg-green/10 text-green-600 dark:text-green-400 border-green/30" };
    case "executing":
      return { label: "Executing…", className: "bg-amber/10 text-amber-600 dark:text-amber-400 border-amber/30 animate-pulse" };
    case "succeeded":
      return { label: "Succeeded", className: "bg-green/10 text-green-600 dark:text-green-400 border-green/30" };
    case "failed":
      return { label: "Failed", className: "bg-danger/10 text-danger border-danger/30" };
    case "unknown":
      return { label: "Unknown", className: "bg-muted text-muted-foreground border-border/60" };
    case "rejected":
      return { label: "Rejected", className: "bg-danger/10 text-danger border-danger/30" };
    case "expired":
      return { label: "Expired", className: "bg-muted text-muted-foreground border-border/60" };
    case "superseded":
      return { label: "Superseded", className: "bg-muted text-muted-foreground border-border/60" };
    default:
      return { label: status, className: "bg-muted text-muted-foreground border-border/60" };
  }
}

function formatRisk(risk: ProposedAction["risk"]): { label: string; className: string } {
  switch (risk) {
    case "high":
      return { label: "HIGH", className: "bg-danger/10 text-danger border-danger/30" };
    case "medium":
      return { label: "MEDIUM", className: "bg-primary/10 text-primary border-primary/30" };
    default:
      return { label: "LOW", className: "bg-muted text-muted-foreground border-border/60" };
  }
}

interface ActionApprovalProps {
  action: ProposedAction;
  result?: ToolResult | null;
  receipt?: ApprovalReceipt | null;
  onConfirm?: (choice: "approve" | "reject", challenge: string) => void;
  onClose?: () => void;
}

export function ActionApproval({
  action,
  result,
  receipt,
  onConfirm,
  onClose,
}: ActionApprovalProps) {
  const statusStyle = formatStatus(action.status);
  const riskStyle = formatRisk(action.risk);
  const isHighRisk = action.risk === "high";
  const isPending = action.status === "pending";
  const isExecuting = action.status === "executing";

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-6" role="dialog" aria-labelledby="action-approval-title" aria-modal="true">
      {onClose && (
        <button
          type="button"
          className="absolute right-4 top-4 rounded-full p-1 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={onClose}
          aria-label="Close approval"
        >
          ✕
        </button>
      )}

      <header className="mb-4">
        <div className="flex items-center gap-2 mb-2">
          <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-medium ${riskStyle.className}`}>
            Risk: {riskStyle.label}
          </span>
          <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-medium ${statusStyle.className}`}>
            {statusStyle.label}
          </span>
          {action.voice_approval_allowed && (
            <Badge variant="secondary">Voice allowed</Badge>
          )}
        </div>
        <h2 id="action-approval-title" className="text-[17px] font-semibold leading-snug">{action.summary}</h2>
      </header>

      <div className="space-y-4">
        <Card>
          <CardHeader className="pb-0">
            <CardTitle className="text-[13px] flex items-center gap-2">
              <span className="text-muted-foreground">📝</span>
              Reason
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-[13px] leading-relaxed">{action.reason}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-0">
            <CardTitle className="text-[13px] flex items-center gap-2">
              <span className="text-muted-foreground">📊</span>
              Impact
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-[13px] leading-relaxed">{action.impact}</p>
          </CardContent>
        </Card>

        {(action.before || action.after) && (
          <Card>
            <CardHeader className="pb-0">
              <CardTitle className="text-[13px] flex items-center gap-2">
                <span className="text-muted-foreground">🔄</span>
                Before / After
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-2">
              <div>
                <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-subtle-foreground mb-1">Before</p>
                <pre className="text-[11.5px] text-muted-foreground bg-muted p-2 rounded border border-border/60 overflow-auto max-h-32">
                  {action.before ? JSON.stringify(action.before, null, 2) : "—"}
                </pre>
              </div>
              <div>
                <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-subtle-foreground mb-1">After</p>
                <pre className="text-[11.5px] text-muted-foreground bg-muted p-2 rounded border border-border/60 overflow-auto max-h-32">
                  {action.after ? JSON.stringify(action.after, null, 2) : "—"}
                </pre>
              </div>
            </CardContent>
          </Card>
        )}

        {action.expires_at && (
          <Card>
            <CardHeader className="pb-0">
              <CardTitle className="text-[13px] flex items-center gap-2">
                <span className="text-muted-foreground">⏰</span>
                Expires
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-[13px] font-mono tabular-nums">{formatTimeInWarsaw(action.expires_at)}</p>
            </CardContent>
          </Card>
        )}

        {isHighRisk && isPending && onConfirm && (
          <Card className="border-danger/30 bg-danger-50/30 dark:bg-danger-950/10">
            <CardHeader>
              <CardTitle className="text-[13px] flex items-center gap-2">
                <span className="text-danger">⚠️</span>
                High Risk — Explicit Confirmation Required
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-[13px] text-danger">
                This action carries <strong>HIGH</strong> risk. Voice or casual confirmation is <strong>not sufficient</strong>.
                You must explicitly confirm or reject using the controls below.
              </p>
              <p className="text-[12px] text-muted-foreground">
                A one-time challenge will be required. The action expires at {action.expires_at ? formatTimeInWarsaw(action.expires_at) : "the configured time"}.
              </p>
              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  onClick={() => onConfirm("approve", "demo-one-time-challenge-0001")}
                  className="rounded-control bg-danger px-4 py-2 text-[13.5px] font-medium text-white transition-colors hover:bg-red-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500"
                >
                  Confirm (Approve)
                </button>
                <button
                  type="button"
                  onClick={() => onConfirm("reject", "demo-one-time-challenge-0001")}
                  className="rounded-control border border-border-strong px-4 py-2 text-[13.5px] font-medium text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Reject
                </button>
              </div>
            </CardContent>
          </Card>
        )}

        {!isHighRisk && isPending && (
          <Card className="border-primary/30">
            <CardHeader>
              <CardTitle className="text-[13px] flex items-center gap-2">
                <span className="text-muted-foreground">✅</span>
                Pending Approval
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <p className="text-[13px] text-muted-foreground">
                This action is awaiting approval. Voice confirmation {action.voice_approval_allowed ? "is" : "is not"} allowed.
              </p>
              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  onClick={() => onConfirm?.("approve", "demo-one-time-challenge-0001")}
                  className="rounded-control bg-primary px-4 py-2 text-[13.5px] font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  disabled={!onConfirm}
                >
                  Approve
                </button>
                <button
                  type="button"
                  onClick={() => onConfirm?.("reject", "demo-one-time-challenge-0001")}
                  className="rounded-control border border-border-strong px-4 py-2 text-[13.5px] font-medium text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  disabled={!onConfirm}
                >
                  Reject
                </button>
              </div>
            </CardContent>
          </Card>
        )}

        {isExecuting && (
          <Card className="border-amber/30 bg-amber-50/30 dark:bg-amber-950/10">
            <CardHeader>
              <CardTitle className="text-[13px] flex items-center gap-2">
                <span className="text-muted-foreground">⚙️</span>
                Executing…
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-[13px] text-amber-700 dark:text-amber-300">
                The action is being executed. Please wait for the result.
              </p>
            </CardContent>
          </Card>
        )}

        {action.status === "succeeded" && (
          <Card className="border-green/30 bg-green-50/30 dark:bg-green-950/10">
            <CardHeader>
              <CardTitle className="text-[13px] flex items-center gap-2">
                <span className="text-green-600 dark:text-green-400">✅</span>
                Succeeded
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <p className="text-[13px] text-green-700 dark:text-green-300 font-medium">Action completed successfully.</p>
              {receipt && (
                <p className="text-[12px] text-muted-foreground">
                  Approval receipt: {receipt.id} via {receipt.channel} at {formatTimeInWarsaw(receipt.approved_at)}
                </p>
              )}
              {result && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-[12.5px] text-primary">Tool result details</summary>
                  <pre className="mt-2 text-[11px] text-muted-foreground bg-muted p-2 rounded border border-border/60 overflow-auto max-h-32">
                    {JSON.stringify(result, null, 2)}
                  </pre>
                </details>
              )}
            </CardContent>
          </Card>
        )}

        {action.status === "failed" && (
          <Card className="border-danger/30 bg-danger-50/30 dark:bg-danger-950/10">
            <CardHeader>
              <CardTitle className="text-[13px] flex items-center gap-2">
                <span className="text-danger">❌</span>
                Failed
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <p className="text-[13px] text-danger font-medium">Action execution failed.</p>
              {result?.error && (
                <p className="text-[12.5px] text-muted-foreground">Error: {result.error.message} ({result.error.code})</p>
              )}
              {result && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-[12.5px] text-primary">Tool result details</summary>
                  <pre className="mt-2 text-[11px] text-muted-foreground bg-muted p-2 rounded border border-border/60 overflow-auto max-h-32">
                    {JSON.stringify(result, null, 2)}
                  </pre>
                </details>
              )}
            </CardContent>
          </Card>
        )}

        {action.status === "unknown" && (
          <Card className="border-muted bg-muted/30">
            <CardHeader>
              <CardTitle className="text-[13px] flex items-center gap-2">
                <span className="text-muted-foreground">❓</span>
                Unknown State
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-[13px] text-muted-foreground">
                The action state is <strong>unknown</strong> — it may have succeeded, failed, or be in progress.
                This is visibly distinct from success; do not assume completion.
              </p>
              {result && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-[12.5px] text-primary">Tool result details</summary>
                  <pre className="mt-2 text-[11px] text-muted-foreground bg-muted p-2 rounded border border-border/60 overflow-auto max-h-32">
                    {JSON.stringify(result, null, 2)}
                  </pre>
                </details>
              )}
            </CardContent>
          </Card>
        )}

        {action.status === "rejected" && (
          <Card className="border-danger/30 bg-danger-50/30 dark:bg-danger-950/10">
            <CardHeader>
              <CardTitle className="text-[13px] flex items-center gap-2">
                <span className="text-danger">🚫</span>
                Rejected
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-[13px] text-danger">Action was explicitly rejected.</p>
            </CardContent>
          </Card>
        )}

        {action.status === "expired" && (
          <Card className="border-muted bg-muted/30">
            <CardHeader>
              <CardTitle className="text-[13px] flex items-center gap-2">
                <span className="text-muted-foreground">⏱️</span>
                Expired
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-[13px] text-muted-foreground">The approval window expired without a decision.</p>
            </CardContent>
          </Card>
        )}

        {action.status === "superseded" && (
          <Card className="border-muted bg-muted/30">
            <CardHeader>
              <CardTitle className="text-[13px] flex items-center gap-2">
                <span className="text-muted-foreground">🔄</span>
                Superseded
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-[13px] text-muted-foreground">This action was superseded by a newer revision.</p>
            </CardContent>
          </Card>
        )}
      </div>

      <p className="mt-6 text-center text-[12px] text-subtle-foreground">
        Data from canonical fixtures: proposed_action_agenda_high.json, approval_receipt_ui.json, tool_result_agenda_ok.json
      </p>
    </div>
  );
}